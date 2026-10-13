# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evidence records, evaluate compaction and the recent-action log."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urlsplit

from openjiuwen.harness.tools.browser_move.contract import Observe, RunScript
from openjiuwen.harness.tools.browser_move.runtime.session import ToolKind
from openjiuwen.harness.tools.browser_move.runtime.utils.parsing import extract_json_object
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.keys import (
    _BROWSER_FIELD_ALIASES,
    _BROWSER_PHASE_STATE_KEY,
    _BROWSER_SELECTED_SORT_LABELS,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.working_context.store import BrowserWorkingContextStore

_BROWSER_CONTEXTUAL_EVALUATE_FIELD_ALIASES = frozenset(
    {
        "name",
        "profile_name",
        "active_tab",
        "active_tabs",
        "selected_tab",
        "selected_tabs",
    }
)
_BROWSER_UNKNOWN_VALUE_RE = re.compile(
    r"^(?:unknown|n/?a|not available|not found|未找到|未知|暂无|未显示|不可用)$",
    re.IGNORECASE,
)
_BROWSER_ZERO_COMMENT_RE = re.compile(
    r"^(?:0|no comments?|no replies|还没有评论|暂无评论|没有评论|暂无回复|0\s*条评论)$",
    re.IGNORECASE,
)


class _EvidenceMixin:
    @classmethod
    def _record_tool_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        previous_evidence = {
            cls._evidence_signature(item) for item in state.get("structured_evidence") or [] if isinstance(item, dict)
        }
        previous_coverage = set(state.get("field_coverage") or [])
        cls._record_structured_evidence(state, result, kind=kind, tool_name=tool_name, tool_args=tool_args)
        evidence_added = any(
            cls._evidence_signature(item) not in previous_evidence
            for item in state.get("structured_evidence") or []
            if isinstance(item, dict)
        )
        new_evidence_fields = sorted(set(state.get("field_coverage") or []) - previous_coverage)
        return {
            "new_evidence_fields": new_evidence_fields,
            "evidence_added": evidence_added,
            "recovered": bool(evidence_added or new_evidence_fields),
        }

    @classmethod
    def _record_price_interval(
        cls,
        details: Dict[str, Any],
        phase: str,
        kind: ToolKind,
        args: Dict[str, Any],
    ) -> None:
        price_interval = cls._price_interval_signature(kind, args)
        if phase != "filtering" or not price_interval:
            return
        visited_intervals = details.setdefault("visited_price_intervals", [])
        if price_interval not in visited_intervals:
            visited_intervals.append(price_interval)

    @classmethod
    def _record_structured_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> None:
        evidence = cls._structured_evidence(result)
        if not evidence and isinstance(kind.action, RunScript):
            evidence = cls._evaluate_evidence(
                result,
                tool_args,
                required_fields=state.get("required_fields") or [],
            )
        if not evidence and "probe_interactives" in str(tool_name or "").lower():
            evidence = cls._interactive_probe_evidence(result)
        stored_evidence = state.setdefault("structured_evidence", [])
        known_signatures = {cls._evidence_signature(item) for item in stored_evidence if isinstance(item, dict)}
        observation = cls._page_observation_evidence(state, result, kind, tool_name, tool_args)
        for item in (evidence, observation):
            if item and cls._evidence_signature(item) not in known_signatures:
                stored_evidence.append(item)
        del stored_evidence[:-20]
        if not evidence:
            return
        if evidence.get("kind") == "card_probe":
            state["observed_result_count"] = max(
                int(state.get("observed_result_count") or 0),
                int(evidence.get("observed_count") or 0),
            )
        cls._record_evidence_slots(
            state,
            evidence,
            result=result,
            tool_name=tool_name,
            tool_args=tool_args,
        )
        BrowserWorkingContextStore.refresh_field_coverage(state)

    @classmethod
    def _page_observation_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        kind: ToolKind,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Retain source text without inventing field coverage or another memory store."""
        if result.get("ok", True) is False:
            return {}
        if not isinstance(kind.action, (Observe, RunScript)):
            return {}
        if not cls._is_read_only_recovery(kind, tool_args):
            return {}
        value = cls._evaluate_result_value(result)
        if value in (None, "", [], {}) or isinstance(value, (bool, int, float)):
            return {}
        if isinstance(value, dict) and (value.get("ok") is False or value.get("error")):
            return {}
        raw_text = value if isinstance(value, str) else cls._evidence_signature(value)
        # AX references and selector spelling are identity metadata, not new facts.
        normalized = re.sub(r"\s*\[(?:ref|target_id|generation_id)=[^\]]+\]", "", raw_text)
        normalized = " ".join(normalized.split())
        if len(normalized) < 16:
            return {}
        _, source = cls._evidence_variant_and_url(state, result, tool_args)
        if not source:
            return {}
        page = result.get("page_state") or {}
        generation = str(result.get("generation_id") or page.get("generation_id") or "")
        target = str(tool_args.get("target") or tool_args.get("selector") or "")[:240]
        return {
            "kind": "page_observation",
            "source": source,
            "generation_id": generation,
            "raw_text": normalized[:4_000],
            "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20],
            "provenance": {
                "tool": tool_name,
                **cls._evaluate_execution_provenance(tool_args, target),
            },
        }

    @classmethod
    def _record_evidence_slots(
        cls,
        state: Dict[str, Any],
        evidence: Dict[str, Any],
        *,
        result: Dict[str, Any],
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> None:
        if not state.get("required_evidence_slots"):
            return
        variant, source_url = cls._evidence_variant_and_url(state, result, tool_args)
        values, generation, provenance, field_status = cls._evidence_values(evidence)
        if not values and not field_status:
            return
        slots = state.setdefault("evidence_slots", [])
        covered = {cls._evidence_slot_key(slot): index for index, slot in enumerate(slots) if isinstance(slot, dict)}
        for required in state.get("required_evidence_slots") or []:
            if not isinstance(required, dict):
                continue
            key = cls._evidence_slot_key(required)
            if key[1] != "default" and key[1] != variant:
                continue
            slot = cls._build_evidence_slot(
                key,
                values=values,
                provenance=provenance,
                field_status=field_status,
                generation=generation,
                source_url=source_url,
                tool_name=tool_name,
            )
            if slot is None:
                continue
            existing_index = covered.get(key)
            if existing_index is not None:
                existing = slots[existing_index]
                existing_status = str(existing.get("status") or "present").strip().lower()
                if existing_status == "present" or slot.get("status") != "present":
                    continue
                slots[existing_index] = slot
            else:
                slots.append(slot)
                covered[key] = len(slots) - 1
        del state["evidence_slots"][:-20]

    @staticmethod
    def _build_evidence_slot(
        key: tuple[str, str, str],
        *,
        values: Dict[str, Any],
        provenance: Dict[str, Any],
        field_status: Dict[str, Any],
        generation: str,
        source_url: str,
        tool_name: str,
    ) -> Optional[Dict[str, Any]]:
        value = values.get(key[2])
        status = str(field_status.get(key[2]) or ("present" if value not in (None, "") else "")).lower()
        if status not in {"present", "missing", "unknown"}:
            return None
        if status == "present" and value in (None, ""):
            return None
        item = provenance.get(key[2]) if isinstance(provenance, dict) else None
        item = item if isinstance(item, dict) else {}
        slot = {
            "entity": key[0],
            "variant": key[1],
            "field": key[2],
            "status": status,
            "source": str(item.get("source") or source_url or tool_name or "")[:500],
            "generation": str(item.get("generation_id") or generation or ""),
        }
        if status == "present":
            slot["value"] = str(value)[:500]
        for provenance_key in ("selector", "raw_text"):
            provenance_value = str(item.get(provenance_key) or "").strip()
            if provenance_value:
                slot[provenance_key] = provenance_value[:600]
        return slot

    @staticmethod
    def _evidence_values(
        evidence: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str, Dict[str, Any], Dict[str, str]]:
        values = evidence.get("values")
        if isinstance(values, dict):
            provenance = evidence.get("provenance")
            statuses = evidence.get("field_status")
            return (
                values,
                str(evidence.get("generation_id") or ""),
                provenance if isinstance(provenance, dict) else {},
                dict(statuses) if isinstance(statuses, dict) else {},
            )
        cards = evidence.get("cards")
        if not isinstance(cards, list):
            return {}, str(evidence.get("generation_id") or ""), {}, {}
        first = next((card for card in cards if isinstance(card, dict)), {})
        if not first:
            return {}, "", {}, {}
        provenance = first.get("provenance")
        statuses = first.get("field_status")
        return (
            first,
            str(first.get("generation_id") or evidence.get("generation_id") or ""),
            provenance if isinstance(provenance, dict) else {},
            dict(statuses) if isinstance(statuses, dict) else {},
        )

    @staticmethod
    def _evidence_variant_and_url(
        state: Dict[str, Any],
        result: Dict[str, Any],
        tool_args: Dict[str, Any],
    ) -> tuple[str, str]:
        page_state = result.get("page_state") if isinstance(result.get("page_state"), dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        source_url = str(
            result.get("url") or page_state.get("url") or tool_args.get("url") or last_page.get("url") or ""
        ).strip()
        query: Dict[str, list[str]] = {}
        try:
            query = parse_qs(urlsplit(source_url).query)
        except ValueError:
            pass
        order = " ".join(query.get("order", []) + query.get("sort", [])).lower()
        serialized_args = json.dumps(tool_args, ensure_ascii=False, default=str).lower()
        if any(token in order for token in ("pubdate", "latest", "newest", "recent")) or any(
            token in serialized_args for token in ("最新", "latest", "newest", "pubdate")
        ):
            return "latest", source_url
        if any(token in order for token in ("totalrank", "relevance", "default", "comprehensive")) or any(
            token in serialized_args for token in ("综合", "comprehensive", "relevance", "totalrank")
        ):
            return "comprehensive", source_url
        required_variants = {
            str(slot.get("variant") or "")
            for slot in state.get("required_evidence_slots") or []
            if isinstance(slot, dict)
        }
        if required_variants == {"comprehensive", "latest"} and source_url and not order:
            return "comprehensive", source_url
        return "default", source_url

    @classmethod
    def _evidence_signature(cls, evidence: Dict[str, Any]) -> str:
        if isinstance(evidence, dict) and evidence.get("kind") == "page_observation":
            return json.dumps([evidence.get("source"), evidence.get("content_hash")], ensure_ascii=False)
        ignored_keys = {
            "generation",
            "generation_id",
            "sel",
            "selector",
            "selector_hint",
            "ref",
            "target_id",
            "target",
            "provenance",
            "execution_provenance",
        }

        def semantic_value(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): semantic_value(item) for key, item in value.items() if str(key) not in ignored_keys}
            if isinstance(value, list):
                return [semantic_value(item) for item in value]
            return value

        return json.dumps(
            semantic_value(evidence),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _evaluate_evidence(
        cls,
        result: Dict[str, Any],
        tool_args: Dict[str, Any],
        *,
        required_fields: Iterable[str] = (),
    ) -> Dict[str, Any]:
        value = cls._evaluate_result_value(result)
        if value is None:
            return {}
        trusted_fields, allowed_fields = cls._evaluate_field_scope(tool_args, required_fields)
        compact_values, fields, field_status, raw_values = cls._compact_evaluate_values(
            value,
            allowed_fields=allowed_fields,
            trusted_fields=trusted_fields,
        )
        if not compact_values and not field_status:
            return {}
        raw_preview = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        generation_id = str(result.get("generation_id") or tool_args.get("generation_id") or "")
        target = str(tool_args.get("target") or tool_args.get("element") or "")[:240]
        return {
            "kind": "targeted_evaluate",
            "generation_id": generation_id,
            "fields": fields,
            "values": compact_values,
            "field_status": field_status,
            "preview": " ".join(raw_preview.split())[:800],
            "provenance": cls._evaluate_field_provenance(
                compact_values,
                field_status=field_status,
                raw_values=raw_values,
                target=target,
                generation_id=generation_id,
            ),
            "execution_provenance": cls._evaluate_execution_provenance(tool_args, target),
        }

    @staticmethod
    def _evaluate_result_value(result: Dict[str, Any]) -> Any:
        value = next(
            (result.get(key) for key in ("result", "value", "data") if result.get(key) is not None),
            None,
        )
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return extract_json_object(value) or value

    @classmethod
    def _evaluate_field_scope(
        cls,
        tool_args: Dict[str, Any],
        required_fields: Iterable[str],
    ) -> tuple[set[str], set[str]]:
        trusted_fields = {
            cls._canonical_field_name(field_name) for field_name in tool_args.get("_runtime_evidence_fields") or []
        }
        trusted_fields.discard("")
        allowed_fields = {cls._canonical_field_name(field_name) for field_name in required_fields}
        allowed_fields.discard("")
        allowed_fields.update(trusted_fields)
        return trusted_fields, allowed_fields

    @staticmethod
    def _evaluate_execution_provenance(tool_args: Dict[str, Any], target: str) -> Dict[str, str]:
        expression = str(
            tool_args.get("function")
            or tool_args.get("expression")
            or tool_args.get("script")
            or tool_args.get("code")
            or ""
        )
        return {
            "target": target,
            "expression_sha256": hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16] if expression else "",
        }

    @classmethod
    def _compact_evaluate_values(
        cls,
        value: Any,
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[Dict[str, str], list[str], Dict[str, str], Dict[str, str]]:
        if isinstance(value, dict):
            return cls._compact_evaluate_mapping(
                value,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            return cls._compact_evaluate_list(
                value,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
        return {}, [], {}, {}

    @classmethod
    def _compact_evaluate_mapping(
        cls,
        value: Dict[str, Any],
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[Dict[str, str], list[str], Dict[str, str], Dict[str, str]]:
        compact_values: Dict[str, str] = {}
        field_status: Dict[str, str] = {}
        raw_values: Dict[str, str] = {}
        for key, item in list(value.items())[:20]:
            normalized = cls._normalize_evaluate_mapping_item(
                key,
                item,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
            if normalized is None:
                continue
            field_name, field_value, status, raw_value = normalized
            field_status[field_name] = status
            raw_values[field_name] = raw_value
            if status == "present":
                compact_values[field_name] = field_value
        return compact_values, sorted(compact_values), field_status, raw_values

    @classmethod
    def _normalize_evaluate_mapping_item(
        cls,
        key: Any,
        item: Any,
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[str, str, str, str] | None:
        selected_sort = cls._selected_sort_value(key, item)
        normalized: tuple[str, str, str, str] | None = None
        if selected_sort:
            if not allowed_fields or "sort_state" in allowed_fields:
                normalized = ("sort_state", selected_sort, "present", f"{key}={item}"[:600])
        else:
            snake_name = cls._snake_case_field_name(key)
            canonical = cls._canonical_evaluate_field_name(key)
            field_allowed = canonical in _BROWSER_FIELD_ALIASES and (not allowed_fields or canonical in allowed_fields)
            contextual_alias_allowed = (
                snake_name not in _BROWSER_CONTEXTUAL_EVALUATE_FIELD_ALIASES or canonical in trusted_fields
            )
            if (
                field_allowed
                and contextual_alias_allowed
                and not (canonical == "sort_state" and isinstance(item, bool))
            ):
                normalized_value, status = cls._normalize_evaluate_field_value(canonical, item)
                normalized = (canonical, normalized_value, status, str(item)[:600])
        return normalized

    @staticmethod
    def _merge_first_values(target: Dict[str, str], source: Dict[str, str]) -> None:
        for field_name, field_value in source.items():
            target.setdefault(field_name, field_value)

    @classmethod
    def _compact_evaluate_list(
        cls,
        value: list[Dict[str, Any]],
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[Dict[str, str], list[str], Dict[str, str], Dict[str, str]]:
        compact_values: Dict[str, str] = {}
        field_status: Dict[str, str] = {}
        raw_values: Dict[str, str] = {}
        fields: set[str] = set()
        for item in value[:5]:
            item_values, item_fields, item_status, item_raw = cls._compact_evaluate_mapping(
                item,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
            fields.update(item_fields)
            cls._merge_first_values(compact_values, item_values)
            cls._merge_first_values(field_status, item_status)
            cls._merge_first_values(raw_values, item_raw)
        if not fields and not field_status:
            return {}, [], {}, {}
        compact_values["items"] = json.dumps(value[:3], ensure_ascii=False, default=str)[:800]
        return compact_values, sorted(fields), field_status, raw_values

    @classmethod
    def _selected_sort_value(cls, key: Any, value: Any) -> str:
        snake_name = cls._snake_case_field_name(key)
        if snake_name in {"active_tab", "active_tabs", "selected_tab", "selected_tabs"}:
            if isinstance(value, bool):
                return ""
            text = " ".join(str(value or "").split())
            return text[:120] if text else ""
        if not snake_name.endswith(("_selected", "_tab_selected")) or value is not True:
            return ""
        base_name = re.sub(r"(?:_tab)?_selected$", "", snake_name)
        for token, label in _BROWSER_SELECTED_SORT_LABELS.items():
            if token in base_name:
                return label
        return ""

    @staticmethod
    def _normalize_evaluate_field_value(field_name: str, value: Any) -> tuple[str, str]:
        if value in (None, "", [], {}):
            return "", "missing"
        text = " ".join(str(value).split())[:300]
        if field_name == "comments" and _BROWSER_ZERO_COMMENT_RE.fullmatch(text):
            return "0", "present"
        if _BROWSER_UNKNOWN_VALUE_RE.fullmatch(text):
            return "", "unknown"
        return text, "present"

    @staticmethod
    def _evaluate_field_provenance(
        values: Dict[str, str],
        *,
        field_status: Dict[str, str],
        raw_values: Dict[str, str],
        target: str,
        generation_id: str,
    ) -> Dict[str, Dict[str, str]]:
        return {
            field_name: {
                "source": "browser_evaluate",
                "selector": target,
                "raw_text": str(raw_values.get(field_name) or values.get(field_name) or "")[:600],
                "generation_id": generation_id,
            }
            for field_name in set(values) | set(field_status)
        }

    @classmethod
    def _record_recent_action(
        cls,
        session: Any,
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        action_class: str,
        elapsed_ms: int,
        progress_delta: Dict[str, Any],
    ) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return False
        state["action_sequence"] = int(state.get("action_sequence") or 0) + 1
        recent_actions = deque(state.get("recent_actions") or [], maxlen=6)
        recent_actions.append(
            cls._build_action_record(
                state,
                kind=kind,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                action_class=action_class,
                elapsed_ms=elapsed_ms,
                progress_delta=progress_delta,
            )
        )
        state["recent_actions"] = list(recent_actions)
        cls._update_last_page(state, tool_result)
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return bool(progress_delta.get("recovered"))

    @classmethod
    def _build_action_record(
        cls,
        state: Dict[str, Any],
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        action_class: str,
        elapsed_ms: int,
        progress_delta: Dict[str, Any],
    ) -> Dict[str, Any]:
        succeeded = bool(progress_delta.get("success"))
        error = str(tool_result.get("error") or "").strip() if isinstance(tool_result, dict) else ""
        semantic_delta = "evidence_added" if progress_delta.get("evidence_added") else "pending"
        outcome_status = "success" if succeeded else "failed"
        if progress_delta.get("ambiguous"):
            outcome_status = "ambiguous"
            semantic_delta = "awaiting_observation"
        elif not succeeded:
            semantic_delta = "error"
        return {
            "seq": state["action_sequence"],
            "phase": progress_delta.get("phase") or state.get("current_phase"),
            "action_class": action_class or cls._classify_action_class(kind, tool_name, tool_args, state),
            "target_summary": cls._target_summary(tool_name, tool_args),
            "outcome": "success" if succeeded else f"error: {error[:180] or 'tool_failed'}",
            "outcome_status": outcome_status,
            "semantic_delta": semantic_delta,
            "new_evidence_fields": list(progress_delta.get("new_evidence_fields") or []),
            "elapsed_ms": max(0, int(elapsed_ms)),
        }

    @staticmethod
    def _update_last_page(state: Dict[str, Any], tool_result: Any) -> None:
        result = tool_result if isinstance(tool_result, dict) else {}
        page_state = result.get("page_state") if isinstance(result.get("page_state"), dict) else {}
        url = str(result.get("url") or page_state.get("url") or "")
        title = str(result.get("title") or page_state.get("title") or "")
        if url or title:
            last_page = state.setdefault("last_page", {})
            if url:
                last_page["url"] = url
            if title:
                last_page["title"] = title

    @classmethod
    def _target_summary(cls, tool_name: str, tool_args: Any) -> str:
        args = cls._coerce_tool_args(tool_args)
        name = str(tool_name or "").strip().rsplit(".", 1)[-1]
        summary: Dict[str, Any] = {"tool": name}
        for key in ("url", "target_id", "target", "ref", "element", "name", "label", "text", "value"):
            value = args.get(key)
            if value not in (None, "", [], {}):
                summary[key] = str(value)[:160]
        steps = args.get("steps")
        if isinstance(steps, list):
            summary["ops"] = [str(step.get("op") or "") for step in steps[:12] if isinstance(step, dict)]
        expression = str(args.get("function") or args.get("expression") or args.get("script") or args.get("code") or "")
        if expression:
            summary["expression_sha256"] = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]
            summary["expression_chars"] = len(expression)
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))[:600]

    @classmethod
    def _structured_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        extracted = result.get("extracted")
        if isinstance(extracted, dict) and extracted:
            return cls._structured_extraction_evidence(result, extracted)
        condition_evidence = cls._condition_evidence(result)
        if condition_evidence:
            return condition_evidence
        return cls._card_probe_evidence(result)

    @classmethod
    def _structured_extraction_evidence(
        cls,
        result: Dict[str, Any],
        extracted: Dict[str, Any],
    ) -> Dict[str, Any]:
        provenance = result.get("field_provenance")
        compact_values: Dict[str, str] = {}
        compact_provenance: Dict[str, Any] = {}
        for key, value in list(extracted.items())[:20]:
            if value in (None, "", [], {}):
                continue
            canonical = cls._canonical_field_name(key) or str(key)
            compact_values[canonical] = str(value)[:300]
            if isinstance(provenance, dict) and isinstance(provenance.get(key), dict):
                compact_provenance[canonical] = dict(provenance[key])
        condition_values, condition_provenance = cls._successful_condition_values(result)
        for key, value in condition_values.items():
            compact_values.setdefault(key, value)
            if key in condition_provenance:
                compact_provenance.setdefault(key, condition_provenance[key])
        return {
            "kind": "structured_extraction",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": sorted(compact_values),
            "values": compact_values,
            "provenance": compact_provenance,
        }

    @classmethod
    def _condition_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        values, provenance = cls._successful_condition_values(result)
        if not values:
            return {}
        return {
            "kind": "condition_observation",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": sorted(values),
            "values": values,
            "provenance": provenance,
        }

    @staticmethod
    def _successful_condition_values(
        result: Dict[str, Any],
    ) -> tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
        field_by_op = {
            "wait_for_sort_state": "sort_state",
            "wait_for_result_count": "result_count",
            "wait_for_first_card_title": "title",
            "wait_for_url": "url",
            "wait_for_dom_text_change": "dom_text",
            "wait_for_tab": "tab_state",
        }
        values: Dict[str, str] = {}
        provenance: Dict[str, Dict[str, str]] = {}
        generation_id = str(result.get("generation_id") or "")
        conditions = result.get("conditions")
        for item in conditions if isinstance(conditions, list) else []:
            if not isinstance(item, dict) or item.get("ok") is not True:
                continue
            field_name = field_by_op.get(str(item.get("op") or "").strip().lower())
            if not field_name:
                continue
            observed = item.get("observed")
            if isinstance(observed, dict):
                value = next(
                    (
                        observed[key]
                        for key in ("value", "text", "url", "count", "tabs", "stable")
                        if observed.get(key) not in (None, "", [], {})
                    ),
                    None,
                )
            else:
                value = observed
            if value in (None, "", [], {}):
                continue
            rendered = (
                json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)
            )
            values[field_name] = rendered[:500]
            provenance[field_name] = {
                "source": "browser_batch_interact",
                "raw_text": rendered[:600],
                "generation_id": generation_id,
            }
        return values, provenance
