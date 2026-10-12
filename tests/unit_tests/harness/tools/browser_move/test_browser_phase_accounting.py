# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Phase budgets are charged on what each command did: actions leave extraction, progress is free, reads count once."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import Click, DriverRef, Observe, RunScript
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.runtime_rail import BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.surfaces.browser.semantic.state import SemanticStateTracker
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    MCP_PREFIX,
    command_kind,
    make_runtime,
    mcp_fake_driver,
    observation,
    result,
)

STATE_KEY = "__browser_phase_budget_state__"
CLICK = MCP_PREFIX + "browser_click"
NAVIGATE = MCP_PREFIX + "browser_navigate"
EVALUATE = MCP_PREFIX + "browser_evaluate"
SIZE_CLICK = command_kind(Click(DriverRef("e7", "g1")))


class _Session:
    def __init__(self, task: str, **state: Any) -> None:
        self._state = {STATE_KEY: {**BrowserRuntimeRail._build_phase_state(task), **state}}

    def get_session_id(self) -> str:
        return "phase-session"

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, payload: dict[str, Any]) -> None:
        self._state.update(payload)

    def phase(self, name: str) -> dict[str, Any]:
        return self._state[STATE_KEY]["phases"][name]


def _consume(session: _Session, kind: Any, tool: str, args: dict[str, Any]) -> str:
    return BrowserRuntimeRail._consume_phase_budget(session, kind, tool, args)


def _script(code: str) -> Any:
    return command_kind(RunScript(code))


def test_a_click_while_extracting_counts_against_the_form_phase_not_extraction() -> None:
    session = _Session("Find the price of a women's golf polo in size M", current_phase="extraction")

    _consume(session, SIZE_CLICK, CLICK, {"target": "e7"})

    assert session.phase("form")["attempts"] == 1
    assert session.phase("extraction")["attempts"] == 0


def test_the_same_page_script_costs_one_extraction_attempt() -> None:
    session = _Session("Read the price", current_phase="extraction")

    for _ in range(3):
        _consume(session, _script("() => document.title"), EVALUATE, {"function": "() => document.title"})
    _consume(session, _script("() => location.href"), EVALUATE, {"function": "() => location.href"})

    assert session.phase("extraction")["attempts"] == 2


def test_distinct_reads_still_exhaust_the_extraction_budget() -> None:
    session = _Session("Read the price", current_phase="extraction")
    budget = session.phase("extraction")["budget"]

    for index in range(budget):
        code = f"() => {index}"
        _consume(session, _script(code), EVALUATE, {"function": code})

    with pytest.raises(ValueError, match="extraction phase budget exhausted"):
        _consume(session, _script("() => 'one more'"), EVALUATE, {"function": "() => 'one more'"})


@pytest.mark.parametrize(("progressed", "attempts"), [(True, 0), (False, 1)])
def test_an_action_is_refunded_only_when_its_record_shows_progress(progressed: bool, attempts: int) -> None:
    session = _Session("Open the size picker")
    _consume(session, SIZE_CLICK, CLICK, {"target": "e7"})

    BrowserRuntimeRail._record_phase_result(
        session, SIZE_CLICK, CLICK, {"target": "e7"}, {"ok": True}, progressed=progressed
    )

    assert session.phase("navigation")["attempts"] == attempts


def test_a_read_is_never_refunded() -> None:
    session = _Session("Read the price", current_phase="extraction")
    kind = command_kind(Observe())
    _consume(session, kind, MCP_PREFIX + "browser_snapshot", {})

    BrowserRuntimeRail._record_phase_result(
        session, kind, MCP_PREFIX + "browser_snapshot", {}, {"ok": True}, progressed=True
    )

    assert session.phase("extraction")["attempts"] == 1


def test_stalled_actions_still_exhaust_their_phase() -> None:
    session = _Session("Open the size picker")
    budget = session.phase("navigation")["budget"]

    for _ in range(budget):
        _consume(session, SIZE_CLICK, CLICK, {"target": "e7"})
        BrowserRuntimeRail._record_phase_result(session, SIZE_CLICK, CLICK, {"target": "e7"}, {"ok": True})

    with pytest.raises(ValueError, match="navigation phase budget exhausted"):
        _consume(session, SIZE_CLICK, CLICK, {"target": "e7"})


def _play(rail: BrowserRuntimeRail, runtime: Any, session: _Session, tool: str, args: dict[str, Any]) -> None:
    call = ToolCall(
        id=f"{tool}-{len(session.get_state(STATE_KEY)['recent_actions'])}", type="function", name=tool, arguments="{}"
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ToolCallInputs(tool_call=call, tool_name=tool, tool_args=args),
        extra={},
    )
    rail._prepare_tool_call(ctx)
    tools = {item.card.name: item for item in runtime.agent_parts(image_support=False, language="en").tools}
    output = asyncio.run(tools[tool].invoke(args))
    ctx.inputs.tool_result = output
    ctx.inputs.tool_msg = ToolMessage(tool_call_id=call.id, content=str(output.data))
    asyncio.run(rail.after_tool_call(ctx))


def test_after_the_first_page_navigations_that_land_on_new_pages_cost_nothing() -> None:
    urls = [f"https://www.flightaware.com/page-{index}" for index in range(15)]
    driver = mcp_fake_driver(results=[result(url=url, title="FlightAware") for url in urls])
    runtime = make_runtime(driver)
    session = _Session("Find the busiest airport last week on FlightAware")
    rail = BrowserRuntimeRail(runtime)

    for url in urls:
        _play(rail, runtime, session, NAVIGATE, {"url": url})

    assert session.phase("navigation")["attempts"] == 1
    assert [call.payload for call in driver.calls if call.kind == "execute"] == [Navigate(url) for url in urls]


def test_a_click_that_changes_nothing_is_charged() -> None:
    driver = mcp_fake_driver(
        observations=[observation()], results=[result(url="https://example.com/", title="Example")] * 2
    )
    runtime = make_runtime(driver)
    session = _Session("Open the size picker")
    rail = BrowserRuntimeRail(runtime)
    _play(rail, runtime, session, NAVIGATE, {"url": "https://example.com/"})

    _play(rail, runtime, session, CLICK, {"target": "e1", "element": "Size M"})

    assert session.phase("navigation")["attempts"] == 1


def test_a_changed_page_after_a_repeated_semantic_state_is_progress() -> None:
    tracker = SemanticStateTracker()
    state = {"url": "https://www.amazon.com/dp/B0FG6L8B2C"}
    tracker.observe(state)

    unchanged = tracker.observe(state)
    changed = tracker.observe(state, changed=True)

    assert unchanged["progress"] == "no_progress"
    assert (changed["progress"], changed["consecutive_no_progress"]) == ("progress", 0)


def test_three_unchanged_repeats_still_require_a_replan() -> None:
    tracker = SemanticStateTracker()
    state = {"url": "https://www.amazon.com/dp/B0FG6L8B2C"}
    for _ in range(4):
        latest = tracker.observe(state)

    assert latest["replan_required"] is True


def test_revisiting_an_earlier_state_is_a_revisit_even_when_the_page_changed() -> None:
    tracker = SemanticStateTracker()
    first, second = {"url": "https://a.test/"}, {"url": "https://b.test/"}
    tracker.observe(first)
    tracker.observe(second)

    assert tracker.observe(first, changed=True)["progress"] == "state_revisit"


def test_the_runtime_reports_a_change_only_when_the_generation_or_observation_moved() -> None:
    runtime = make_runtime(mcp_fake_driver())

    first = runtime._semantic_changed()
    same = runtime._semantic_changed()
    runtime.page_state.advance(url="https://example.com/next")
    moved = runtime._semantic_changed()
    runtime.page_state.record(Observe(), observation())
    observed = runtime._semantic_changed()

    assert (first, same, moved, observed) == (False, False, True, True)
