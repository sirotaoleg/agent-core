# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Probe result enrichment and the evidence the probes yield."""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.harness.tools.browser_move.runtime.page_state import CARD_EVIDENCE_FIELDS
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.keys import (
    _BROWSER_PHASE_STATE_KEY,
    _BROWSER_SELECTED_SORT_LABELS,
)


class _ProbeResultsMixin:
    @classmethod
    def _enrich_probe_result_contract(
        cls,
        session: Any,
        inputs: Any,
        tool_name: str,
        tool_result: Dict[str, Any],
    ) -> None:
        if "probe_cards" not in str(tool_name or "").strip().lower():
            return
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        requested_count = int(state.get("requested_result_count") or 0)
        observed_count = int(tool_result.get("observed_count") or 0)
        diagnostics = tool_result.get("diagnostics")
        diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        diagnostics.update(
            {
                "requested_count": requested_count,
                "observed_count": observed_count,
            }
        )

        current_classes = cls._probe_card_classifications(tool_result.get("cards"))
        conflicts = cls._probe_classification_conflicts(state, current_classes)
        has_conflict = bool(conflicts or diagnostics.get("classification_conflict"))
        if has_conflict:
            diagnostics["classification_conflict"] = True
            if conflicts:
                diagnostics["classification_conflicts"] = conflicts
            fallback_count = int(state.get("probe_classification_fallback_count") or 0)
            if fallback_count < 1:
                diagnostics["recommended_fallback"] = "one_precise_probe"
                state["probe_classification_fallback_count"] = fallback_count + 1
            else:
                diagnostics["recommended_fallback"] = "retain_unknown_classification"
            diagnostics["precise_fallback_remaining"] = max(
                0,
                1 - int(state.get("probe_classification_fallback_count") or 0),
            )
        tool_result["requested_count"] = requested_count
        tool_result["observed_count"] = observed_count
        tool_result["diagnostics"] = diagnostics
        page_state = tool_result.get("page_state")
        if isinstance(page_state, dict):
            page_state["requested_count"] = requested_count
            page_state["observed_count"] = observed_count
            if has_conflict:
                page_state["classification_conflict"] = True
        state["last_card_probe"] = {
            "url": str(tool_result.get("url") or "")[:500],
            "generation_id": str(tool_result.get("generation_id") or ""),
            "classifications": current_classes,
        }
        if session is not None:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        cls._rewrite_tool_message_contract(inputs, tool_result)

    @staticmethod
    def _probe_classification_conflicts(
        state: Dict[str, Any],
        current_classes: Dict[str, str],
    ) -> list[str]:
        previous_probe = state.get("last_card_probe")
        previous_probe = previous_probe if isinstance(previous_probe, dict) else {}
        previous_classes = previous_probe.get("classifications")
        previous_classes = previous_classes if isinstance(previous_classes, dict) else {}
        return [
            identity
            for identity, classification in current_classes.items()
            if identity in previous_classes and previous_classes[identity] != classification
        ][:5]

    @staticmethod
    def _probe_card_classifications(cards: Any) -> Dict[str, str]:
        classifications: Dict[str, str] = {}
        for card in cards if isinstance(cards, list) else []:
            if not isinstance(card, dict):
                continue
            identity = " ".join(
                str(card.get("primary_link") or card.get("href") or card.get("title") or "").split()
            ).lower()[:300]
            if not identity:
                continue
            classifications[identity] = ":".join(
                (
                    str(card.get("region") or ""),
                    str(card.get("kind") or ""),
                    str(bool(card.get("is_ad"))).lower(),
                )
            )
        return classifications

    @classmethod
    def _interactive_probe_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        elements = result.get("elements")
        if not isinstance(elements, list) or not elements:
            return {}
        targets = [
            {
                "target_id": str(item.get("target_id") or ""),
                "generation_id": str(item.get("generation_id") or result.get("generation_id") or ""),
                "kind": str(item.get("kind") or ""),
                "region": str(item.get("region") or ""),
                "name": str(item.get("accessible_name") or item.get("text") or "")[:160],
                "actionable": bool(item.get("actionable")),
                "selected": bool(item.get("selected")),
                "selected_source": str(item.get("selected_source") or "")[:40],
            }
            for item in elements[:12]
            if isinstance(item, dict)
        ]
        selected_sort = next(
            (
                target
                for target in targets
                if target["selected"] and cls._is_sort_interactive_target(target)
            ),
            None,
        )
        evidence = {
            "kind": "interactive_probe",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": [],
            "target_count": len(targets),
            "targets": targets,
        }
        if selected_sort and selected_sort["name"]:
            evidence["fields"] = ["sort_state"]
            evidence["values"] = {"sort_state": selected_sort["name"]}
            evidence["provenance"] = {
                "sort_state": {
                    "source": "browser_probe_interactives",
                    "selector": selected_sort["target_id"],
                    "raw_text": selected_sort["name"],
                    "generation_id": selected_sort["generation_id"],
                    "selection_source": selected_sort["selected_source"],
                }
            }
        return evidence

    @staticmethod
    def _is_sort_interactive_target(target: Dict[str, Any]) -> bool:
        if str(target.get("kind") or "") in {"sort", "sort_tab", "sort_option"}:
            return True
        name = str(target.get("name") or "").strip().lower()
        region = str(target.get("region") or "").strip().lower()
        return bool(
            region in {"sort", "filtering", "search_controls"}
            or any(token in name for token in _BROWSER_SELECTED_SORT_LABELS)
        )

    @classmethod
    def _card_probe_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        cards = result.get("cards")
        if not isinstance(cards, list) or not cards:
            return {}
        compact_cards: list[Dict[str, Any]] = []
        fields: set[str] = set()
        for card in cards:
            compact = cls._compact_evidence_card(card, result)
            if compact is None:
                continue
            metadata_fields = {
                "generation_id",
                "result_index",
                "kind",
                "provenance",
                "field_status",
            }
            field_status = compact.get("field_status", {})
            for field_name in compact:
                if field_name in metadata_fields:
                    continue
                if field_status.get(field_name, "present") == "present":
                    fields.add(field_name)
            compact_cards.append(compact)
            if len(compact_cards) >= 5:
                break
        if not compact_cards:
            return {}
        return {
            "kind": "card_probe",
            "fields": sorted(fields),
            "cards": compact_cards,
            "observed_count": int(result.get("observed_count") or len(compact_cards)),
        }

    @classmethod
    def _compact_evidence_card(
        cls,
        card: Any,
        result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(card, dict) or not cls._is_natural_evidence_card(card):
            return None
        compact: Dict[str, Any] = {
            "generation_id": str(card.get("generation_id") or result.get("generation_id") or ""),
            "result_index": card.get("result_index"),
            "kind": card.get("kind"),
        }
        for field_name in CARD_EVIDENCE_FIELDS:
            value = card.get(field_name)
            if value in (None, "", [], {}):
                continue
            compact.setdefault(cls._canonical_field_name(field_name) or field_name, str(value)[:300])
        provenance = card.get("field_provenance")
        if isinstance(provenance, dict):
            compact["provenance"] = {
                cls._canonical_field_name(field_name) or str(field_name): dict(item)
                for field_name, item in list(provenance.items())[:20]
                if isinstance(item, dict)
            }
        statuses = card.get("field_status")
        if isinstance(statuses, dict):
            compact["field_status"] = {
                cls._canonical_field_name(field_name) or str(field_name): str(status)
                for field_name, status in list(statuses.items())[:20]
                if str(status).strip().lower() in {"present", "missing", "unknown"}
            }
        return compact

    @staticmethod
    def _is_natural_evidence_card(card: Dict[str, Any]) -> bool:
        region = str(card.get("region") or "")
        kind = str(card.get("kind") or "")
        if not (region or kind or "is_ad" in card):
            return True
        return (
            card.get("is_ad") is not True
            and region
            not in {
                "hot_search",
                "sidebar",
                "account",
                "chat",
                "activity",
                "sponsored_result",
                "resource",
                "commercial_module",
                "ai_overview",
                "question_module",
                "related",
            }
            and kind
            not in {
                "hot_search",
                "paid_column",
                "promotion",
                "activity",
                "account",
                "shop",
                "chat",
                "download_resource",
                "commercial_module",
                "ai_overview",
                "people_also_ask",
                "related_links",
                "knowledge_panel",
            }
        )
