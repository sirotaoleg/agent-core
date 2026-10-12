# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task phases, strategy fingerprints and completion requirements."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from openjiuwen.harness.tools.browser_move.contract import (
    Click,
    Drag,
    Effect,
    Hover,
    Observe,
    RunScript,
    Scroll,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.runtime.session import ToolKind
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.keys import (
    _BATCH_MUTATING_TARGET_OPS,
    _BROWSER_FIELD_ALIASES,
    _BROWSER_PHASE_STATE_KEY,
    _BROWSER_TERMINAL_STATUSES,
    _contains_any_token,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.semantic.state import price_interval_signature
from openjiuwen.harness.tools.browser_move.surfaces.browser.site_profiles import infer_profile_evidence_entity
from openjiuwen.harness.tools.browser_move.surfaces.browser.working_context.store import BrowserWorkingContextStore

_BROWSER_REPLAN_DENIAL_LIMIT = 3
_BROWSER_READ_ONLY_RECOVERY_LIMIT = 1

_BROWSER_OUTPUT_REQUEST_CUE_RE = re.compile(
    r"(?:返回|告诉我|提取|获取|输出|给出|列出|汇报|报告|比较|对比|"
    r"return|tell\s+me|extract|retrieve|output|provide|list|report|compare)"
    r"[^。；;.!?\n]{0,240}",
    re.IGNORECASE,
)

_BATCH_SAFE_READ_SELECTOR_OPS = frozenset({"extract_text", "extract_value"})
_BATCH_EXPLICIT_SELECTOR_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
    }
)
_FORM_COMMAND_NAMES = frozenset({"TypeText", "PressKey", "SelectOption", "FillForm", "UploadFile"})
# Inferred display fields are advisory; these distinctions require typed evidence.
_BROWSER_STRICT_EVIDENCE_FIELDS = frozenset({"rating", "product_rating", "shop_rating", "sort_state"})
_BROWSER_EVALUATE_FIELD_ALIASES = {
    "article_title": "title",
    "page_title": "title",
    "product_title": "title",
    "result_title": "title",
    "author_name": "author",
    "author_raw": "author",
    "creator_name": "author",
    "profile_name": "author",
    "writer_name": "author",
    "like_count": "likes",
    "likes_count": "likes",
    "like_raw": "likes",
    "upvote_count": "likes",
    "favorite_count": "favorites",
    "favorites_count": "favorites",
    "favourite_count": "favorites",
    "bookmark_count": "favorites",
    "fav_raw": "favorites",
    "comment_count": "comments",
    "comments_count": "comments",
    "reply_count": "comments",
    "comment_raw": "comments",
    "com_raw": "comments",
    "active_tab": "sort_state",
    "active_tabs": "sort_state",
    "selected_tab": "sort_state",
    "selected_tabs": "sort_state",
    "sort_selected": "sort_state",
}
_BROWSER_PHASE_DEFINITIONS = {
    "navigation": {
        "budget": 12,
        "completion_condition": "Target URL or expected page identity is observed.",
    },
    "form": {
        "budget": 24,
        "completion_condition": "All required fields are populated and submission is evidenced.",
    },
    "filtering": {
        "budget": 20,
        "completion_condition": "Requested filter/sort state and changed results are observed.",
    },
    "extraction": {
        "budget": 20,
        "completion_condition": "One structured result contains every requested field.",
    },
}


def _has_non_empty_mapping_value(
    value: Dict[str, Any],
    keys: Iterable[str],
) -> bool:
    for key in keys:
        if value.get(key) not in (None, ""):
            return True
    return False


def _copy_non_none_values(
    value: Dict[str, Any],
    keys: Iterable[str],
) -> Dict[str, Any]:
    copied: Dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item is not None:
            copied[key] = item
    return copied


class _PhaseMixin:
    @staticmethod
    def _build_phase_state(task: str) -> Dict[str, Any]:
        normalized = str(task or "").lower()
        complex_tokens = (
            "form",
            "filter",
            "sort",
            "compare",
            "book",
            "checkout",
            "register",
            "login",
            "purchase",
            "choose",
            "select",
            "fill",
            "submit",
            "switch",
            "cart",
            "basket",
            "add to cart",
            "\u8868\u5355",
            "\u7b5b\u9009",
            "\u6392\u5e8f",
            "\u6bd4\u8f83",
            "\u5bf9\u6bd4",
            "\u9009\u62e9",
            "\u586b\u5199",
            "\u63d0\u4ea4",
            "\u5207\u6362",
            "\u8bbe\u7f6e",
            "\u65e5\u671f",
            "\u9884\u8ba2",
            "\u767b\u5f55",
            "\u6ce8\u518c",
            "\u7ed3\u8d26",
            "\u8d2d\u4e70",
            "\u8d2d\u7269\u8f66",
            "\u52a0\u8d2d",
            "\u52a0\u5165\u8d2d\u7269\u8f66",
        )
        task_type = "complex" if any(token in normalized for token in complex_tokens) else "simple"
        phase_names = tuple(_BROWSER_PHASE_DEFINITIONS) if task_type == "complex" else ("navigation", "extraction")
        phases = {
            name: {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[name]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[name]["completion_condition"],
                "blocked_signature": "",
                "visited_price_intervals": [],
            }
            for name in phase_names
        }
        required_slots = _PhaseMixin._infer_required_evidence_slots(task)
        required_fields = list(
            dict.fromkeys(
                str(slot.get("field") or "")
                for slot in required_slots
                if str(slot.get("field") or "").strip()
            )
        )
        return {
            "task_id": hashlib.sha256(str(task).encode("utf-8")).hexdigest()[:16],
            "task": task,
            "goal": task,
            "requirements_source": "inferred",
            "task_type": task_type,
            "status": "in_progress",
            "phases": phases,
            "current_phase": phase_names[0],
            "known_urls": re.findall(r"https?://[^\s<>\"]+", task)[:4],
            "constraints": [],
            "required_fields": required_fields,
            "required_evidence_slots": required_slots,
            "requested_result_count": _PhaseMixin._infer_requested_result_count(task),
            "observed_result_count": 0,
            "replan_count": 0,
            "replan_denial_count": 0,
            "resume_count": 0,
            "direct_navigation_guidance_count": 0,
            "read_only_recovery_counts": {},
            "model_protocol_retry_count": 0,
            "replan_required": False,
            "replan_trial_pending": False,
            "blocked_strategy": "",
            "failed_strategies": [],
            "trial_strategy": "",
            "last_strategy_fingerprint": "",
            "next_action_class": "navigation",
            "semantic_revision": 0,
            "structured_evidence": [],
            "evidence_slots": [],
            "field_coverage": [],
            "blockers": [],
            "recent_actions": [],
            "action_sequence": 0,
            "last_page": {},
        }

    @classmethod
    def _infer_required_fields(cls, task: str) -> list[str]:
        normalized = str(task or "").lower()
        request_scopes = _BROWSER_OUTPUT_REQUEST_CUE_RE.findall(normalized)
        output_scope = " ".join(request_scopes).strip() or normalized
        inferred = [
            field
            for field, aliases in _BROWSER_FIELD_ALIASES.items()
            if any(cls._contains_field_alias(output_scope, alias) for alias in (field, *aliases))
        ]
        if "product_rating" in inferred or "shop_rating" in inferred:
            inferred = [field_name for field_name in inferred if field_name != "rating"]
        if "product_rating" in inferred and not any(
            cls._contains_field_alias(normalized, alias) for alias in ("title", "name", "标题", "名称", "电影", "结果")
        ):
            inferred = [field_name for field_name in inferred if field_name != "title"]
        if "shop_rating" in inferred:
            without_shop_rating = re.sub(
                r"shop\s+rating|seller\s+rating|store\s+rating|店铺评分|卖家评分",
                "",
                normalized,
                flags=re.IGNORECASE,
            )
            if not any(
                cls._contains_field_alias(without_shop_rating, alias) for alias in _BROWSER_FIELD_ALIASES["shop"]
            ):
                inferred = [field_name for field_name in inferred if field_name != "shop"]
        return inferred

    @classmethod
    def _infer_required_evidence_slots(cls, task: str) -> list[Dict[str, str]]:
        """Create one provenance-bearing evidence slot per requested field."""
        normalized = str(task or "").lower()
        required_fields = cls._infer_required_fields(task)
        entity = cls._infer_evidence_entity(normalized)
        comprehensive_requested = any(
            token in normalized for token in ("comprehensive", "relevance", "综合", "默认排序")
        )
        latest_requested = any(token in normalized for token in ("latest", "newest", "最新", "最近发布"))
        split_title_variants = bool(
            "title" in required_fields and comprehensive_requested and latest_requested
        )
        slots = [
            {"entity": entity, "variant": "default", "field": field_name}
            for field_name in required_fields
            if not (field_name == "title" and split_title_variants)
        ]
        if split_title_variants:
            slots.extend(
                [
                    {"entity": entity, "variant": "comprehensive", "field": "title"},
                    {"entity": entity, "variant": "latest", "field": "title"},
                ]
            )
        return slots

    @staticmethod
    def _infer_evidence_entity(normalized_task: str) -> str:
        profile_entity = infer_profile_evidence_entity(normalized_task)
        if profile_entity:
            return profile_entity
        entities = (
            (("商品",), "product"),
            (("回答",), "article_or_answer"),
            (("article", "文章"), "article"),
            (("电影",), "movie"),
            (("weather", "天气", "气温"), "weather"),
            (("hotel", "酒店"), "hotel"),
            (("video", "视频"), "video"),
        )
        for aliases, entity in entities:
            if any(alias in normalized_task for alias in aliases):
                return entity
        return "task_result"

    @staticmethod
    def _contains_field_alias(text: str, alias: str) -> bool:
        normalized_alias = str(alias or "").strip().lower()
        if not normalized_alias:
            return False
        if any("\u4e00" <= character <= "\u9fff" for character in normalized_alias):
            return normalized_alias in text
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized_alias)}(?![a-z0-9_])", text) is not None

    _NUMBER_WORDS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }

    @classmethod
    def _infer_requested_result_count(cls, task: str) -> int:
        normalized = str(task or "").lower()
        patterns = (
            r"(?:top|first)\s*(\d{1,2})(?:\s*(?:results?|items?|articles?|links?))?",
            r"(?:前|最前)\s*(\d{1,2})\s*(?:条|个|篇|项|部|名|则)?",
            r"(\d{1,2})\s*(?:条|个|篇|项|部)\s*(?:结果|记录|文章|商品|视频|链接)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match is not None:
                return min(20, max(1, int(match.group(1))))
        number_words = "|".join(cls._NUMBER_WORDS)
        verb_match = re.search(
            rf"\b(?:find|discover|list|show|get|recommend|identify|locate)\s+(\d{{1,2}}|{number_words})\b",
            normalized,
        )
        if verb_match is not None:
            token = verb_match.group(1)
            count = int(token) if token.isdigit() else cls._NUMBER_WORDS[token]
            return min(20, max(1, count))
        return 0

    @staticmethod
    def _snake_case_field_name(value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
        text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", text)
        return text.strip("_").lower()

    @classmethod
    def _canonical_field_name(cls, value: Any) -> str:
        snake_name = cls._snake_case_field_name(value)
        normalized = " ".join(snake_name.replace("_", " ").split())
        if not normalized:
            return ""
        for field_name, aliases in _BROWSER_FIELD_ALIASES.items():
            candidates = (field_name.replace("_", " "), *aliases)
            if any(normalized == candidate.lower() for candidate in candidates):
                return field_name
        return str(value or "").strip()[:80]

    @classmethod
    def _canonical_evaluate_field_name(cls, value: Any) -> str:
        snake_name = cls._snake_case_field_name(value)
        if not snake_name:
            return ""
        explicit = _BROWSER_EVALUATE_FIELD_ALIASES.get(snake_name)
        if explicit:
            return explicit
        canonical = cls._canonical_field_name(snake_name)
        if canonical != snake_name:
            return canonical
        for suffix in ("_raw", "_text", "_value"):
            if not snake_name.endswith(suffix):
                continue
            base_name = snake_name[: -len(suffix)]
            canonical_base = cls._canonical_field_name(base_name)
            if canonical_base in _BROWSER_FIELD_ALIASES:
                return canonical_base
        return snake_name[:80]

    @classmethod
    def _field_mentioned(cls, field_name: str, text: str) -> bool:
        aliases = _BROWSER_FIELD_ALIASES.get(str(field_name), (str(field_name),))
        return any(cls._contains_field_alias(text, alias) for alias in aliases)

    def _sync_semantic_progress(self, session: Any) -> None:
        if session is None:
            return
        progress = self._runtime.semantic_progress
        if BrowserWorkingContextStore.sync_semantic_progress(session, progress):
            self._runtime.acknowledge_semantic_replan()

    @classmethod
    def _classify_tool_phase(
        cls,
        kind: ToolKind,
        tool_args: Any,
        state: Dict[str, Any],
    ) -> str:
        args = cls._coerce_tool_args(tool_args)
        serialized = json.dumps(args, ensure_ascii=False).lower()
        if kind.effect is Effect.NAVIGATE:
            return "navigation"
        filter_terms = (
            "filter",
            "sort",
            "price",
            "rating",
            "category",
            "\u7b5b\u9009",
            "\u6392\u5e8f",
            "\u4ef7\u683c",
            "\u8bc4\u5206",
            "\u5206\u7c7b",
        )
        filter_terms_present = _contains_any_token(serialized, filter_terms)
        script_tool = isinstance(kind.action, RunScript)
        filter_intent = bool(
            filter_terms_present
            and (
                not script_tool
                or cls._operation_intent(kind, args) == "script_mutation"
            )
        )
        steps = args.get("steps")
        if kind.helper and isinstance(steps, list):
            operations = {str(step.get("op") or "").lower() for step in steps if isinstance(step, dict)}
            extraction_operations = {"extract_text", "extract_value"}
            mutating_operations = _BATCH_MUTATING_TARGET_OPS | {"press", "navigate", "navigate_back"}
            if operations & extraction_operations and not operations & mutating_operations:
                return "extraction"
            if filter_intent:
                return "filtering"
            if operations & {
                "fill",
                "type",
                "autocomplete",
                "select_option",
                "select_visible_text",
                "set_checked",
            }:
                return "form"
            if operations & {"extract_text", "extract_value"}:
                return "extraction"
        if filter_intent:
            return "filtering"
        if cls._command_name(kind) in _FORM_COMMAND_NAMES:
            return "form"
        if script_tool or (kind.read_only and not kind.exempt_from_budget):
            return "extraction"
        current = str(state.get("current_phase") or "navigation")
        if kind.known and kind.effect is Effect.ACT and current == "extraction":
            return "form"
        return current if current in _BROWSER_PHASE_DEFINITIONS else "navigation"

    @classmethod
    def _phase_action_signature(
        cls,
        tool_name: str,
        tool_args: Any,
    ) -> str:
        args = cls._coerce_tool_args(tool_args)
        semantic_keys = (
            "url",
            "href",
            "name",
            "text",
            "query",
            "value",
            "values",
            "text_value",
            "label_value",
            "option_value",
            "option_label",
            "option_text",
            "choose_text",
            "checked",
            "key",
            "field",
            "action",
            "element",
        )
        actions: list[Dict[str, Any]] = []
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    compact = _copy_non_none_values(step, semantic_keys)
                    compact["op"] = str(step.get("op") or "").strip().lower()
                    actions.append(compact)
        else:
            compact = _copy_non_none_values(args, semantic_keys)
            normalized_name = str(tool_name or "").strip().lower()
            operation = normalized_name.rsplit("browser_", 1)[-1]
            compact["op"] = operation
            actions.append(compact)
        return json.dumps(
            actions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )[:600]

    @classmethod
    def _strategy_fingerprint(
        cls,
        state: Dict[str, Any],
        kind: ToolKind,
        tool_args: Any,
        action_class: str,
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Identify a material strategy without selector or generation noise."""

        args = cls._coerce_tool_args(tool_args)
        fields = cls._requested_strategy_fields(state, args)
        intent = cls._operation_intent(kind, args)
        descriptor = {
            "action_class": action_class,
            "page": cls._normalize_navigation_url(cls._current_page_url(state, current_page_state)),
            "target": cls._strategy_target(args, current_page_state),
            "fields": fields,
            "intent": intent,
        }
        serialized = json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        field_label = ",".join(fields[:4]) or "none"
        return (f"{action_class}:{intent}:{field_label}:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]}")[
            :240
        ]

    @staticmethod
    def _current_page_url(
        state: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]],
    ) -> str:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        return str(page_state.get("url") or last_page.get("url") or "")

    @classmethod
    def _strategy_target(
        cls,
        args: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        url = str(args.get("url") or args.get("href") or "").strip()
        if url:
            return cls._normalize_navigation_url(url)
        target_id = str(args.get("target_id") or "").strip()
        if target_id:
            return cls._target_semantic_identity(target_id, current_page_state)
        for key in ("name", "label", "placeholder", "text", "element"):
            value = " ".join(str(args.get(key) or "").split()).lower()
            if value:
                return f"semantic:{value[:120]}"
        steps = args.get("steps")
        if isinstance(steps, list):
            labels = []
            for step in steps[:12]:
                if not isinstance(step, dict):
                    continue
                step_target_id = str(step.get("target_id") or "").strip()
                if step_target_id:
                    labels.append(cls._target_semantic_identity(step_target_id, current_page_state))
                    continue
                label = next(
                    (
                        " ".join(str(step.get(key) or "").split()).lower()
                        for key in ("name", "label", "placeholder", "text", "element")
                        if str(step.get(key) or "").strip()
                    ),
                    "",
                )
                labels.append(label[:80] or "page_target")
            if labels:
                return "batch:" + "|".join(labels)
        return (
            "page_target" if _has_non_empty_mapping_value(args, ("target_id", "target", "ref", "selector")) else "page"
        )

    @staticmethod
    def _target_semantic_identity(
        target_id: str,
        current_page_state: Optional[Dict[str, Any]],
    ) -> str:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}

        def find_target(value: Any) -> Optional[Dict[str, Any]]:
            if isinstance(value, dict):
                if str(value.get("target_id") or "") == target_id:
                    return value
                for nested in value.values():
                    match = find_target(nested)
                    if match is not None:
                        return match
            elif isinstance(value, list):
                for nested in value:
                    match = find_target(nested)
                    if match is not None:
                        return match
            return None

        target = find_target(page_state)
        if target is None:
            return "registered_target"
        identity = {
            key: " ".join(str(target.get(key) or "").split()).lower()[:160]
            for key in ("href", "field", "role", "kind", "region", "text")
            if str(target.get(key) or "").strip()
        }
        return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:400]

    @classmethod
    def _requested_strategy_fields(cls, state: Dict[str, Any], args: Dict[str, Any]) -> list[str]:
        fields: set[str] = set()
        for value in (args.get("field"), args.get("fields")):
            values = value if isinstance(value, list) else [value]
            for item in values:
                canonical = cls._canonical_field_name(item)
                if canonical:
                    fields.add(canonical)
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                canonical = cls._canonical_field_name(step.get("field"))
                if canonical:
                    fields.add(canonical)
        serialized = json.dumps(args, ensure_ascii=False, default=str).lower()
        for field_name in state.get("required_fields") or []:
            if cls._field_mentioned(str(field_name), serialized):
                fields.add(str(field_name))
        return sorted(fields)

    @classmethod
    def _operation_intent(cls, kind: ToolKind, args: Dict[str, Any]) -> str:
        steps = args.get("steps")
        if isinstance(steps, list):
            operations = sorted(
                {
                    str(step.get("op") or "").strip().lower()
                    for step in steps
                    if isinstance(step, dict) and str(step.get("op") or "").strip()
                }
            )
            return "+".join(operations)[:120] or "batch"
        if isinstance(kind.action, RunScript):
            expression = str(
                args.get("function") or args.get("expression") or args.get("script") or args.get("code") or ""
            ).lower()
            if re.search(r"\.click\s*\(|dispatchEvent|\.value\s*=|setAttribute\s*\(", expression):
                return "script_mutation"
            if re.search(r"textContent|innerText|return|querySelector|document\.title", expression):
                return "script_extraction"
            return "script_inspection"
        return cls._command_name(kind).lower() or "other"

    @staticmethod
    def _price_interval_signature(kind: ToolKind, tool_args: Any) -> str:
        """Delegate cross-site filter normalization to semantic-state logic; a script carries no filter."""
        if isinstance(kind.action, RunScript):
            return ""
        return price_interval_signature("", tool_args)

    @classmethod
    def _classify_action_class(cls, kind: ToolKind, tool_name: str, tool_args: Any, state: Dict[str, Any]) -> str:
        name = str(tool_name or "").strip().lower()
        args = cls._coerce_tool_args(tool_args)
        serialized = json.dumps(args, ensure_ascii=False).lower()
        if kind.effect is Effect.NAVIGATE:
            return "navigation"
        if isinstance(kind.action, Scroll):
            return "viewport_exploration"
        if isinstance(kind.action, RunScript):
            return "script_exploration"
        if "probe_cards" in name:
            return "structured_extraction"
        if isinstance(kind.action, Observe) or (kind.helper and kind.read_only and not kind.exempt_from_budget):
            return "target_discovery"
        phase = cls._classify_tool_phase(kind, tool_args, state)
        if phase == "extraction":
            return "structured_extraction"
        if phase in {"form", "filtering"}:
            return phase
        if isinstance(kind.action, (Click, Hover, Drag)) or cls._command_name(kind) == "Drop":
            return "interaction"
        if any(token in serialized for token in ("filter", "sort", "price", "rating", "筛选", "排序", "价格")):
            return "filtering"
        return "other"

    @classmethod
    def _consume_phase_budget(
        cls,
        session: Any,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        *,
        current_page_state: Optional[Dict[str, Any]] = None,
        replan_group_admitted: bool = False,
    ) -> str:
        if session is None:
            return "other"
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return "other"
        cls._reject_terminal_state(state)
        if kind.exempt_from_budget:
            return cls._classify_action_class(kind, tool_name, tool_args, state)
        phases = state.setdefault("phases", {})
        phase = cls._classify_tool_phase(kind, tool_args, state)
        details = cls._phase_details(phases, phase)
        signature = cls._phase_action_signature(tool_name, tool_args)
        action_class = cls._classify_action_class(kind, tool_name, tool_args, state)
        strategy_fingerprint = cls._strategy_fingerprint(
            state,
            kind,
            tool_args,
            action_class,
            current_page_state,
        )

        attempts = int(details.get("attempts") or 0)
        budget = max(1, int(details.get("budget") or 1))
        if attempts >= budget:
            details["status"] = "replan_required"
            details["blocked_signature"] = signature
            state["current_phase"] = phase
            state["status"] = "partial"
            state["blockers"] = [f"{phase}_phase_budget_exhausted"]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                f"Browser {phase} phase budget exhausted ({attempts}/{budget}). "
                "Finish with available evidence or return partial/blocked."
            )

        cls._reject_revisited_price_interval(details, phase, kind, tool_args)

        if cls._direct_navigation_is_required(state, phases, phase, current_page_state):
            state["direct_navigation_guidance_count"] = int(state.get("direct_navigation_guidance_count") or 0) + 1
            recommended_url = str((state.get("known_urls") or [""])[0])
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                "The task already contains a known URL. Navigate to it directly before selector exploration: "
                f"{recommended_url}"
            )

        try:
            if not replan_group_admitted:
                cls._consume_replan_gate(
                    state,
                    kind,
                    tool_args,
                    action_class,
                    strategy_fingerprint,
                )
        except ValueError:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise

        details["attempts"] = attempts + (0 if cls._repeated_script(details, phase, kind) else 1)
        details["status"] = "in_progress"
        details["last_signature"] = signature
        details["last_semantic_signature"] = signature
        state["current_phase"] = phase
        state["last_action_class"] = action_class
        state["last_strategy_fingerprint"] = strategy_fingerprint
        state["next_action_class"] = ""
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return action_class

    @staticmethod
    def _repeated_script(details: Dict[str, Any], phase: str, kind: ToolKind) -> bool:
        """A page script read in this phase before; each distinct script costs one attempt."""
        if phase != "extraction" or not isinstance(kind.action, RunScript):
            return False
        digest = hashlib.sha256(kind.action.code.encode("utf-8")).hexdigest()[:16]
        scripts = details.setdefault("scripts", [])
        if digest in scripts:
            return True
        scripts.append(digest)
        return False

    @classmethod
    def _direct_navigation_is_required(
        cls,
        state: Dict[str, Any],
        phases: Dict[str, Any],
        phase: str,
        current_page_state: Optional[Dict[str, Any]],
    ) -> bool:
        if (
            not state.get("known_urls")
            or phase == "navigation"
            or int(state.get("direct_navigation_guidance_count") or 0) >= 1
        ):
            return False
        total_attempts = sum(int(item.get("attempts") or 0) for item in phases.values() if isinstance(item, dict))
        return total_attempts == 0 and not cls._known_url_navigation_satisfied(
            state,
            current_page_state,
        )

    @classmethod
    def _known_url_navigation_satisfied(
        cls,
        state: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]],
    ) -> bool:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        current_url = str(page_state.get("url") or last_page.get("url") or "").strip()
        if not current_url:
            return False
        current_normalized = cls._normalize_navigation_url(current_url)
        current_document = cls._normalize_navigation_url(current_url, include_query=False)
        try:
            current_parts = urlsplit(current_url)
        except ValueError:
            current_parts = None
        for known_url in state.get("known_urls") or []:
            known_normalized = cls._normalize_navigation_url(known_url)
            if known_normalized and known_normalized == current_normalized:
                return True
            known_document = cls._normalize_navigation_url(known_url, include_query=False)
            if known_document and known_document == current_document:
                return True
            try:
                known_parts = urlsplit(str(known_url))
            except ValueError:
                known_parts = None
            if current_parts is None or known_parts is None:
                continue
            if current_parts.netloc.lower() != known_parts.netloc.lower():
                continue
            known_path = (known_parts.path or "/").rstrip("/") or "/"
            current_path = (current_parts.path or "/").rstrip("/") or "/"
            if known_path == "/" and (current_path != "/" or bool(current_parts.query)):
                return True
            if known_path != "/" and current_path.startswith(f"{known_path}/"):
                return True
        return False

    @staticmethod
    def _normalize_navigation_url(value: Any, *, include_query: bool = True) -> str:
        raw = str(value or "").strip().rstrip(".,;，。；)")
        if not raw:
            return ""
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw.lower()
        if not parsed.netloc:
            return raw.lower().rstrip("/")
        path = unquote(parsed.path or "/").rstrip("/") or "/"
        query = ""
        if include_query:
            query_items = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
            ]
            query = urlencode(sorted(query_items))
        normalized = f"{parsed.netloc.lower()}{path}"
        return f"{normalized}?{query}" if query else normalized

    @staticmethod
    def _reject_terminal_state(state: Dict[str, Any]) -> None:
        status = str(state.get("status") or "in_progress").strip().lower()
        if status in _BROWSER_TERMINAL_STATUSES:
            raise ValueError(
                f"Browser task is already {status}; do not issue another browser action. "
                "Finish with the runtime evidence and blockers."
            )

    @staticmethod
    def _phase_details(phases: Dict[str, Any], phase: str) -> Dict[str, Any]:
        return phases.setdefault(
            phase,
            {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[phase]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[phase]["completion_condition"],
                "blocked_signature": "",
                "visited_price_intervals": [],
            },
        )

    @classmethod
    def _consume_replan_gate(
        cls,
        state: Dict[str, Any],
        kind: ToolKind,
        tool_args: Any,
        action_class: str,
        strategy_fingerprint: str = "",
    ) -> None:
        cls._reject_terminal_state(state)
        if not state.get("replan_required"):
            return
        if kind.exempt_from_budget:
            return
        if state.get("replan_trial_pending"):
            cls._consume_replan_denial(state, "replan_trial_pending")
            raise ValueError(
                "A replan trial already ran without verified semantic progress. "
                "Wait for the runtime observation, or finish partial/blocked instead of trying "
                "another selector, generation, tool, or phase."
            )
        replan_count = int(state.get("replan_count") or 0)
        budget_s = float(state.get("deadline_budget_s") or 0.0)
        remaining_s = float(state.get("deadline_remaining_s") or 0.0)
        replan_limit = 4 if budget_s and remaining_s / budget_s > 0.5 else 2
        if replan_count >= replan_limit:
            state["status"] = "partial" if cls._has_task_evidence(state) else "blocked"
            state["blockers"] = ["semantic_replan_budget_exhausted"]
            state["terminal_reason"] = "semantic_replan_budget_exhausted"
            state["next_action_class"] = "finish"
            raise ValueError(
                f"Semantic progress remained blocked after {replan_limit} replan trials. "
                "Return blocked or partial with the available structured evidence."
            )
        blocked_strategy = str(
            state.get("blocked_strategy")
            or state.get("last_strategy_fingerprint")
            or state.get("last_action_class")
            or ""
        )
        failed_strategies = {str(item) for item in state.get("failed_strategies") or [] if str(item).strip()}
        if blocked_strategy:
            failed_strategies.add(blocked_strategy)
        current_strategy = strategy_fingerprint or action_class
        if current_strategy in failed_strategies or action_class in failed_strategies:
            if cls._allow_read_only_recovery(state, kind, tool_args, current_strategy):
                state["replan_count"] = replan_count + 1
                state["replan_trial_pending"] = True
                state["trial_strategy"] = current_strategy
                state["status"] = "replan_trial"
                return
            cls._consume_replan_denial(state, "repeated_strategy")
            raise ValueError(
                "Semantic loop detected. Changing selector, generation, tool name, or phase does not "
                f"change the {action_class} target/field/intent strategy. Re-plan materially or finish "
                "partial/blocked."
            )
        state["replan_count"] = replan_count + 1
        state["replan_trial_pending"] = True
        state["trial_strategy"] = current_strategy
        state["status"] = "replan_trial"

    @classmethod
    def _allow_read_only_recovery(
        cls,
        state: Dict[str, Any],
        kind: ToolKind,
        tool_args: Any,
        strategy_fingerprint: str,
    ) -> bool:
        if not cls._is_read_only_recovery(kind, tool_args):
            return False
        counts = state.setdefault("read_only_recovery_counts", {})
        count = int(counts.get(strategy_fingerprint) or 0)
        if count >= _BROWSER_READ_ONLY_RECOVERY_LIMIT:
            return False
        counts[strategy_fingerprint] = count + 1
        return True

    @classmethod
    def _is_read_only_recovery(cls, kind: ToolKind, tool_args: Any) -> bool:
        args = cls._coerce_tool_args(tool_args)
        if kind.read_only:
            return True
        # Switching tabs changes nothing on any page, so it may recover a stalled plan like a read.
        if isinstance(kind.action, SwitchContext):
            return True
        if isinstance(kind.action, RunScript):
            return cls._operation_intent(kind, args) != "script_mutation"
        steps = args.get("steps")
        if not kind.helper or not isinstance(steps, list) or not steps:
            return False
        read_only_ops = _BATCH_SAFE_READ_SELECTOR_OPS | _BATCH_EXPLICIT_SELECTOR_OPS | {
            "wait_for_text",
            "wait_for_load_state",
            "wait_for_url",
            "wait_for_tab",
        }
        return all(
            isinstance(step, dict) and str(step.get("op") or "").strip().lower() in read_only_ops
            for step in steps
        )

    @staticmethod
    def _consume_replan_denial(state: Dict[str, Any], reason: str) -> None:
        denial_count = int(state.get("replan_denial_count") or 0) + 1
        state["replan_denial_count"] = denial_count
        state["last_denial_reason"] = str(reason or "")[:120]
        if denial_count < _BROWSER_REPLAN_DENIAL_LIMIT:
            return
        blockers = list(state.get("blockers") or [])
        if "semantic_replan_denial_budget_exhausted" not in blockers:
            blockers.append("semantic_replan_denial_budget_exhausted")
        state["blockers"] = blockers[:10]
        state["status"] = "blocked"
        state["next_action_class"] = "finish"
        state["terminal_reason"] = "semantic_replan_denial_budget_exhausted"

    @classmethod
    def _reject_revisited_price_interval(
        cls,
        details: Dict[str, Any],
        phase: str,
        kind: ToolKind,
        tool_args: Any,
    ) -> None:
        price_interval = cls._price_interval_signature(kind, tool_args)
        visited_intervals = details.setdefault("visited_price_intervals", [])
        if phase == "filtering" and price_interval and price_interval in visited_intervals:
            raise ValueError(
                f"Price interval {price_interval} was already visited in this task. "
                "Use the existing evidence or choose a different interval."
            )

    @classmethod
    @classmethod
    def _record_phase_result(
        cls,
        session: Any,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        *,
        progressed: bool = False,
    ) -> Dict[str, Any]:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return {}
        result = tool_result if isinstance(tool_result, dict) else {"result": tool_result}
        succeeded = result.get("ok", True) is not False
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return {
                "phase": state.get("current_phase") or "unknown",
                "success": succeeded,
                "new_evidence_fields": [],
                "evidence_added": False,
                "recovered": False,
            }
        phase = cls._classify_tool_phase(kind, tool_args, state)
        phases = state.get("phases")
        if not isinstance(phases, dict):
            return {}
        details = phases.get(phase)
        if not isinstance(details, dict):
            return {}
        args = cls._coerce_tool_args(tool_args)
        if not succeeded:
            evidence_delta = cls._record_tool_evidence(
                state,
                result,
                kind=kind,
                tool_name=tool_name,
                tool_args=args,
            )
            completion_evidence = cls._phase_completion_evidence(phase, kind, result, state)
            cls._record_failed_phase_result(state, details, tool_result)
            missing_fields = cls._missing_completion_requirements(state) if phase == "extraction" else []
            if completion_evidence and not missing_fields:
                cls._complete_phase(state, phases, phase, details, completion_evidence)
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return {"phase": phase, "success": False, **evidence_delta}

        details["successes"] = int(details.get("successes") or 0) + 1
        details["last_error"] = ""
        # An action that changed the context or the observation was work, not a retry: only stalled actions count.
        if progressed and phase != "extraction":
            details["attempts"] = max(0, int(details.get("attempts") or 0) - 1)
        cls._record_price_interval(details, phase, kind, args)
        evidence_delta = cls._record_tool_evidence(state, result, kind=kind, tool_name=tool_name, tool_args=args)
        if evidence_delta.get("recovered"):
            BrowserWorkingContextStore.mark_replan_recovered(state)
        completion_evidence = cls._phase_completion_evidence(phase, kind, result, state)
        missing_fields = cls._missing_completion_requirements(state) if phase == "extraction" else []
        if completion_evidence and not missing_fields:
            cls._complete_phase(state, phases, phase, details, completion_evidence)
        elif details.get("status") != "replan_required":
            details["status"] = "in_progress"
            details["missing_fields"] = missing_fields
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return {"phase": phase, "success": True, **evidence_delta}

    @staticmethod
    def _missing_required_fields(state: Dict[str, Any]) -> list[str]:
        required = {str(item) for item in state.get("required_fields") or [] if str(item).strip()}
        coverage = {str(item) for item in state.get("field_coverage") or [] if str(item).strip()}
        return sorted(required - coverage)

    @staticmethod
    def _has_task_evidence(state: Dict[str, Any]) -> bool:
        """Use evidence records, not compatibility coverage, as task truth."""

        return bool(state.get("evidence_slots") or state.get("structured_evidence"))

    @staticmethod
    def _task_observations(state: Dict[str, Any]) -> list[Dict[str, Any]]:
        return [
            item for item in state.get("structured_evidence") or []
            if isinstance(item, dict) and item.get("kind") == "page_observation"
        ]

    @classmethod
    def _advisory_missing_fields(cls, state: Dict[str, Any]) -> set[str]:
        """Do not mistake an incomplete field adapter for an unsuccessful page read."""
        if state.get("requirements_source") != "inferred" or not cls._task_observations(state):
            return set()
        slots = state.get("required_evidence_slots") or []
        # Comparison variants and typed rating/sort evidence remain strict.
        if any(isinstance(slot, dict) and slot.get("variant", "default") != "default" for slot in slots):
            return set()
        return set(cls._missing_required_fields(state)) - _BROWSER_STRICT_EVIDENCE_FIELDS

    @classmethod
    def _missing_completion_requirements(cls, state: Dict[str, Any]) -> list[str]:
        missing = cls._missing_required_fields(state)
        missing_field_names = set(missing)
        missing_slots = cls._missing_evidence_slots(state)
        missing.extend(
            f"evidence_slot:{slot['entity']}:{slot['variant']}:{slot['field']}"
            for slot in missing_slots
            if not (slot["variant"] == "default" and slot["field"] in missing_field_names)
        )
        requested_count = int(state.get("requested_result_count") or 0)
        observed_count = int(state.get("observed_result_count") or 0)
        if requested_count and observed_count < requested_count:
            missing.append(f"result_count:{observed_count}/{requested_count}")
        return missing

    @classmethod
    def _missing_evidence_slots(cls, state: Dict[str, Any]) -> list[Dict[str, str]]:
        required_slots = set()
        for slot in state.get("required_evidence_slots") or []:
            if isinstance(slot, dict):
                required_slots.add(cls._evidence_slot_key(slot))

        covered_slots = set()
        for slot in state.get("evidence_slots") or []:
            if not isinstance(slot, dict):
                continue
            status = str(slot.get("status") or "").strip().lower()
            if slot.get("value") not in (None, "") or status in {"missing", "unknown"}:
                covered_slots.add(cls._evidence_slot_key(slot))
        return [
            {"entity": entity, "variant": variant, "field": field_name}
            for entity, variant, field_name in sorted(required_slots - covered_slots)
        ]

    @classmethod
    def _unavailable_evidence_slots(cls, state: Dict[str, Any]) -> list[Dict[str, str]]:
        required_slots = set()
        for slot in state.get("required_evidence_slots") or []:
            if isinstance(slot, dict):
                required_slots.add(cls._evidence_slot_key(slot))

        unavailable = []
        for slot in state.get("evidence_slots") or []:
            if not isinstance(slot, dict):
                continue
            key = cls._evidence_slot_key(slot)
            status = str(slot.get("status") or "").strip().lower()
            if key not in required_slots or status not in {"missing", "unknown"}:
                continue
            unavailable.append(
                {
                    "entity": key[0],
                    "variant": key[1],
                    "field": key[2],
                    "status": status or "unknown",
                }
            )
        return unavailable

    @staticmethod
    def _evidence_slot_key(slot: Dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(slot.get("entity") or "").strip().lower(),
            str(slot.get("variant") or "").strip().lower(),
            str(slot.get("field") or "").strip().lower(),
        )

    @staticmethod
    def _record_failed_phase_result(state: Dict[str, Any], details: Dict[str, Any], tool_result: Any) -> None:
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return
        details["status"] = "pending"
        if isinstance(tool_result, dict):
            details["last_error"] = str(tool_result.get("error") or "")[:300]
        if not state.get("replan_trial_pending"):
            return
        trial_strategy = str(state.get("trial_strategy") or "")
        state["replan_trial_pending"] = False
        state["replan_required"] = True
        state["blocked_strategy"] = trial_strategy
        BrowserWorkingContextStore.record_failed_strategy(state, trial_strategy)
        state["status"] = "replan_required"

    @staticmethod
    def _phase_completion_evidence(
        phase: str,
        kind: ToolKind,
        result: Dict[str, Any],
        state: Dict[str, Any],
    ) -> str:
        successful_condition_operations = {
            str(condition.get("op") or "").strip().lower()
            for condition in (result.get("conditions") if isinstance(result.get("conditions"), list) else [])
            if isinstance(condition, dict) and condition.get("ok") is True
        }
        if phase == "navigation":
            result_url = str(result.get("url") or "")
            if result_url or kind.effect is Effect.NAVIGATE:
                return result_url or "navigation tool succeeded"
        if phase == "form" and successful_condition_operations & {
            "wait_for_url",
            "wait_for_selector",
            "wait_for_text",
            "wait_for_first_card_title",
            "wait_for_result_count",
            "wait_for_dom_text_change",
            "wait_for_stable",
            "wait_for_tab",
        }:
            return "form batch completed with an observable condition"
        if phase == "filtering" and successful_condition_operations & {
            "wait_for_sort_state",
            "wait_for_result_count",
            "wait_for_dom_text_change",
            "wait_for_first_card_title",
        }:
            return "requested state and changed results were observed"
        if phase == "extraction":
            extracted = result.get("extracted")
            cards = result.get("cards")
            if isinstance(extracted, dict) and extracted:
                return f"structured extraction returned {len(extracted)} field(s)"
            if isinstance(cards, list) and cards:
                # A card probe reads a results list, not the entity the task is about. A task
                # asking for one specific item (requested_result_count == 0) needs a real
                # `extracted` result from opening it; _missing_completion_requirements already
                # gates list tasks (requested_result_count >= 1) on having enough cards.
                if int(state.get("requested_result_count") or 0) < 1:
                    return ""
                excluded_regions = {"hot_search", "sidebar", "account", "chat"}
                excluded_kinds = {
                    "hot_search",
                    "paid_column",
                    "promotion",
                    "activity",
                    "account",
                    "shop",
                    "chat",
                }
                natural_cards = []
                for card in cards:
                    if not isinstance(card, dict) or card.get("is_ad") is True:
                        continue
                    if str(card.get("region") or "") in excluded_regions:
                        continue
                    if str(card.get("kind") or "") in excluded_kinds:
                        continue
                    natural_cards.append(card)
                if natural_cards:
                    return f"card probe returned {len(natural_cards)} structured result(s)"
        return ""

    @staticmethod
    def _complete_phase(
        state: Dict[str, Any],
        phases: Dict[str, Any],
        phase: str,
        details: Dict[str, Any],
        completion_evidence: str,
    ) -> None:
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return
        details["status"] = "completed"
        details["completion_evidence"] = completion_evidence[:300]
        phase_order = list(phases)
        try:
            remaining_phases = phase_order[phase_order.index(phase) + 1:]
        except ValueError:
            remaining_phases = []
        next_phase = next(
            (
                candidate
                for candidate in remaining_phases
                if isinstance(phases.get(candidate), dict) and phases[candidate].get("status") != "completed"
            ),
            phase,
        )
        state["current_phase"] = next_phase
        if all(isinstance(item, dict) and item.get("status") == "completed" for item in phases.values()):
            state["status"] = "completed"
            state["next_action_class"] = "finish"
        else:
            state["next_action_class"] = next_phase
