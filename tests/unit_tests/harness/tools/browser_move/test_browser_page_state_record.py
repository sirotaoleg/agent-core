# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``BrowserPageState`` on contract types: generations from results, the stale check and the ref resolver."""

from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    ContextRef,
    DriverRef,
    Element,
    Hover,
    Locator,
    Observation,
    Observe,
    PointRef,
    RefResolver,
    RunScript,
    Screenshot,
    StaleRef,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    FieldKind,
    FillForm,
    FormField,
    Navigate,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

TAB_0 = ContextRef(id="0", kind="tab", title="Shop")
TAB_1 = ContextRef(id="1", kind="tab", title="Cart")


def _observation(
    *refs: str, context: ContextRef = TAB_0, address: str = "https://shop.test/", role: str = "button"
) -> Observation:
    return Observation(
        context=context,
        elements=tuple(Element(ref=DriverRef(ref, ""), role=role, name=f"name {ref}") for ref in refs),
        surface=BrowserContext(url=address, title=context.title or "", tabs=(TAB_0, TAB_1)),
    )


def _state(*refs: str, **kwargs) -> BrowserPageState:
    state = BrowserPageState(page_id="page-test", **kwargs)
    state.record(Observe(), _observation(*refs))
    return state


def test_context_changed_starts_a_new_generation_and_stamps_result_observation_and_elements() -> None:
    state = _state("e1", "e2")

    result = state.record(
        Navigate("https://shop.test/a"),
        ActionResult(ok=True, context_changed=True, observation=_observation("e1", address="https://shop.test/a")),
    )

    assert state.generation_id == "g1"
    assert result.generation == "g1"
    assert result.observation.generation == "g1"
    assert result.observation.elements[0].ref == DriverRef("e1", "g1")
    assert state.last_observation is result.observation


def test_same_address_reload_with_context_changed_makes_old_refs_stale() -> None:
    state = _state("e1")
    old_ref = DriverRef("e1", state.generation_of("e1"))

    state.record(Navigate("https://shop.test/"), ActionResult(ok=True, context_changed=True))

    with pytest.raises(StaleRef) as raised:
        state.check(Click(old_ref))
    assert raised.value.dispatched is False
    assert str(raised.value) == "Stale AX ref e1 belongs to g0; current generation is g1."


def test_context_id_change_without_flag_starts_a_new_generation() -> None:
    state = _state("e1")

    state.record(Click(DriverRef("e1", "g0")), ActionResult(ok=True, observation=_observation("e1", context=TAB_1)))

    assert state.generation_id == "g1"
    assert state.current_context == TAB_1


def test_address_change_without_flag_starts_a_new_generation() -> None:
    state = _state("e1")

    state.record(
        Click(DriverRef("e1", "g0")),
        ActionResult(ok=True, surface=BrowserContext(url="https://shop.test/other")),
    )

    assert state.generation_id == "g1"
    assert state.url == "https://shop.test/other"


def test_element_set_change_above_the_threshold_starts_a_new_generation() -> None:
    state = _state("e1", "e2", "e3", "e4")

    state.record(Observe(), _observation("e1", "e5", "e6", "e7"))

    assert state.generation_id == "g1"


def test_element_set_change_at_or_below_the_threshold_keeps_the_generation() -> None:
    state = _state("e1", "e2", "e3")

    state.record(Observe(), _observation("e1", "e2", "e3", "e4", "e5"))

    assert state.generation_id == "g0"
    assert state.generation_of("e5") == "g0"


def test_element_set_threshold_is_a_constructor_parameter() -> None:
    state = _state("e1", "e2", "e3", element_change_threshold=0.1)

    state.record(Observe(), _observation("e1", "e2", "e3", "e4"))

    assert state.generation_id == "g1"


def test_role_change_of_the_same_ref_counts_as_an_element_change() -> None:
    state = _state("e1", "e2")

    state.record(Observe(), _observation("e1", "e2", role="link"))

    assert state.generation_id == "g1"


def test_observation_elements_become_ax_targets_and_replace_missing_refs() -> None:
    state = _state("e1", "e2")

    state.record(Observe(), _observation("e1", "e2", "e3"))
    state.record(Observe(), _observation("e2", "e3"))

    interactives = state.export()["interactives"]
    assert [item["text"] for item in interactives] == ["name e2", "name e3"]
    assert "e1" not in state.reference_generations
    assert state.resolve(interactives[0]["target_id"], "g0") == DriverRef("e2", "g0")


def test_a_scoped_observation_keeps_the_generation_and_the_last_full_observation() -> None:
    state = _state("e1", "e2", "e3", "e4")
    full = state.last_observation

    scoped = state.record(Observe(depth=1), _observation("e9"))

    assert state.generation_id == "g0"
    assert state.last_observation is state.fresh_observation is full
    assert scoped.generation == "g0" and scoped.elements[0].ref == DriverRef("e9", "g0")


def test_a_scoped_observation_adds_its_refs_and_keeps_the_refs_it_does_not_show() -> None:
    state = _state("e1", "e2")

    state.record(Observe(root=DriverRef("e2", "g0")), _observation("e2", "e3"))

    assert state.generation_of("e1") == state.generation_of("e3") == "g0"
    state.check(Click(DriverRef("e1", "g0")))
    assert [item["text"] for item in state.export()["interactives"]] == ["name e1", "name e2", "name e3"]


def test_a_scoped_observation_does_not_make_an_outdated_observation_fresh_again() -> None:
    state = _state("e1")
    state.record(RunScript(code="() => 1"), ActionResult(ok=True, value=1))

    state.record(Observe(depth=1), _observation("e1"))

    assert state.fresh_observation is None
    state.record(Observe(), _observation("e1"))
    assert state.fresh_observation is not None


def test_check_rejects_a_scoped_observe_rooted_at_a_stale_ref() -> None:
    state = _state("e1")
    state.reset()

    with pytest.raises(StaleRef, match="Stale AX ref e1 belongs to g0"):
        state.check(Observe(root=DriverRef("e1", "g0")))


def test_check_finds_stale_refs_nested_in_fill_form() -> None:
    state = _state("e1", "e2")
    fields = tuple(
        FormField(target=DriverRef(ref, state.generation_of(ref)), kind=FieldKind.TEXTBOX, value="x")
        for ref in ("e1", "e2")
    )
    state.record(Navigate("https://shop.test/a"), ActionResult(ok=True, context_changed=True))

    with pytest.raises(StaleRef) as raised:
        state.check(FillForm(fields))

    assert "e1 belongs to g0" in str(raised.value)
    assert "e2 belongs to g0" in str(raised.value)


def test_check_rejects_stale_point_and_locator_refs() -> None:
    state = _state()
    state.reset()

    with pytest.raises(StaleRef, match="PointRef belongs to g0"):
        state.check(Click(PointRef(1, 2, "g0")))
    with pytest.raises(StaleRef, match="Stale selector belongs to g0; current generation is g1: #buy"):
        state.check(Click(Locator(css="#buy", text=None, generation="g0")))


def test_check_passes_unknown_refs_of_the_current_generation() -> None:
    state = _state("e1")

    state.check(Click(DriverRef("e99", state.generation_of("e99"))))

    assert state.generation_of("e99") == "g0"


def test_contexts_are_replaced_on_a_context_change() -> None:
    state = _state("e1")
    assert state.contexts == (TAB_0, TAB_1)

    state.record(Navigate("https://shop.test/a"), ActionResult(ok=True, context_changed=True))

    assert state.contexts == ()
    assert state.current_context is None
    assert state.surface_context is None
    assert state.last_observation is None


def test_free_observation_counter_counts_observations_that_came_with_results() -> None:
    state = _state("e1")

    state.record(Click(DriverRef("e1", "g0")), ActionResult(ok=True, observation=_observation("e1")))
    state.record(Click(DriverRef("e1", "g0")), ActionResult(ok=True))
    state.record(Observe(), _observation("e1"))

    assert state.free_observations == 1


def test_resolve_returns_a_locator_for_a_probe_target_and_rejects_a_link_only_target() -> None:
    state = BrowserPageState(page_id="page-test")
    state.register_interactives(
        {
            "elements": [
                {"role": "button", "selector_hint": "#buy", "selector_hint_validated": True, "match_count": 1},
                {"role": "link", "href": "https://shop.test/item"},
            ]
        }
    )
    with_selector, link_only = (item["target_id"] for item in state.export()["interactives"])

    assert state.resolve(with_selector, "g0") == Locator(css="#buy", text=None, generation="g0")
    with pytest.raises(ValueError, match="has no element locator"):
        state.resolve(link_only, "g0")


def test_resolve_rejects_a_stale_target() -> None:
    state = _state("e1")
    target_id = state.export()["interactives"][0]["target_id"]
    state.reset()

    with pytest.raises(ValueError, match="Stale"):
        state.resolve(target_id, "g1")


def test_page_state_serves_tool_decoders_as_their_ref_resolver() -> None:
    def build_click(args: dict, refs: RefResolver) -> Click:
        return Click(DriverRef(args["target"], refs.generation_of(args["target"])))

    def build_select(args: dict, refs: RefResolver) -> SwitchContext:
        return SwitchContext(refs.contexts[args["index"]])

    state = _state("e1")

    assert build_click({"target": "e1"}, state) == Click(DriverRef("e1", "g0"))
    assert build_select({"index": 1}, state) == SwitchContext(TAB_1)
    assert state.current_context == TAB_0


def test_a_target_the_model_sends_on_the_mcp_dialect_passes_the_check_after_the_generation_moved() -> None:
    state = _state("e1")
    state.reset()

    click = DECODERS["browser_click"]({"target": "e1"}, state)
    hover = DECODERS["browser_hover"]({"target": "#buy"}, state)

    assert (click, hover) == (Click(DriverRef("e1", "")), Hover(Locator("#buy", None, "")))
    state.check(click)
    state.check(hover)


def test_reset_starts_a_new_generation_and_forgets_contexts() -> None:
    state = _state("e1")

    state.reset()

    assert state.generation_id == "g1"
    assert state.generation_of("e1") == "g0"
    assert (state.contexts, state.current_context, state.last_observation) == ((), None, None)


SHOT = Screenshot(b"\x89PNG-fake", 2, 2)


def test_the_last_observation_is_fresh_until_a_command_is_sent_without_one() -> None:
    state = _state("e1")
    observed = state.last_observation

    assert state.fresh_observation is observed
    state.record(RunScript(code="() => 1"), ActionResult(ok=True, value=1))
    assert state.fresh_observation is None
    state.record(Click(DriverRef("e1", "g0")), ActionResult(ok=True, observation=_observation("e1")))
    assert state.fresh_observation is state.last_observation is not None


def test_a_dispatched_failure_outdates_the_last_observation() -> None:
    state = _state("e1")

    state.note_dispatched()

    assert state.fresh_observation is None and state.last_observation is not None


def test_an_observation_of_an_older_generation_is_not_fresh() -> None:
    state = _state("e1")

    state.advance()

    assert state.last_observation is not None and state.fresh_observation is None


def test_a_screenshot_records_its_generation_for_points() -> None:
    state = _state("e1")
    assert state.screenshot_generation == ""

    state.record(Observe(include_screenshot=True), dataclasses.replace(_observation("e1"), screenshot=SHOT))
    state.record(Navigate("https://shop.test/a"), ActionResult(ok=True, context_changed=True))

    assert (state.screenshot_generation, state.generation_id) == ("g0", "g1")
    with pytest.raises(StaleRef):
        state.check(Click(PointRef(1, 2, state.screenshot_generation)))
