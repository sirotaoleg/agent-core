# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The browser's page state: URL and title, probe and card targets, evidence fields and blockers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import Observation, SurfaceContext
from openjiuwen.harness.tools.browser_move.runtime.page_state import PageState, Target, compact_text

_AX_REF_LINE_RE = re.compile(
    r"^\s*-\s+(?P<role>[A-Za-z][\w-]*)"
    r'(?:\s+"(?P<name>[^"]*)")?.*?\[ref=(?P<ref>[A-Za-z0-9_.:-]+)\]',
    re.MULTILINE,
)
_ANY_REF_RE = re.compile(r"\bref\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_BLOCKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("captcha", re.compile(r"captcha|验证码|人机验证", re.IGNORECASE)),
    (
        "login_required",
        re.compile(
            r"login required|sign[ -]?in required|please (?:log|sign) in|请先?登录|需要登录",
            re.IGNORECASE,
        ),
    ),
    ("access_denied", re.compile(r"access denied|forbidden|访问被拒绝", re.IGNORECASE)),
)
_CARD_SEMANTIC_FIELD_NAMES = (
    "region",
    "kind",
    "result_index",
    "is_ad",
)
CARD_EVIDENCE_FIELDS = (
    "title",
    "price",
    "rating",
    "product_rating",
    "shop_rating",
    "review_count",
    "availability",
    "author",
    "likes",
    "favorites",
    "comments",
    "shop",
    "duration",
    "high_temperature",
    "low_temperature",
    "sort_state",
    "source",
    "summary",
    "primary_link",
    "href",
)
_READ_ONLY_CARD_TARGET_FIELDS = frozenset(
    {
        "rating",
        "product_rating",
        "shop_rating",
        "author",
        "likes",
        "favorites",
        "comments",
        "shop",
        "duration",
        "high_temperature",
        "low_temperature",
        "sort_state",
    }
)
_DEFAULT_CARD_FIELDS = frozenset({"title", "price", "rating", "source", "summary", "primary_link", "href"})
_MAX_PUBLIC_INTERACTIVES = 20
_MAX_PUBLIC_CARDS = 8


@dataclass
class BrowserTarget(Target):
    """One model-visible target bound to a single state generation."""

    href: str = ""
    region: str = ""
    kind: str = ""
    extractable: bool = False
    field_name: str = ""
    selected: bool = False
    selected_source: str = ""

    def compact_index(self) -> dict[str, Any]:
        """Return the small PageState index entry shown to the model."""
        result: dict[str, Any] = {
            "target_id": self.target_id,
            "generation_id": self.generation_id,
        }
        if self.href:
            result["href"] = self.href
        if self.role:
            result["role"] = self.role
        if self.text:
            result["text"] = compact_text(self.text, 80)
        if self.region:
            result["region"] = self.region
        if self.kind:
            result["kind"] = self.kind
        if self.field_name:
            result["field"] = self.field_name
        result["match_count"] = 1
        result["visible"] = self.visible
        result["enabled"] = self.enabled
        result["actionable"] = self.actionable
        if self.clickable:
            result["clickable"] = True
        if self.extractable:
            result["extractable"] = True
            result["read_only"] = True
        if self.selected:
            result["selected"] = True
            if self.selected_source:
                result["selected_source"] = self.selected_source
        return result


class BrowserPageState(PageState):
    """Own identity, compact representation, and target generations."""

    target_type = BrowserTarget
    id_prefix = "page"
    observe_again = "Probe or snapshot the current page again."
    snapshot_again = "Capture the current page snapshot again."

    def __init__(
        self,
        *,
        page_id: str | None = None,
        generation: int = 0,
        element_change_threshold: float = 0.5,
    ) -> None:
        super().__init__(page_id=page_id, generation=generation, element_change_threshold=element_change_threshold)
        self.title = ""
        self.field_coverage: set[str] = set()
        self.requested_fields: set[str] = set()
        self.blockers: set[str] = set()
        self.selector_primary_links: dict[str, tuple[int, str]] = {}
        self._cards: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        return self.identity

    @url.setter
    def url(self, value: str) -> None:
        self.identity = value

    def advance(self, *, url: str = "", title: str = "") -> None:
        """Start a new document generation while retaining stale-target history."""
        super().advance()
        self.url = str(url or "").strip()
        self.title = compact_text(title, 300)

    def observe(self, *, url: Any = "", title: Any = "") -> None:
        """Update non-empty document metadata without advancing generation."""
        normalized_url = str(url or "").strip()
        normalized_title = compact_text(title, 300)
        if normalized_url:
            self.url = normalized_url
        if normalized_title:
            self.title = normalized_title

    def register_interactives(self, payload: dict[str, Any]) -> None:
        """Attach safe target IDs to a Probe result and refresh its compact index."""
        self.observe(url=payload.get("url"), title=payload.get("title"))
        elements = payload.get("elements")
        if not isinstance(elements, list):
            elements = []
        self._interactive_target_ids = []
        for item in elements:
            if not isinstance(item, dict):
                continue
            target = self._register_probe_target(item, source="interactive")
            if target is not None:
                item["target_id"] = target.target_id
                item["generation_id"] = self.generation_id
                self._interactive_target_ids.append(target.target_id)
        self._update_blockers(payload, elements)

    def register_cards(self, payload: dict[str, Any]) -> None:
        """Attach target IDs and build compact card records for PageState."""
        self.observe(url=payload.get("url"), title=payload.get("title"))
        cards = payload.get("cards")
        if not isinstance(cards, list):
            cards = []
        self._cards = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            compact_card: dict[str, Any] = {"generation_id": self.generation_id}
            target = self._register_probe_target(card, source="card")
            if target is not None:
                card["target_id"] = target.target_id
                card["generation_id"] = self.generation_id
                compact_card["target_id"] = target.target_id

            primary_link_target = self._register_primary_link_target(card)
            if primary_link_target is not None:
                card["primary_link_target_id"] = primary_link_target.target_id
                compact_card["primary_link_target_id"] = primary_link_target.target_id

            field_targets = self._register_card_field_targets(card)
            if field_targets:
                card["field_targets"] = field_targets
                compact_card["field_targets"] = field_targets

            compact_controls: list[dict[str, Any]] = []
            for child_key in ("buttons", "controls", "actions"):
                children = card.get(child_key)
                if not isinstance(children, list):
                    continue
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_target = self._register_probe_target(
                        child,
                        source=f"card_{child_key}",
                    )
                    if child_target is not None:
                        child["target_id"] = child_target.target_id
                        child["generation_id"] = self.generation_id
                        compact_controls.append(child_target.compact_index())

            compact_card.update(self._compact_card_fields(card))
            if compact_card.get("primary_link") and not compact_card.get("href"):
                compact_card["href"] = compact_card["primary_link"]
            if compact_controls:
                compact_card["controls"] = compact_controls
            if len(compact_card) > 1:
                self._cards.append(compact_card)
        self._update_blockers(payload, cards)

    def _compact_card_fields(self, card: Mapping[str, Any]) -> dict[str, Any]:
        statuses_value = card.get("field_status")
        statuses = statuses_value if isinstance(statuses_value, Mapping) else {}
        visible_fields = _DEFAULT_CARD_FIELDS | self.requested_fields
        compact: dict[str, Any] = {}
        for field_name in _CARD_SEMANTIC_FIELD_NAMES + CARD_EVIDENCE_FIELDS:
            if field_name in CARD_EVIDENCE_FIELDS and field_name not in visible_fields:
                continue
            field_value = card.get(field_name)
            if field_value in (None, "", [], {}):
                continue
            if field_name in CARD_EVIDENCE_FIELDS and statuses.get(field_name, "present") == "present":
                self.field_coverage.add(field_name)
            compact[field_name] = (
                field_value if isinstance(field_value, (bool, int, float)) else compact_text(field_value)
            )
        missing_fields = sorted(
            field_name
            for field_name in CARD_EVIDENCE_FIELDS
            if field_name in visible_fields and statuses.get(field_name) in {"missing", "unknown"}
        )
        compact_statuses = {
            field_name: str(statuses[field_name])
            for field_name in CARD_EVIDENCE_FIELDS
            if field_name in visible_fields and statuses.get(field_name) in {"present", "missing", "unknown"}
        }
        if compact_statuses:
            compact["field_status"] = compact_statuses
        provenance_value = card.get("field_provenance")
        if isinstance(provenance_value, Mapping):
            compact_provenance: dict[str, dict[str, Any]] = {}
            for field_name in CARD_EVIDENCE_FIELDS:
                if field_name not in visible_fields:
                    continue
                item = provenance_value.get(field_name)
                if not isinstance(item, Mapping):
                    continue
                projected = {
                    key: (compact_text(value, 240) if isinstance(value, str) else value)
                    for key, value in item.items()
                    if key in {"selector", "raw_text", "generation_id", "source"} and value not in (None, "", [], {})
                }
                if projected:
                    compact_provenance[field_name] = projected
            if compact_provenance:
                compact["field_provenance"] = compact_provenance
        if missing_fields:
            compact["missing_fields"] = missing_fields
        return compact

    def register_ax_snapshot(self, value: Any) -> tuple[str, ...]:
        """Register native Playwright refs without translating them in the model."""
        text = value if isinstance(value, str) else str(value)
        registered: list[str] = []
        for match in _AX_REF_LINE_RE.finditer(text):
            ref_value = match.group("ref")
            self._register_ax_ref(
                ref_value,
                role=str(match.group("role") or "").strip(),
                name=str(match.group("name") or "").strip(),
            )
            registered.append(ref_value)

        for ref_value in _ANY_REF_RE.findall(text):
            if self.reference_generations.get(ref_value) == self.generation:
                continue
            self.reference_generations[ref_value] = self.generation
            target = self._new_target(
                source="ax",
                locator={"ref": ref_value},
                ref=ref_value,
            )
            self._ref_targets[ref_value] = target.target_id
            self._interactive_target_ids.append(target.target_id)
            registered.append(ref_value)

        self._update_blockers({}, [text[:4000]])
        return tuple(registered)

    def replace_ax_snapshot(self, value: Any) -> tuple[str, ...]:
        """Replace current-generation AX refs with refs from one complete snapshot."""
        text = value if isinstance(value, str) else str(value)
        snapshot_refs = set(_ANY_REF_RE.findall(text))
        missing_refs = [
            ref_value
            for ref_value, generation in self.reference_generations.items()
            if generation == self.generation and ref_value not in snapshot_refs
        ]
        for ref_value in missing_refs:
            self._remove_current_ax_ref(ref_value)
        return self.register_ax_snapshot(text)

    def add_field_coverage(self, fields: Iterable[str]) -> None:
        """Record structured fields extracted from the current document."""
        for field_name in fields:
            normalized = str(field_name or "").strip()
            if normalized:
                self.field_coverage.add(normalized)

    def set_requested_fields(self, fields: Iterable[str]) -> None:
        """Set task-scoped fields used to keep model-facing cards compact."""

        self.requested_fields = {
            str(field_name or "").strip() for field_name in fields if str(field_name or "").strip()
        }

    def refresh_target_id(self, target_id: str) -> str:
        """Map a stale probe target to its one equivalent current target by runtime-owned identity."""

        normalized_target_id = str(target_id or "").strip()
        target = self._targets.get(normalized_target_id)
        if target is None:
            raise ValueError(f"Unknown PageState target_id: {normalized_target_id}")
        if target.generation == self.generation:
            return target.target_id
        if target.source == "ax":
            raise ValueError(
                f"Stale AX target_id {target.target_id} belongs to {target.generation_id}; "
                "capture the current page snapshot again."
            )

        current_targets = [
            candidate
            for candidate in self._targets.values()
            if candidate.generation == self.generation and candidate.source == target.source
        ]
        candidates = self._equivalent_current_targets(target, current_targets)
        if len(candidates) != 1:
            reason = "not found" if not candidates else f"ambiguous ({len(candidates)} matches)"
            raise ValueError(
                f"Stale target_id {target.target_id} cannot be safely refreshed in "
                f"{self.generation_id}: {reason}. Probe the current page again."
            )
        return candidates[0].target_id

    def rebind_target(
        self,
        target_id: str,
        *,
        visible: bool,
        enabled: bool,
        actionable: bool,
    ) -> str:
        """Rebind a stale runtime-owned locator after unique DOM validation."""
        stale = self.get_target(target_id)
        if stale is None:
            raise ValueError(f"Unknown PageState target_id: {target_id}")
        if stale.generation == self.generation:
            return stale.target_id
        if stale.source == "ax" or not (stale.selector or stale.href):
            raise ValueError(f"Stale target_id {stale.target_id} has no refreshable runtime identity.")
        target = self._new_target(
            source=stale.source,
            locator=dict(stale.locator),
            selector=stale.selector,
            href=stale.href,
            role=stale.role,
            name=stale.name,
            text=stale.text,
            region=stale.region,
            kind=stale.kind,
            visible=bool(visible),
            enabled=bool(enabled),
            actionable=bool(actionable),
            clickable=bool(stale.clickable and actionable),
            extractable=stale.extractable,
            field_name=stale.field_name,
        )
        if target.selector:
            self.selector_generations[target.selector] = self.generation
            self._selector_targets[(self.generation, target.selector)] = target.target_id
        return target.target_id

    def recovery_candidates(self, target_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return compact current-generation candidates for a stale target error."""
        stale = self.get_target(target_id)
        if stale is None:
            return []
        current_targets = [target for target in self._targets.values() if target.generation == self.generation]
        return [
            candidate.compact_index()
            for candidate in self._equivalent_current_targets(stale, current_targets)[: max(1, int(limit))]
        ]

    @staticmethod
    def _equivalent_current_targets(
        stale: BrowserTarget,
        current_targets: list[BrowserTarget],
    ) -> list[BrowserTarget]:
        if stale.selector:
            selector_matches = [target for target in current_targets if target.selector == stale.selector]
            if selector_matches:
                return selector_matches
        if stale.href:
            href_matches = [target for target in current_targets if target.href == stale.href]
            if href_matches:
                return href_matches

        stale_name = compact_text(stale.name or stale.text, 160).lower()
        if not stale_name or not (stale.kind or stale.role):
            return []
        matches: list[BrowserTarget] = []
        for target in current_targets:
            target_name = compact_text(target.name or target.text, 160).lower()
            if target_name != stale_name:
                continue
            if stale.kind and target.kind != stale.kind:
                continue
            if stale.role and target.role != stale.role:
                continue
            if stale.region and target.region != stale.region:
                continue
            matches.append(target)
        return matches

    def update_target_locator(self, target_id: str, locator: Mapping[str, str]) -> None:
        """Replace a current target's internal locator after runtime resolution."""
        target = self._targets.get(str(target_id or "").strip())
        if target is None:
            raise ValueError(f"Unknown PageState target_id: {target_id}")
        self._validate_target_generation(target)
        target.locator = {str(key): str(value) for key, value in locator.items() if str(value or "").strip()}
        selector = str(target.locator.get("selector") or "").strip()
        if selector:
            target.selector = selector
            self.selector_generations[selector] = self.generation
            self._selector_targets[(self.generation, selector)] = target.target_id

    def export(self) -> dict[str, Any]:
        """Return a bounded PageState index suitable for every result."""
        interactives = self._export_targets(
            self._interactive_target_ids,
            _MAX_PUBLIC_INTERACTIVES,
        )
        return {
            "page_id": self.page_id,
            "generation_id": self.generation_id,
            "url": self.url,
            "title": self.title,
            "interactives": interactives,
            "cards": self._cards[:_MAX_PUBLIC_CARDS],
            "field_coverage": sorted(self.field_coverage),
            "blockers": sorted(self.blockers),
        }

    def export_summary(self) -> dict[str, Any]:
        """Return PageState identity without duplicating cards or interactives."""

        return {
            "page_id": self.page_id,
            "generation_id": self.generation_id,
            "url": self.url,
            "title": self.title,
            "field_coverage": sorted(self.field_coverage),
            "blockers": sorted(self.blockers),
        }

    def _note_surface(self, surface: SurfaceContext) -> None:
        self.observe(title=getattr(surface, "title", ""))
        self.contexts = tuple(getattr(surface, "tabs", ())) or self.contexts

    def _on_advance(self) -> None:
        self.title = ""
        self.field_coverage.clear()
        self.blockers.clear()
        self._cards.clear()

    def _on_observation(self, observation: Observation) -> None:
        names = (element.name for element in observation.elements)
        self._update_blockers({}, [observation.context.title or "", *names])

    def _forget_target(self, target: Target) -> None:
        primary_link = self.selector_primary_links.get(target.selector) if target.selector else None
        if primary_link and primary_link[0] == target.generation:
            self.selector_primary_links.pop(target.selector, None)

    def _register_probe_target(
        self,
        item: dict[str, Any],
        *,
        source: str,
    ) -> BrowserTarget | None:
        selector = str(item.get("selector_hint") or "").strip()
        selector_validated = bool(item.get("selector_hint_validated"))
        match_count = item.get("match_count")
        if selector and (not selector_validated or match_count != 1):
            selector = ""

        locator: dict[str, str] = {"selector": selector} if selector else {}

        href = str(item.get("primary_link") or item.get("href") or "").strip()
        if not locator and not href:
            return None

        if selector:
            existing_id = self._selector_targets.get((self.generation, selector))
            if existing_id and existing_id in self._targets:
                existing = self._targets[existing_id]
                existing.visible = bool(item.get("visible", True))
                existing.enabled = bool(item.get("enabled", not item.get("disabled", False)))
                existing.actionable = bool(item.get("actionable", False))
                existing.clickable = bool(item.get("clickable", False))
                existing.selected = bool(item.get("selected", False))
                existing.selected_source = str(item.get("selected_source") or "")[:40]
                return existing

        target = self._new_target(
            source=source,
            locator=locator,
            selector=selector,
            href=href,
            role=str(item.get("role") or "").strip(),
            name=str(item.get("accessible_name") or item.get("name") or "").strip(),
            text=str(item.get("text") or item.get("title") or "").strip(),
            region=str(item.get("region") or "").strip(),
            kind=str(item.get("kind") or "").strip(),
            visible=bool(item.get("visible", True)),
            enabled=bool(item.get("enabled", not item.get("disabled", False))),
            actionable=bool(item.get("actionable", False)),
            clickable=bool(item.get("clickable", False)),
            selected=bool(item.get("selected", False)),
            selected_source=str(item.get("selected_source") or "")[:40],
        )
        if selector:
            self.selector_generations[selector] = self.generation
            self._selector_targets[(self.generation, selector)] = target.target_id
        return target

    def _register_primary_link_target(
        self,
        card: dict[str, Any],
    ) -> BrowserTarget | None:
        href = str(card.get("primary_link") or card.get("href") or "").strip()
        selector = str(card.get("primary_link_selector_hint") or "").strip()
        match_count = card.get("primary_link_match_count")
        if not href:
            return None
        selector_validated = bool(card.get("primary_link_selector_hint_validated"))
        if selector and (not selector_validated or match_count != 1):
            selector = ""
        target = self._register_probe_target(
            {
                "selector_hint": selector,
                "selector_hint_validated": bool(selector),
                "match_count": 1 if selector else None,
                "primary_link": href,
                "role": "link",
                "text": card.get("title") or "",
                "region": card.get("region") or "",
                "kind": card.get("kind") or "",
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
            },
            source="card_primary_link",
        )
        if selector:
            self.selector_primary_links[selector] = (self.generation, href)
        return target

    def _register_card_field_targets(self, card: dict[str, Any]) -> dict[str, dict[str, Any]]:
        statuses = card.get("field_status")
        field_status = statuses if isinstance(statuses, Mapping) else {}
        provenance = card.get("field_provenance")
        field_provenance = provenance if isinstance(provenance, Mapping) else {}
        selector_metadata = card.get("selector_metadata")
        selector_metadata = selector_metadata if isinstance(selector_metadata, Mapping) else {}
        registered: dict[str, dict[str, Any]] = {}
        for field_name in _READ_ONLY_CARD_TARGET_FIELDS:
            if field_name not in self.requested_fields:
                continue
            if field_status.get(field_name) != "present":
                continue
            provenance_entry = field_provenance.get(field_name)
            provenance_entry = provenance_entry if isinstance(provenance_entry, Mapping) else {}
            selector = str(
                provenance_entry.get("selector")
                or card.get(f"{field_name}_selector_hint")
                or (card.get("rating_selector_hint") if field_name in {"product_rating", "shop_rating"} else "")
                or ""
            ).strip()
            metadata_entry = selector_metadata.get(field_name)
            if not isinstance(metadata_entry, Mapping) and field_name in {"product_rating", "shop_rating"}:
                metadata_entry = selector_metadata.get("rating")
            metadata_entry = metadata_entry if isinstance(metadata_entry, Mapping) else {}
            if not selector or metadata_entry.get("match_count") != 1 or metadata_entry.get("visible") is not True:
                continue
            target = self._new_target(
                source="card_field",
                locator={"selector": selector},
                selector=selector,
                role="text",
                text=str(card.get(field_name) or provenance_entry.get("raw_text") or ""),
                region=str(card.get("region") or ""),
                kind="detail_field",
                visible=True,
                enabled=True,
                actionable=False,
                clickable=False,
                extractable=True,
                field_name=field_name,
            )
            self.selector_generations[selector] = self.generation
            self._selector_targets[(self.generation, selector)] = target.target_id
            registered[field_name] = target.compact_index()
        return registered

    def _update_blockers(self, payload: Mapping[str, Any], values: Iterable[Any]) -> None:
        chunks = [
            str(payload.get("error") or ""),
            str(payload.get("title") or ""),
            str(payload.get("url") or ""),
        ]
        for value in values:
            if isinstance(value, Mapping):
                chunks.extend(str(value.get(key) or "") for key in ("text", "title", "accessible_name", "aria_label"))
            else:
                chunks.append(str(value or ""))
            if sum(len(chunk) for chunk in chunks) >= 6000:
                break
        searchable = " ".join(chunks)[:6000]
        for blocker, pattern in _BLOCKER_PATTERNS:
            if pattern.search(searchable):
                self.blockers.add(blocker)


__all__ = ["CARD_EVIDENCE_FIELDS", "BrowserPageState", "BrowserTarget"]
