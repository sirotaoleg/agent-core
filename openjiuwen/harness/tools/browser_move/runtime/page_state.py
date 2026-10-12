#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generations of the surface state and the generation-scoped target registry."""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..contract import (
    Action,
    ActionResult,
    ContextRef,
    DriverRef,
    Locator,
    Observation,
    Observe,
    Ref,
    StaleRef,
    SurfaceContext,
    iter_refs,
)

_MAX_PUBLIC_INTERACTIVES = 20
_MAX_TARGET_HISTORY = 1024


def compact_text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _generation_number(generation_id: str) -> int:
    normalized = str(generation_id or "").strip().lower()
    if not re.fullmatch(r"g\d+", normalized):
        raise ValueError("generation_id must use the PageState generation format, for example g3")
    return int(normalized[1:])


def _element_change(before: Observation, after: Observation) -> float:
    """Jaccard distance between the ``(ref, role)`` sets of two observations."""
    old = {(element.ref.ref, element.role) for element in before.elements}
    new = {(element.ref.ref, element.role) for element in after.elements}
    union = old | new
    return 1 - len(old & new) / len(union) if union else 0.0


def _stale_message(ref: Ref, current: str) -> str:
    if isinstance(ref, DriverRef):
        return f"Stale AX ref {ref.ref} belongs to {ref.generation}; current generation is {current}."
    if isinstance(ref, Locator) and ref.css:
        return f"Stale selector belongs to {ref.generation}; current generation is {current}: {ref.css}"
    return f"Stale {type(ref).__name__} belongs to {ref.generation}; current generation is {current}."


@dataclass
class Target:
    """One model-visible target bound to a single state generation."""

    target_id: str
    page_id: str
    generation: int
    source: str
    locator: dict[str, str] = field(default_factory=dict)
    ref: str = ""
    selector: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    visible: bool = True
    enabled: bool = True
    actionable: bool = False
    clickable: bool = False

    @property
    def generation_id(self) -> str:
        return f"g{self.generation}"

    def compact_index(self) -> dict[str, Any]:
        """The small index entry shown to the model."""
        result: dict[str, Any] = {"target_id": self.target_id, "generation_id": self.generation_id}
        if self.role:
            result["role"] = self.role
        if self.text:
            result["text"] = compact_text(self.text, 80)
        result["match_count"] = 1
        result["visible"] = self.visible
        result["enabled"] = self.enabled
        result["actionable"] = self.actionable
        if self.clickable:
            result["clickable"] = True
        return result


class PageState:
    """Generations, the last observation and the targets minted from it; a surface subclasses it for its own state."""

    target_type: type[Target] = Target
    id_prefix = "state"
    observe_again = "Observe again."
    snapshot_again = "Observe again."

    def __init__(
        self,
        *,
        page_id: str | None = None,
        generation: int = 0,
        element_change_threshold: float = 0.5,
    ) -> None:
        self.page_id = page_id or f"{self.id_prefix}-{uuid.uuid4().hex[:12]}"
        self.generation = max(0, int(generation))
        self.element_change_threshold = element_change_threshold
        self.identity = ""
        self.last_observation: Observation | None = None
        self.screenshot_generation = ""
        self._dispatched_since_observation = False
        self.surface_context: SurfaceContext | None = None
        self.contexts: tuple[ContextRef, ...] = ()
        self.current_context: ContextRef | None = None
        self.free_observations = 0
        self.reference_generations: dict[str, int] = {}
        self.selector_generations: dict[str, int] = {}
        self._targets: dict[str, Target] = {}
        self._ref_targets: dict[str, str] = {}
        self._selector_targets: dict[tuple[int, str], str] = {}
        self._interactive_target_ids: list[str] = []
        self._target_counter = 0

    @property
    def generation_id(self) -> str:
        return f"g{self.generation}"

    @property
    def fresh_observation(self) -> Observation | None:
        """The last observation while it is current: of this generation, with no command dispatched after it."""
        observation = self.last_observation
        if observation is None or observation.generation != self.generation_id or self._dispatched_since_observation:
            return None
        return observation

    def note_dispatched(self) -> None:
        """A command reached the surface but failed, so there is no result to record."""
        self._dispatched_since_observation = True

    def advance(self) -> None:
        """Start a new generation while retaining stale-target history."""
        self.generation += 1
        self.identity = ""
        self._interactive_target_ids.clear()
        self._on_advance()
        self._trim_target_history()

    def validate_generation(self, generation_id: str) -> None:
        """Reject callers that are not using the current generation."""
        requested = _generation_number(generation_id)
        if requested != self.generation:
            raise ValueError(
                f"Stale PageState generation {generation_id}; current generation is "
                f"{self.generation_id}. {self.observe_again}"
            )

    def record(self, action: Action, outcome: ActionResult | Observation) -> ActionResult | Observation:
        """Advance the generation when ``outcome`` shows a new context or document; return it stamped."""
        if isinstance(outcome, Observation) and isinstance(action, Observe) and action.scoped:
            return self._register_observation(outcome, complete=False)
        result = outcome if isinstance(outcome, ActionResult) else ActionResult(ok=True, observation=outcome)
        observation = result.observation
        if observation is not None and not isinstance(action, Observe):
            self.free_observations += 1
        surface = (observation.surface if observation is not None else None) or result.surface
        identity = self._identity_of(surface) if surface is not None else ""
        moved = bool(identity) and self.identity not in ("", identity)
        if moved or self._changes_context(result):
            self.advance()
            self._forget_contexts()
        if identity:
            self.identity = identity
        if surface is not None:
            self.surface_context = surface
            self._note_surface(surface)
        if observation is not None:
            observation = self._register_observation(observation)
            self.current_context = observation.context
            self.last_observation = observation
            if observation.screenshot is not None:
                self.screenshot_generation = self.generation_id
        self._dispatched_since_observation = observation is None
        if isinstance(outcome, Observation):
            return observation
        return dataclasses.replace(result, generation=self.generation_id, observation=observation)

    def check(self, action: Action) -> None:
        """Raise ``StaleRef(dispatched=False)`` if any ref in ``action`` belongs to an older generation."""
        stale = [
            _stale_message(ref, self.generation_id)
            for ref in iter_refs(action)
            if ref.generation and _generation_number(ref.generation) < self.generation
        ]
        if stale:
            raise StaleRef(" ".join(stale), dispatched=False)

    def generation_of(self, ref: str) -> str:
        """The generation ``ref`` was last registered in; the current one for a ref never seen."""
        generation = self.reference_generations.get(ref)
        return self.generation_id if generation is None else f"g{generation}"

    def resolve(self, model_id: str, generation: str) -> Ref:
        """The contract ref behind a ``target_id``; ``ValueError`` when it is stale, unknown or has no locator."""
        target = self.resolve_target(generation_id=generation, target_id=model_id)
        if target.ref:
            return DriverRef(target.ref, target.generation_id)
        if target.selector:
            return Locator(css=target.selector, text=None, generation=target.generation_id)
        raise ValueError(f"PageState target_id {target.target_id} has no element locator")

    def reset(self) -> None:
        """Start a new generation with no contexts and no last observation, as after a driver restart."""
        self.advance()
        self._forget_contexts()

    def resolve_target(
        self,
        *,
        generation_id: str,
        target_id: str = "",
        ref: str = "",
        selector: str = "",
        require_registered_selector: bool = True,
    ) -> Target | None:
        """Resolve one target and reject every stale generation before execution."""
        self.validate_generation(generation_id)
        normalized_target_id = str(target_id or "").strip()
        normalized_ref = str(ref or "").strip()
        normalized_selector = str(selector or "").strip()

        if normalized_target_id:
            target = self._targets.get(normalized_target_id)
            if target is None:
                raise ValueError(f"Unknown PageState target_id: {normalized_target_id}")
            self._validate_target_generation(target)
            return target

        if normalized_ref:
            ref_generation = self.reference_generations.get(normalized_ref)
            if ref_generation is None:
                raise ValueError(f"Unknown AX ref: {normalized_ref}. {self.snapshot_again}")
            if ref_generation != self.generation:
                raise ValueError(
                    f"Stale AX ref {normalized_ref} belongs to g{ref_generation}; "
                    f"current generation is {self.generation_id}."
                )
            resolved_id = self._ref_targets.get(normalized_ref)
            target = self._targets.get(resolved_id or "")
            if target is None:
                raise ValueError(f"AX ref is not registered as a target: {normalized_ref}")
            self._validate_target_generation(target)
            return target

        if normalized_selector:
            selector_generation = self.selector_generations.get(normalized_selector)
            if selector_generation is not None and selector_generation != self.generation:
                raise ValueError(
                    f"Stale selector belongs to g{selector_generation}; current generation is "
                    f"{self.generation_id}: {normalized_selector}"
                )
            resolved_id = self._selector_targets.get((self.generation, normalized_selector))
            target = self._targets.get(resolved_id or "")
            if target is not None:
                return target
            if require_registered_selector:
                raise ValueError(
                    "Unregistered action selector. Use target_id/ref from the current "
                    f"PageState instead of constructing CSS: {normalized_selector}"
                )
            self.selector_generations[normalized_selector] = self.generation
        return None

    def get_target(self, target_id: str) -> Target | None:
        """An internal target, stale history included, for recovery."""
        return self._targets.get(str(target_id or "").strip())

    def export(self) -> dict[str, Any]:
        """A bounded index suitable for every result."""
        return {
            "page_id": self.page_id,
            "generation_id": self.generation_id,
            "interactives": self._export_targets(self._interactive_target_ids, _MAX_PUBLIC_INTERACTIVES),
        }

    def _identity_of(self, surface: SurfaceContext) -> str:
        return ""

    def _note_surface(self, surface: SurfaceContext) -> None:
        """A surface subclass reads its own context type here."""

    def _on_advance(self) -> None:
        """A surface subclass clears its per-generation state here."""

    def _on_observation(self, observation: Observation) -> None:
        """A surface subclass reads a complete or partial observation here."""

    def _forget_target(self, target: Target) -> None:
        """A surface subclass drops what it keeps for a target trimmed from the history."""

    def _changes_context(self, result: ActionResult) -> bool:
        if result.context_changed:
            return True
        observation = result.observation
        if observation is None:
            return False
        if self.current_context is not None and observation.context.id != self.current_context.id:
            return True
        return (
            self.last_observation is not None
            and _element_change(self.last_observation, observation) > self.element_change_threshold
        )

    def _forget_contexts(self) -> None:
        # Context ids may be positions, valid only until the next context change.
        self.last_observation = None
        self.surface_context = None
        self.contexts = ()
        self.current_context = None

    def _register_observation(self, observation: Observation, *, complete: bool = True) -> Observation:
        # A partial observation (a scoped snapshot) adds the refs it shows; only a complete one drops the others.
        elements = tuple(
            dataclasses.replace(element, ref=DriverRef(element.ref.ref, self.generation_id))
            for element in observation.elements
        )
        refs = {element.ref.ref for element in elements}
        missing_refs = [
            ref_value
            for ref_value, generation in self.reference_generations.items()
            if complete and generation == self.generation and ref_value not in refs
        ]
        for ref_value in missing_refs:
            self._remove_current_ax_ref(ref_value)
        for element in elements:
            self._register_ax_ref(element.ref.ref, role=element.role, name=element.name)
        stamped = dataclasses.replace(observation, generation=self.generation_id, elements=elements)
        self._on_observation(stamped)
        return stamped

    def _register_ax_ref(self, ref_value: str, *, role: str, name: str) -> None:
        self.reference_generations[ref_value] = self.generation
        existing_id = self._ref_targets.get(ref_value)
        existing_target = self._targets.get(existing_id or "")
        if existing_target is not None and existing_target.generation == self.generation:
            target = existing_target
        else:
            target = self._new_target(
                source="ax",
                locator={"ref": ref_value},
                ref=ref_value,
                role=role,
                name=name,
                text=name,
            )
            self._ref_targets[ref_value] = target.target_id
        if target.target_id not in self._interactive_target_ids:
            self._interactive_target_ids.append(target.target_id)

    def _new_target(self, *, source: str, locator: dict[str, str], **kwargs: Any) -> Target:
        self._target_counter += 1
        target_id = f"t_g{self.generation}_{self._target_counter}"
        target = self.target_type(
            target_id=target_id,
            page_id=self.page_id,
            generation=self.generation,
            source=source,
            locator=dict(locator),
            **kwargs,
        )
        self._targets[target_id] = target
        return target

    def _validate_target_generation(self, target: Target) -> None:
        if target.page_id != self.page_id or target.generation != self.generation:
            raise ValueError(
                f"Stale target_id {target.target_id} belongs to {target.generation_id}; "
                f"current generation is {self.generation_id}."
            )

    def _remove_current_ax_ref(self, ref_value: str) -> None:
        target_id = self._ref_targets.get(ref_value)
        target = self._targets.get(target_id or "")
        if target is not None and target.generation == self.generation and target.source == "ax":
            self._targets.pop(target.target_id, None)
            self._interactive_target_ids = [
                current_target_id
                for current_target_id in self._interactive_target_ids
                if current_target_id != target.target_id
            ]
            if self._ref_targets.get(ref_value) == target.target_id:
                self._ref_targets.pop(ref_value, None)
        if self.reference_generations.get(ref_value) == self.generation:
            self.reference_generations.pop(ref_value, None)

    def _export_targets(self, target_ids: Iterable[str], limit: int) -> list[dict[str, Any]]:
        exported: list[dict[str, Any]] = []
        seen: set[str] = set()
        for target_id in target_ids:
            if target_id in seen:
                continue
            seen.add(target_id)
            target = self._targets.get(target_id)
            if target is None or target.generation != self.generation:
                continue
            exported.append(target.compact_index())
            if len(exported) >= limit:
                break
        return exported

    def _trim_target_history(self) -> None:
        overflow = len(self._targets) - _MAX_TARGET_HISTORY
        if overflow <= 0:
            return
        stale_ids = [target_id for target_id, target in self._targets.items() if target.generation != self.generation][
            :overflow
        ]
        for target_id in stale_ids:
            target = self._targets.pop(target_id, None)
            if target is None:
                continue
            if target.ref and self._ref_targets.get(target.ref) == target_id:
                self._ref_targets.pop(target.ref, None)
                if self.reference_generations.get(target.ref) == target.generation:
                    self.reference_generations.pop(target.ref, None)
            if target.selector:
                selector_key = (target.generation, target.selector)
                if self._selector_targets.get(selector_key) == target_id:
                    self._selector_targets.pop(selector_key, None)
                if self.selector_generations.get(target.selector) == target.generation:
                    self.selector_generations.pop(target.selector, None)
            self._forget_target(target)


__all__ = ["PageState", "Target", "compact_text"]
