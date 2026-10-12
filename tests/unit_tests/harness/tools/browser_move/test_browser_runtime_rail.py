#!/usr/bin/env python
# coding: utf-8
"""Tests for BrowserRuntimeRail lifecycle hook."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message import ToolMessage, UserMessage
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    ActionTimeout,
    Click,
    ConnectionLost,
    DriverError,
    DriverInfo,
    DriverRef,
    Observe,
    RunScript,
    StaleRef,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS
from openjiuwen.harness.tools.browser_move.runtime import session as runtime_module
from openjiuwen.harness.tools.browser_move.runtime.service import MAX_ITERATION_MESSAGE
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import CommandRecord, command_record
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import (
    CORE_BROWSER_TOOL_NAMES,
    resolve_browser_capabilities,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate
from openjiuwen.harness.tools.browser_move.surfaces.browser.working_context.store import (
    BrowserWorkingContextStore,
)
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    BATCH,
    MCP_PREFIX,
    PROBE,
    RECALL,
    command_kind,
    element,
    make_runtime,
    mcp_fake_driver,
    observation,
    result,
)

CLICK = MCP_PREFIX + "browser_click"
NAVIGATE = MCP_PREFIX + "browser_navigate"
SNAPSHOT = MCP_PREFIX + "browser_snapshot"
EVALUATE = MCP_PREFIX + "browser_evaluate"
DRAG = MCP_PREFIX + "browser_drag"


def _run(coro):
    return asyncio.run(coro)


def _make_ctx() -> AgentCallbackContext:
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=True, language="en")
    agent.register_rail = AsyncMock()
    return AgentCallbackContext(agent=agent)


class _FakeSession:
    def __init__(self, session_id: str = "browser-session") -> None:
        self._session_id = session_id
        self._state = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str):
        return self._state.get(key)

    def update_state(self, payload):
        self._state.update(payload)


def _mock_runtime() -> MagicMock:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.tool_kind.return_value = runtime_module.ToolKind()
    return runtime


def _tools(runtime: BrowserAgentRuntime) -> dict:
    return {tool.card.name: tool for tool in runtime.agent_parts(image_support=False, language="en").tools}


def _invoke(runtime: BrowserAgentRuntime, name: str, args: dict):
    return _run(_tools(runtime)[name].invoke(args))


def _tool_ctx(runtime: BrowserAgentRuntime, name: str, args: dict, output, *, session=None) -> AgentCallbackContext:
    content = output.data["content"] if output.data else output.error
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ToolCallInputs(
            tool_name=name,
            tool_args=args,
            tool_result=output,
            tool_msg=ToolMessage(tool_call_id=f"{name}-call", content=text),
        ),
    )


def _added_names(ctx: AgentCallbackContext) -> set[str]:
    return {call.args[0].name for call in ctx.agent.ability_manager.add_ability.call_args_list}


def test_rail_is_agent_rail_subclass() -> None:
    assert issubclass(BrowserRuntimeRail, AgentRail)


def test_rail_holds_runtime_reference() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    assert rail._runtime is runtime


def test_reset_active_browser_runtimes_resets_all_live_instances() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.reset = AsyncMock()

    first = _Runtime()
    second = _Runtime()
    runtime_module._ACTIVE_BROWSER_RUNTIMES.clear()
    runtime_module._ACTIVE_BROWSER_RUNTIMES.add(first)
    runtime_module._ACTIVE_BROWSER_RUNTIMES.add(second)

    assert _run(runtime_module.reset_active_browser_runtimes()) == 2
    first.reset.assert_awaited_once()
    second.reset.assert_awaited_once()
    runtime_module._ACTIVE_BROWSER_RUNTIMES.clear()


def test_before_invoke_calls_ensure_runtime_ready() -> None:
    runtime = make_runtime(image_support=None)
    runtime.ensure_runtime_ready = AsyncMock()
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()

    _run(rail.before_invoke(ctx))

    runtime.ensure_runtime_ready.assert_called_once_with()
    parts = runtime.agent_parts(image_support=True, language="en")
    assert _added_names(ctx) == {tool.card.name for tool in parts.tools}
    ctx.agent.register_rail.assert_not_awaited()


def test_before_invoke_builds_the_parts_in_the_agents_language() -> None:
    runtime = make_runtime(image_support=None)
    runtime.ensure_runtime_ready = AsyncMock()
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.deep_config.language = "cn"

    with patch.object(runtime, "agent_parts", wraps=runtime.agent_parts) as parts:
        _run(rail.before_invoke(ctx))

    parts.assert_called_once_with(image_support=True, language="cn")


def test_before_invoke_with_none_allowlist_defaults_to_core() -> None:
    runtime = make_runtime(allowed_tool_names=None, image_support=None)
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()

    _run(rail.before_invoke(ctx))

    names = _added_names(ctx)
    assert {MCP_PREFIX + name for name in CORE_BROWSER_TOOL_NAMES} <= names
    helpers = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact", "browser_recall_offload"}
    assert helpers <= names
    assert MCP_PREFIX + "browser_pdf_save" not in names


def test_before_invoke_removes_screenshot_for_non_multimodal_model() -> None:
    allowed = resolve_browser_capabilities(["vision"]).allowed_tool_names
    runtime = make_runtime(allowed_tool_names=allowed, image_support=None)
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.deep_config.enable_read_image_multimodal = False

    _run(rail.before_invoke(ctx))

    names = _added_names(ctx)
    assert MCP_PREFIX + "browser_take_screenshot" not in names
    assert {MCP_PREFIX + "browser_click", MCP_PREFIX + "browser_mouse_click_xy"} <= names


def test_before_invoke_builds_non_multimodal_allowlist_from_core() -> None:
    runtime = make_runtime(allowed_tool_names=None, image_support=None)
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()
    ctx.agent.deep_config.enable_read_image_multimodal = False

    _run(rail.before_invoke(ctx))

    assert {MCP_PREFIX + name for name in CORE_BROWSER_TOOL_NAMES} <= _added_names(ctx)
    assert not any("screenshot" in name for name in _added_names(ctx))


def test_pdf_allowlist_filters_active_browser_agent_schemas() -> None:
    allowed = resolve_browser_capabilities(["pdf"]).allowed_tool_names
    runtime = make_runtime(allowed_tool_names=allowed, image_support=None)
    rail = BrowserRuntimeRail(runtime)
    agent = MagicMock()
    agent.ability_manager = AbilityManager()
    agent.register_rail = AsyncMock()
    ctx = AgentCallbackContext(agent=agent)

    _run(rail.before_invoke(ctx))

    visible_names = {tool.name for tool in _run(agent.ability_manager.list_tool_info())}
    assert "mcp_playwright-official_browser_click" in visible_names
    assert "mcp_playwright-official_browser_pdf_save" in visible_names
    assert "mcp_playwright-official_browser_get_config" not in visible_names
    assert "mcp_playwright-official_browser_cookie_list" not in visible_names
    assert "mcp_playwright-official_browser_mouse_click_xy" not in visible_names


def test_before_invoke_called_twice_delegates_twice() -> None:
    """The tools are added on every invoke; the rail registers no rail (they are queued at construction)."""
    runtime = make_runtime(image_support=None)
    runtime.ensure_runtime_ready = AsyncMock()
    rail = BrowserRuntimeRail(runtime)
    ctx1 = _make_ctx()
    ctx2 = AgentCallbackContext(agent=ctx1.agent)

    _run(rail.before_invoke(ctx1))
    _run(rail.before_invoke(ctx2))

    assert runtime.ensure_runtime_ready.call_count == 2
    parts = runtime.agent_parts(image_support=True, language="en")
    assert ctx1.agent.ability_manager.add_ability.call_count == 2 * len(parts.tools)
    assert ctx1.agent.register_rail.await_count == 0


def test_a_session_that_cannot_start_aborts_the_invoke_before_any_tool_is_added() -> None:
    runtime = make_runtime(image_support=None)
    error = ConnectionLost("connect: no device", dispatched=False)
    runtime.ensure_runtime_ready = AsyncMock(side_effect=error)
    rail = BrowserRuntimeRail(runtime)
    ctx = _make_ctx()

    with pytest.raises(AbortError) as raised:
        _run(rail.before_invoke(ctx))

    assert (raised.value.cause, raised.value.reason) == (error, "connect: no device")
    ctx.agent.ability_manager.add_ability.assert_not_called()
    ctx.agent.register_rail.assert_not_awaited()


def test_before_tool_call_normalizes_explicit_snapshot_ref_target() -> None:
    rail = BrowserRuntimeRail(make_runtime())
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=CLICK,
            tool_args={
                "target": "ref=f2e36",
                "ref": "[ref=f2e36]",
                "element": "search input",
            },
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_args == {
        "target": "f2e36",
        "element": "search input",
    }


def _drag_runtime() -> BrowserAgentRuntime:
    return make_runtime(allowed_tool_names=resolve_browser_capabilities(["extended_interaction"]).allowed_tool_names)


def test_before_tool_call_normalizes_bracketed_refs_in_json_arguments() -> None:
    rail = BrowserRuntimeRail(_drag_runtime())
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=DRAG,
            tool_args=('{"startTarget": "[ref=f1e2]", "endTarget": "ref=f1e9", "element": "card"}'),
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert '"startTarget": "f1e2"' in ctx.inputs.tool_args
    assert '"endTarget": "f1e9"' in ctx.inputs.tool_args


def test_before_tool_call_rewrites_direct_mcp_ref_aliases() -> None:
    rail = BrowserRuntimeRail(_drag_runtime())
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=DRAG,
            tool_args={
                "startRef": "[ref=f1e2]",
                "endRef": "ref=f1e9",
                "element": "card",
            },
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_args == {
        "startTarget": "f1e2",
        "endTarget": "f1e9",
    }


def test_before_tool_call_preserves_css_selector_targets() -> None:
    rail = BrowserRuntimeRail(make_runtime())
    args = {"target": "#search-input", "element": "search input"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=CLICK,
            tool_args=args,
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_args is args


def test_before_tool_call_rewrites_card_primary_link_click_to_navigation() -> None:
    runtime = make_runtime()
    runtime.page_state.selector_primary_links[".result-card:nth-of-type(1)"] = (0, "https://example.com/item/1")
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=CLICK,
            tool_args={"target": ".result-card:nth-of-type(1)", "element": "card"},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_name == NAVIGATE
    assert ctx.inputs.tool_args == {"url": "https://example.com/item/1"}


def test_before_tool_call_leaves_the_browser_dialects_ref_and_generation_alone() -> None:
    driver = FakeDriver(info=DriverInfo(name="fake", surfaces=frozenset({"browser"}), actions=DEFAULT_ACTIONS))
    rail = BrowserRuntimeRail(make_runtime(driver))
    arguments = {"ref": "bnid:8", "generation_id": "g0"}
    ctx = AgentCallbackContext(
        agent=MagicMock(), inputs=ToolCallInputs(tool_name="browser_click", tool_args=dict(arguments))
    )

    _run(rail.before_tool_call(ctx))

    assert (ctx.inputs.tool_name, ctx.inputs.tool_args) == ("browser_click", arguments)


def test_before_tool_call_rejects_batch_screenshot_without_image_support() -> None:
    rail = BrowserRuntimeRail(make_runtime())
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=False, language="en")
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_name="browser_batch_interact",
            tool_args={
                "steps": [
                    {"op": "click", "selector": "#search"},
                    {"op": "screenshot", "path": "result.png"},
                ]
            },
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_result["status"] == "denied"
    assert ctx.inputs.tool_result["error"]["code"] == "browser_image_input_unavailable"
    assert ctx.inputs.tool_msg is not None
    assert ctx.extra["_skip_tool_calls"]


def test_a_ref_from_before_a_navigation_reaches_the_driver_and_the_server_refuses_it() -> None:
    driver = mcp_fake_driver(
        observations=[observation([element("f1e2", "textbox", "Search")])],
        results=[result(url="https://example.com/results", context_changed=True)],
    )
    runtime = make_runtime(driver)

    assert _invoke(runtime, SNAPSHOT, {}).success is True
    assert runtime.generation_id == "g0"

    assert _invoke(runtime, NAVIGATE, {"url": "https://example.com/results"}).success is True

    assert runtime.generation_id == "g1"
    fresh = observation([element("e3", "link", "First result")], url="https://example.com/results")
    driver.fail_next(StaleRef("Ref f1e2 not found in the current page snapshot.", observation=fresh))
    stale = _invoke(runtime, CLICK, {"target": "f1e2"})
    assert stale.success is False
    assert "Ref f1e2 not found in the current page snapshot." in stale.error
    assert "[ref=e3]" in stale.error
    assert driver.kinds() == ["observe", "execute", "execute"]
    assert driver.calls[-1].payload == Click(DriverRef("f1e2", ""))


def test_a_ref_from_before_a_url_change_reaches_the_driver_again() -> None:
    driver = mcp_fake_driver(
        observations=[observation([element("e7", "link", "Result")], url="https://example.com/")],
        results=[result(url="https://example.com/result/7"), result(url="https://example.com/result/7")],
    )
    runtime = make_runtime(driver)

    _invoke(runtime, SNAPSHOT, {})
    assert _invoke(runtime, CLICK, {"target": "e7"}).success is True

    assert runtime.generation_id == "g1"
    assert _invoke(runtime, CLICK, {"target": "e7"}).success is True
    assert driver.kinds() == ["observe", "execute", "execute"]
    assert runtime.generation_id == "g1"


def test_after_snapshot_attaches_compact_page_state_to_tool_message() -> None:
    runtime = make_runtime(mcp_fake_driver(observations=[observation([element("f1e2", "textbox", "Search")])]))
    rail = BrowserRuntimeRail(runtime)
    output = _invoke(runtime, SNAPSHOT, {})
    ctx = _tool_ctx(runtime, SNAPSHOT, {}, output)
    tool_message = ctx.inputs.tool_msg
    assert "[ref=f1e2]" in tool_message.content

    _run(rail.after_tool_call(ctx))

    assert '"observation":"compact_page_state"' in tool_message.content
    assert '"generation_id":"g0"' in tool_message.content
    assert '"target_id":"t_g0_1"' in tool_message.content
    assert "[ref=f1e2]" not in tool_message.content
    assert '"ref":"f1e2"' not in tool_message.content


def test_card_primary_link_mapping_is_generation_scoped() -> None:
    runtime = make_runtime()
    page_state = runtime.page_state
    page_state.selector_primary_links[".card:nth-of-type(1)"] = (page_state.generation, "https://example.com/item/1")

    assert runtime.resolve_primary_link({"selector": ".card:nth-of-type(1)"}) == "https://example.com/item/1"

    page_state.advance()

    assert runtime.resolve_primary_link({"selector": ".card:nth-of-type(1)"}) == ""


def _click_record(error: DriverError | None = None, *, ok: bool = True) -> CommandRecord:
    runtime = make_runtime()
    action = Click(DriverRef("e4", "g0"))
    if error is not None:
        return CommandRecord.failed(action, error, runtime.page_state)
    return CommandRecord.of(action, ActionResult(ok=ok, detail=None if ok else "### Error\nnot actionable"))


@pytest.mark.parametrize(
    ("record", "state_changed"),
    [
        (_click_record(ActionTimeout("locator click timed out after 2500ms", dispatched=True)), True),
        (_click_record(DriverError("Error: element is not actionable", dispatched=True)), False),
        (_click_record(DriverError("MCP request failed", dispatched=False)), False),
        (_click_record(ok=False), False),
    ],
)
def test_tool_result_classifier_rejects_transport_error_shapes(record: CommandRecord, state_changed: bool) -> None:
    kind = command_kind(record.action)

    success, executed, changed = BrowserRuntimeRail._outcome(kind, record, None, {"ok": record.ok})

    assert success is False
    assert executed is record.dispatched
    assert changed is state_changed


def test_direct_mcp_target_id_is_resolved_and_runtime_fields_are_removed() -> None:
    runtime = make_runtime()
    payload = {
        "elements": [
            {
                "selector_hint": "#search",
                "selector_hint_validated": True,
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
                "role": "textbox",
                "text": "Search",
            }
        ]
    }
    runtime.page_state.register_interactives(payload)
    target_id = payload["elements"][0]["target_id"]
    rail = BrowserRuntimeRail(runtime)

    normalized = rail._normalize_target_args({"target_id": target_id, "generation_id": "g0"})

    assert normalized == {"target": "#search", "element": "Search"}


def test_evaluate_runtime_field_contract_is_not_forwarded_to_mcp() -> None:
    rail = BrowserRuntimeRail(make_runtime())

    normalized = rail._normalize_target_args(
        {
            "target": "document",
            "element": "page",
            "function": "() => ({profileName: 'Alice'})",
            "field": "author",
            "fields": ["author", "comments"],
        },
        frozenset({"target", "element", "function"}),
    )

    assert normalized == {
        "target": "document",
        "element": "page",
        "function": "() => ({profileName: 'Alice'})",
    }


def test_direct_mcp_target_is_refreshed_when_runtime_identity_is_unique() -> None:
    runtime = make_runtime()
    first = {
        "elements": [
            {
                "selector_hint": "#sales-sort",
                "selector_hint_validated": True,
                "match_count": 1,
                "actionable": True,
                "clickable": True,
                "text": "Sales",
            }
        ]
    }
    runtime.page_state.register_interactives(first)
    stale_target_id = first["elements"][0]["target_id"]
    runtime.page_state.advance()
    current = {
        "elements": [
            {
                "selector_hint": "#sales-sort",
                "selector_hint_validated": True,
                "match_count": 1,
                "actionable": True,
                "clickable": True,
                "text": "Sales",
            }
        ]
    }
    runtime.page_state.register_interactives(current)
    rail = BrowserRuntimeRail(runtime)

    normalized = rail._normalize_target_args({"target_id": stale_target_id, "generation_id": "g0"})

    assert normalized == {"target": "#sales-sort", "element": "Sales"}


def test_primary_link_target_is_rewritten_to_direct_navigation() -> None:
    runtime = make_runtime()
    payload = {
        "cards": [
            {
                "title": "Result",
                "primary_link": "https://example.test/item/1",
                "region": "main_result",
                "kind": "product",
            }
        ]
    }
    runtime.page_state.register_cards(payload)
    target_id = payload["cards"][0]["primary_link_target_id"]
    rail = BrowserRuntimeRail(runtime)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=CLICK,
            tool_args={"target_id": target_id, "generation_id": "g0"},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_name == NAVIGATE
    assert ctx.inputs.tool_args == {"url": "https://example.test/item/1"}


def test_phase_plan_uses_explicit_completion_conditions_and_large_budgets() -> None:
    state = BrowserRuntimeRail._build_phase_state("Compare products, apply filters, and complete the checkout form")

    assert state["task_type"] == "complex"
    assert list(state["phases"]) == [
        "navigation",
        "form",
        "filtering",
        "extraction",
    ]
    assert state["phases"]["navigation"]["budget"] == 12
    assert state["phases"]["form"]["budget"] == 24
    assert state["phases"]["filtering"]["budget"] == 20
    assert state["phases"]["extraction"]["budget"] == 20
    assert all(phase["completion_condition"] for phase in state["phases"].values())
    assert BrowserRuntimeRail._build_phase_state("对比两个页面的信息")["task_type"] == "complex"


def test_shared_deadline_bounds_each_invocation_without_forcing_a_resume() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    session = _FakeSession()

    for task, expected_budget in (
        ("Extract the title", 240.0),
        ("Compare two products", 600.0),
    ):
        state = BrowserRuntimeRail._build_phase_state(task)
        session.update_state({"__browser_phase_budget_state__": state})
        ctx = AgentCallbackContext(agent=MagicMock(), session=session)

        remaining = rail._bind_shared_task_deadline(ctx, session, state)

        assert state["deadline_budget_s"] == expected_budget
        assert 0 < remaining <= expected_budget
        assert remaining > expected_budget - 1.0
        assert remaining < 720.0


def test_known_url_must_be_navigated_before_selector_exploration() -> None:
    session = _FakeSession()
    session.update_state(
        {
            "__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state(
                "Inspect https://example.com/products"
            )
        }
    )

    with pytest.raises(ValueError, match="Navigate to it directly"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            command_kind(Observe()),
            SNAPSHOT,
            {},
        )


def test_exhausted_phase_budget_requires_replan() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Search for OpenJiuwen")
    state["phases"]["navigation"]["attempts"] = state["phases"]["navigation"]["budget"]
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="budget exhausted"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            command_kind(Navigate("https://www.baidu.com/s?wd=OpenJiuwen")),
            NAVIGATE,
            {"url": "https://www.baidu.com/s?wd=OpenJiuwen"},
        )

    phase = session.get_state("__browser_phase_budget_state__")["phases"]["navigation"]
    assert phase["status"] == "replan_required"


def test_batch_phase_signature_changes_when_steps_change() -> None:
    first = BrowserRuntimeRail._phase_action_signature(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "selector": "#q", "value": "first"},
                {"op": "press", "key": "Enter"},
            ]
        },
    )
    second = BrowserRuntimeRail._phase_action_signature(
        "browser_batch_interact",
        {
            "steps": [
                {"op": "fill", "selector": "#search", "value": "second"},
                {"op": "wait_for_url", "url_contains": "/results"},
            ]
        },
    )

    assert first != second


def test_price_field_extraction_is_not_misclassified_as_filtering() -> None:
    phase = BrowserRuntimeRail._classify_tool_phase(
        BATCH,
        {
            "steps": [
                {"op": "extract_text", "field": "price"},
                {"op": "extract_text", "field": "title"},
            ]
        },
        {"current_phase": "extraction"},
    )

    assert phase == "extraction"


def test_semantic_action_signature_ignores_selector_generation_and_equivalent_tool() -> None:
    direct = BrowserRuntimeRail._phase_action_signature(
        "mcp_playwright-official_browser_click",
        {"target": "f1e2", "generation_id": "g1"},
    )
    batch = BrowserRuntimeRail._phase_action_signature(
        "browser_batch_interact",
        {"generation_id": "g8", "steps": [{"op": "click", "selector": "#different"}]},
    )

    assert direct == batch


def test_phase_attempts_do_not_reset_after_material_replan() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Filter products by price")
    phase = state["phases"]["filtering"]
    phase["attempts"] = 3
    state["current_phase"] = "filtering"
    state["status"] = "replan_required"
    state["replan_required"] = True
    state["blocked_strategy"] = "script_exploration"
    state["failed_strategies"] = ["script_exploration"]
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        BATCH,
        "browser_batch_interact",
        {"steps": [{"op": "click", "text": "100 to 200"}]},
    )

    updated = session.get_state("__browser_phase_budget_state__")["phases"]["filtering"]
    assert action_class == "filtering"
    assert updated["attempts"] == 4
    task_state = session.get_state("__browser_phase_budget_state__")
    assert task_state["replan_count"] == 1
    assert task_state["replan_trial_pending"] is True


def test_semantic_loop_marks_current_phase_replan_required() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {
        "revision": 4,
        "progress": "state_revisit",
        "consecutive_no_progress": 3,
        "state_revisit_count": 3,
        "aba_loop": True,
        "repeated_filter_state": True,
        "replan_required": True,
        "replan_reason": ["three_semantic_state_revisits"],
        "semantic_state": {"field_coverage": ["title", "price"]},
    }
    rail = BrowserRuntimeRail(runtime)
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Filter products by price")
    state["current_phase"] = "filtering"
    state["phases"]["filtering"]["last_semantic_signature"] = '[{"op":"click"}]'
    session.update_state({"__browser_phase_budget_state__": state})

    rail._sync_semantic_progress(session)

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["semantic_revision"] == 4
    assert updated["replan_count"] == 0
    assert updated["field_coverage"] == []
    assert updated["status"] == "replan_required"
    assert updated["replan_required"] is True
    assert updated["failed_strategies"] == []


def test_new_evidence_clears_pending_semantic_replan_trial() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("inspect alternating search results")
    state.update(
        {
            "status": "replan_trial",
            "replan_required": True,
            "replan_trial_pending": True,
            "trial_strategy": "structured_extraction",
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    delta = BrowserRuntimeRail._record_phase_result(
        session,
        PROBE,
        "browser_probe_cards",
        {},
        {
            "ok": True,
            "cards": [
                {
                    "region": "main_result",
                    "kind": "result",
                    "title": "Repeated result",
                    "primary_link": "https://example.test/result",
                }
            ],
        },
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert delta["evidence_added"] is True
    assert delta["recovered"] is True
    assert updated["replan_trial_pending"] is False
    assert updated["replan_required"] is False
    assert updated["status"] == "in_progress"


def test_terminal_state_is_sticky_across_semantic_progress_and_worker_report() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("extract title")
    state.update(
        {
            "status": "blocked",
            "blockers": ["semantic_replan_budget_exhausted"],
            "replan_required": True,
            "semantic_revision": 1,
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    recovered = BrowserWorkingContextStore.sync_semantic_progress(
        session,
        {
            "revision": 2,
            "progress": "progress",
            "observable_progress": True,
            "semantic_state": {"url": "https://example.test/new"},
        },
    )
    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session,
        {"status": "completed", "blockers": []},
        "done",
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert recovered is False
    assert updated["status"] == "blocked"
    assert updated["blockers"] == ["semantic_replan_budget_exhausted"]
    with pytest.raises(ValueError, match="already blocked"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            command_kind(Navigate("https://example.test/again")),
            NAVIGATE,
            {"url": "https://example.test/again"},
        )


@pytest.mark.parametrize("status", ["blocked", "partial", "completed"])
def test_terminal_state_returns_structured_denial(status: str) -> None:
    runtime = _mock_runtime()
    runtime.semantic_progress = {}
    rail = BrowserRuntimeRail(runtime)
    state = BrowserRuntimeRail._build_phase_state("inspect results")
    state.update({"status": status, "blockers": ["captcha"]})
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ToolCallInputs(
            tool_call=ToolCall(id="probe-1", type="function", name="browser_probe_cards", arguments="{}"),
            tool_name="browser_probe_cards",
            tool_args={},
        ),
    )

    _run(rail.before_tool_call(ctx))

    assert ctx.inputs.tool_result["error"]["code"] == "browser_task_terminal"
    assert ctx.inputs.tool_result["error"]["terminal"] is True
    assert ctx.extra["_skip_tool_calls"] == {"probe-1": True}


def test_comparison_evidence_requires_distinct_bilibili_sort_slots() -> None:
    state = BrowserRuntimeRail._build_phase_state("对比B站 Python 搜索综合和最新结果的标题")
    comprehensive = {
        "generation_id": "g2",
        "page_state": {"url": "https://search.bilibili.com/all?keyword=Python"},
        "cards": [{"title": "Comprehensive result", "result_index": 1, "generation_id": "g2"}],
    }
    latest = {
        "generation_id": "g3",
        "page_state": {"url": "https://search.bilibili.com/all?keyword=Python&order=pubdate"},
        "cards": [{"title": "Latest result", "result_index": 1, "generation_id": "g3"}],
    }

    BrowserRuntimeRail._record_structured_evidence(
        state,
        comprehensive,
        kind=PROBE,
        tool_name="browser_probe_cards",
        tool_args={},
    )
    assert [slot["variant"] for slot in state["evidence_slots"]] == ["comprehensive"]
    assert any("latest" in item for item in BrowserRuntimeRail._missing_completion_requirements(state))

    BrowserRuntimeRail._record_structured_evidence(
        state,
        latest,
        kind=PROBE,
        tool_name="browser_probe_cards",
        tool_args={},
    )
    assert {slot["variant"] for slot in state["evidence_slots"]} == {"comprehensive", "latest"}
    latest_slot = next(slot for slot in state["evidence_slots"] if slot["variant"] == "latest")
    assert latest_slot == {
        "entity": "bilibili_search_result",
        "variant": "latest",
        "field": "title",
        "value": "Latest result",
        "source": "https://search.bilibili.com/all?keyword=Python&order=pubdate",
        "generation": "g3",
        "status": "present",
    }
    assert BrowserRuntimeRail._missing_completion_requirements(state) == []


def test_retryable_model_failure_retries_once_then_returns_structured_error() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("find a title")})
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        extra={"__browser_task_deadline__": runtime_module.time.monotonic() + 60},
        exception=RuntimeError("model_provider_response_error: code=504"),
    )

    _run(rail.on_model_exception(ctx))
    assert ctx.consume_retry_request() is not None

    ctx.retry_attempt = 1
    _run(rail.on_model_exception(ctx))
    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["result_type"] == "error"
    assert '"status":"failed"' in finish.result["output"]


def test_ability_manager_consumes_per_call_skip_without_executing_tool() -> None:
    manager = AbilityManager.__new__(AbilityManager)
    manager._execute_single_tool_call = AsyncMock()
    tool_call = ToolCall(
        id="denied-call",
        type="function",
        name="browser_click",
        arguments='{"target_id":"t_g1_2"}',
    )
    denied_result = {"ok": False, "status": "denied"}
    denied_message = ToolMessage(
        tool_call_id="denied-call",
        content='{"ok":false,"status":"denied"}',
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_call.name,
            tool_args={"target_id": "t_g1_2"},
            tool_result=denied_result,
            tool_msg=denied_message,
        ),
        extra={"_skip_tool_calls": {"denied-call": True}},
    )

    raw_execute = AbilityManager._railed_execute_single_tool_call.__wrapped__
    result = _run(raw_execute(manager, ctx, tool_call, _FakeSession()))

    assert result == (denied_result, denied_message)
    manager._execute_single_tool_call.assert_not_awaited()


def test_worker_cannot_claim_completion_without_runtime_field_evidence() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract product title and price")
    state["phases"]["navigation"]["status"] = "completed"
    state["field_coverage"] = ["title"]
    state["structured_evidence"] = [{"kind": "structured_extraction", "fields": ["title"]}]
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session,
        {"status": "completed", "missing_requirements": []},
        "Title only",
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "partial"
    assert updated["blockers"] == [
        "missing_required_field:price",
        "missing_required_field:evidence_slot:task_result:default:title",
    ]
    assert updated["worker_reported_status"] == "completed"


@pytest.mark.parametrize(
    ("kind", "tool_name", "tool_args"),
    [
        (command_kind(Observe()), SNAPSHOT, {}),
        (PROBE, "browser_probe_interactives", {"query": "next"}),
        (PROBE, MCP_PREFIX + "browser_find", {"text": "next"}),
    ],
)
def test_target_discovery_gets_one_bounded_replan_recovery(kind, tool_name, tool_args) -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("find the next control")
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": "target_discovery",
            "failed_strategies": ["target_discovery"],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(session, kind, tool_name, tool_args)
    assert action_class == "target_discovery"

    with pytest.raises(ValueError, match="already ran without verified semantic progress"):
        BrowserRuntimeRail._consume_phase_budget(session, kind, tool_name, tool_args)

    assert session.get_state("__browser_phase_budget_state__")["replan_count"] == 1


def test_evaluate_result_becomes_compact_evidence_before_action_windowing() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("extract the title")
    session.update_state({"__browser_phase_budget_state__": state})
    function = "() => ({title: document.title, count: 3})"
    kind = command_kind(RunScript(function))

    delta = BrowserRuntimeRail._record_phase_result(
        session,
        kind,
        EVALUATE,
        {"function": function, "target": "document"},
        {"ok": True, "result": {"title": "Example", "count": 3}, "generation_id": "g4"},
    )
    BrowserRuntimeRail._record_recent_action(
        session,
        kind=kind,
        tool_name=EVALUATE,
        tool_args={"function": function, "target": "document"},
        tool_result={"ok": True},
        action_class="script_exploration",
        elapsed_ms=23,
        progress_delta=delta,
    )

    task_state = session.get_state("__browser_phase_budget_state__")
    evidence = task_state["structured_evidence"][0]
    assert evidence["kind"] == "targeted_evaluate"
    assert evidence["values"] == {"title": "Example"}
    assert evidence["generation_id"] == "g4"
    action = task_state["recent_actions"][0]
    assert action["semantic_delta"] == "evidence_added"
    assert function not in action["target_summary"]
    assert "expression_sha256" in action["target_summary"]


def test_targeted_evaluate_maps_only_requested_fields_with_provenance() -> None:
    state = BrowserRuntimeRail._build_phase_state(
        "返回文章作者、评论数和当前排序状态"
    )

    BrowserRuntimeRail._record_structured_evidence(
        state,
        {
            "ok": True,
            "generation_id": "g7",
            "result": {
                "profileName": "Alice",
                "comRaw": "还没有评论",
                "saleTabSelected": True,
                "unrelatedCount": 99,
            },
        },
        kind=command_kind(RunScript("() => ({})")),
        tool_name=EVALUATE,
        tool_args={
            "target": ".article-author",
            "_runtime_evidence_fields": ["author", "comments", "sort_state"],
        },
    )

    evidence = state["structured_evidence"][0]
    assert evidence["values"] == {
        "author": "Alice",
        "comments": "0",
        "sort_state": "销量",
    }
    assert evidence["field_status"] == {
        "author": "present",
        "comments": "present",
        "sort_state": "present",
    }
    assert "unrelatedCount" not in evidence["values"]
    assert evidence["provenance"]["author"] == {
        "source": "browser_evaluate",
        "selector": ".article-author",
        "raw_text": "Alice",
        "generation_id": "g7",
    }


def test_ambiguous_evaluate_alias_requires_explicit_target_contract() -> None:
    state = BrowserRuntimeRail._build_phase_state("返回文章作者")

    BrowserRuntimeRail._record_structured_evidence(
        state,
        {"ok": True, "result": {"profileName": "Toolbar Account"}},
        kind=command_kind(RunScript("() => ({})")),
        tool_name=EVALUATE,
        tool_args={"target": "document"},
    )

    assert state["structured_evidence"] == []
    assert state["field_coverage"] == []


def test_missing_evaluate_value_closes_slot_as_unavailable() -> None:
    state = BrowserRuntimeRail._build_phase_state("返回商品评分")

    BrowserRuntimeRail._record_structured_evidence(
        state,
        {"ok": True, "generation_id": "g3", "result": {"productRating": None}},
        kind=command_kind(RunScript("() => ({})")),
        tool_name=EVALUATE,
        tool_args={"target": ".product-rating"},
    )

    assert BrowserRuntimeRail._missing_evidence_slots(state) == []
    assert BrowserRuntimeRail._unavailable_evidence_slots(state) == [
        {
            "entity": "product",
            "variant": "default",
            "field": "product_rating",
            "status": "missing",
        }
    ]
    assert state["evidence_slots"][0]["source"] == "browser_evaluate"
    assert state["evidence_slots"][0]["generation"] == "g3"


def test_interactive_probe_sort_evidence_keeps_selection_source() -> None:
    evidence = BrowserRuntimeRail._interactive_probe_evidence(
        {
            "generation_id": "g5",
            "elements": [
                {
                    "target_id": "t_g5_sort_1",
                    "generation_id": "g5",
                    "kind": "sort_tab",
                    "region": "sort",
                    "text": "销量",
                    "selected": True,
                    "selected_source": "url-param",
                    "actionable": True,
                }
            ],
        }
    )

    assert evidence["values"] == {"sort_state": "销量"}
    assert evidence["provenance"]["sort_state"] == {
        "source": "browser_probe_interactives",
        "selector": "t_g5_sort_1",
        "raw_text": "销量",
        "generation_id": "g5",
        "selection_source": "url-param",
    }


def test_probe_contract_exposes_result_count_and_bounds_conflict_fallback() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("返回前3条搜索结果")
    session.update_state({"__browser_phase_budget_state__": state})
    first = {
        "observed_count": 1,
        "generation_id": "g1",
        "cards": [
            {
                "title": "Result",
                "primary_link": "https://example.test/1",
                "region": "main_result",
                "kind": "result",
                "is_ad": False,
            }
        ],
        "page_state": {},
    }
    second = {
        "observed_count": 0,
        "generation_id": "g1",
        "cards": [
            {
                "title": "Result",
                "primary_link": "https://example.test/1",
                "region": "sponsored_result",
                "kind": "promotion",
                "is_ad": True,
            }
        ],
        "page_state": {},
    }

    BrowserRuntimeRail._enrich_probe_result_contract(
        session,
        SimpleNamespace(tool_msg=None),
        "browser_probe_cards",
        first,
    )
    BrowserRuntimeRail._enrich_probe_result_contract(
        session,
        SimpleNamespace(tool_msg=None),
        "browser_probe_cards",
        second,
    )

    assert first["requested_count"] == 3
    assert first["page_state"] == {"requested_count": 3, "observed_count": 1}
    assert second["diagnostics"]["classification_conflict"] is True
    assert second["diagnostics"]["recommended_fallback"] == "one_precise_probe"
    assert second["diagnostics"]["precise_fallback_remaining"] == 0
    assert second["page_state"]["classification_conflict"] is True
    assert session.get_state("__browser_phase_budget_state__")[
        "probe_classification_fallback_count"
    ] == 1


def test_generation_or_selector_changes_do_not_create_new_semantic_evidence() -> None:
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("extract title")})
    kind = command_kind(RunScript("() => document.title"))
    first = BrowserRuntimeRail._record_phase_result(
        session,
        kind,
        EVALUATE,
        {"function": "() => document.title", "target": "ref=e1"},
        {"ok": True, "result": {"title": "Example"}, "generation_id": "g1"},
    )
    second = BrowserRuntimeRail._record_phase_result(
        session,
        kind,
        EVALUATE,
        {"function": "() => document.title", "target": "ref=e9"},
        {"ok": True, "result": {"title": "Example"}, "generation_id": "g2"},
    )

    task_state = session.get_state("__browser_phase_budget_state__")
    assert first["evidence_added"] is True
    assert second["evidence_added"] is False
    assert len(task_state["structured_evidence"]) == 1


def test_recent_action_window_is_runtime_owned_and_bounded_to_six() -> None:
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("inspect results")})
    for index in range(8):
        BrowserRuntimeRail._record_recent_action(
            session,
            kind=command_kind(Click(DriverRef(f"e{index}", "g0"))),
            tool_name=CLICK,
            tool_args={"target_id": f"e{index}"},
            tool_result={"ok": True},
            action_class="interaction",
            elapsed_ms=index,
            progress_delta={"phase": "navigation", "success": True},
        )

    actions = session.get_state("__browser_phase_budget_state__")["recent_actions"]
    assert [action["seq"] for action in actions] == [3, 4, 5, 6, 7, 8]


def test_same_price_interval_cannot_be_visited_twice() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Filter products from 100 to 200 yuan")
    state["current_phase"] = "filtering"
    state["phases"]["filtering"]["visited_price_intervals"] = ["100:200"]
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="already visited"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            BATCH,
            "browser_batch_interact",
            {
                "steps": [
                    {"op": "fill", "name": "minimum price", "value": "100"},
                    {"op": "fill", "name": "maximum price", "value": "200"},
                ]
            },
        )


def test_structured_extraction_records_task_scoped_evidence() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract product title and price")
    state["current_phase"] = "extraction"
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._record_phase_result(
        session,
        BATCH,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "title"}]},
        {
            "ok": True,
            "generation_id": "g5",
            "extracted": {"title": "Headphones", "price": "$99"},
            "field_provenance": {
                "title": {
                    "selector": "h2.product-title",
                    "raw_text": "Headphones",
                    "generation_id": "g5",
                }
            },
        },
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["field_coverage"] == ["price", "title"]
    assert updated["structured_evidence"][0]["kind"] == "structured_extraction"
    assert updated["structured_evidence"][0]["generation_id"] == "g5"
    assert updated["structured_evidence"][0]["provenance"]["title"]["selector"] == "h2.product-title"

    prompt_evidence = BrowserWorkingContextStore.compact_evidence(updated["structured_evidence"])
    assert prompt_evidence[0]["values"] == {"title": "Headphones", "price": "$99"}
    assert prompt_evidence[0]["provenance"]["title"]["selector"] == "h2.product-title"


def test_extraction_phase_waits_for_all_inferred_required_fields() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract product title and price")
    state["current_phase"] = "extraction"
    state["phases"]["navigation"]["status"] = "completed"
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._record_phase_result(
        session,
        BATCH,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "title"}]},
        {"ok": True, "extracted": {"title": "Headphones"}},
    )
    partial = session.get_state("__browser_phase_budget_state__")
    assert partial["phases"]["extraction"]["status"] == "in_progress"
    assert partial["phases"]["extraction"]["missing_fields"] == ["price"]

    BrowserRuntimeRail._record_phase_result(
        session,
        BATCH,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "price"}]},
        {"ok": True, "extracted": {"price": "$99"}},
    )
    completed = session.get_state("__browser_phase_budget_state__")
    assert completed["phases"]["extraction"]["status"] == "completed"
    assert completed["status"] == "completed"


def test_known_url_gate_allows_extraction_when_browser_is_already_on_target() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Open https://example.test/article?id=7 and extract the title")
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        PROBE,
        "browser_probe_cards",
        {},
        current_page_state={"url": "https://example.test/article?id=7", "title": "Article"},
    )

    assert action_class == "structured_extraction"
    assert session.get_state("__browser_phase_budget_state__")["phases"]["extraction"]["attempts"] == 1


def test_known_url_gate_guides_once_then_leaves_recovery_to_model() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Open https://example.test and extract the title")
    session.update_state({"__browser_phase_budget_state__": state})

    with pytest.raises(ValueError, match="https://example.test"):
        BrowserRuntimeRail._consume_phase_budget(
            session,
            PROBE,
            "browser_probe_cards",
            {},
            current_page_state={"url": "about:blank"},
        )

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        PROBE,
        "browser_probe_cards",
        {},
        current_page_state={"url": "about:blank"},
    )
    updated = session.get_state("__browser_phase_budget_state__")
    assert action_class == "structured_extraction"
    assert updated["direct_navigation_guidance_count"] == 1
    assert updated["phases"]["extraction"]["attempts"] == 1


def test_known_root_url_gate_accepts_deeper_same_site_page() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Open https://example.test and return the title")
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        PROBE,
        "browser_probe_cards",
        {},
        current_page_state={"url": "https://example.test/search?q=title"},
    )

    assert action_class == "structured_extraction"


def test_replan_fingerprint_allows_a_different_requested_field() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title and price")
    state["last_page"] = {"url": "https://example.test/item/7"}
    title_args = {
        "function": "() => ({title: document.title})",
        "target": "ref=e1",
        "field": "title",
    }
    title_strategy = BrowserRuntimeRail._strategy_fingerprint(
        state,
        command_kind(RunScript(title_args["function"])),
        title_args,
        "script_exploration",
    )
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": title_strategy,
            "failed_strategies": [title_strategy],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        command_kind(RunScript("() => ({price: document.querySelector('.price')?.textContent})")),
        EVALUATE,
        {
            "function": "() => ({price: document.querySelector('.price')?.textContent})",
            "target": "ref=e9",
            "field": "price",
        },
    )

    assert action_class == "script_exploration"
    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "replan_trial"
    assert updated["replan_count"] == 1


def test_strategy_fingerprint_tracks_semantic_target_without_generation_noise() -> None:
    state = BrowserRuntimeRail._build_phase_state("Compare the titles")
    first_page_state = {
        "url": "https://example.test/results",
        "interactives": [
            {"target_id": "t_g2_1", "role": "link", "text": "First result"},
            {"target_id": "t_g2_2", "role": "link", "text": "Second result"},
        ],
    }
    refreshed_page_state = {
        "url": "https://example.test/results",
        "interactives": [
            {"target_id": "t_g3_9", "role": "link", "text": "First result"},
        ],
    }

    first = BrowserRuntimeRail._strategy_fingerprint(
        state,
        BATCH,
        {"steps": [{"op": "extract_text", "target_id": "t_g2_1", "field": "title"}]},
        "structured_extraction",
        first_page_state,
    )
    second = BrowserRuntimeRail._strategy_fingerprint(
        state,
        BATCH,
        {"steps": [{"op": "extract_text", "target_id": "t_g2_2", "field": "title"}]},
        "structured_extraction",
        first_page_state,
    )
    refreshed_first = BrowserRuntimeRail._strategy_fingerprint(
        state,
        BATCH,
        {"steps": [{"op": "extract_text", "target_id": "t_g3_9", "field": "title"}]},
        "structured_extraction",
        refreshed_page_state,
    )

    assert first != second
    assert first == refreshed_first


def test_repeated_replan_denials_become_terminal_after_finite_budget() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    args = {"function": "() => document.querySelector('button').click()", "target": "ref=e1"}
    kind = command_kind(RunScript(args["function"]))
    strategy = BrowserRuntimeRail._strategy_fingerprint(
        state,
        kind,
        args,
        "script_exploration",
    )
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": strategy,
            "failed_strategies": [strategy],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    for _ in range(3):
        with pytest.raises(ValueError, match="Semantic loop detected"):
            BrowserRuntimeRail._consume_phase_budget(session, kind, EVALUATE, args)

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "blocked"
    assert updated["replan_denial_count"] == 3
    assert "semantic_replan_denial_budget_exhausted" in updated["blockers"]


def test_replan_allows_one_bounded_read_only_evidence_recovery() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    args = {"function": "() => ({title: document.title})", "field": "title"}
    kind = command_kind(RunScript(args["function"]))
    strategy = BrowserRuntimeRail._strategy_fingerprint(
        state,
        kind,
        args,
        "script_exploration",
    )
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "blocked_strategy": strategy,
            "failed_strategies": [strategy],
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(session, kind, EVALUATE, args)

    updated = session.get_state("__browser_phase_budget_state__")
    assert action_class == "script_exploration"
    assert updated["status"] == "replan_trial"
    assert updated["read_only_recovery_counts"][strategy] == 1


def test_offload_recall_does_not_consume_browser_phase_or_replan_budget() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    state.update(
        {
            "status": "replan_required",
            "replan_required": True,
            "replan_count": 2,
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    action_class = BrowserRuntimeRail._consume_phase_budget(
        session,
        RECALL,
        "browser_recall_offload",
        {"handle": "audit-1", "query": "title"},
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert action_class == "other"
    assert all(details["attempts"] == 0 for details in updated["phases"].values())
    assert updated["replan_count"] == 2


def test_new_structured_evidence_recovers_pending_replan_trial() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    state.update(
        {
            "status": "replan_trial",
            "replan_required": True,
            "replan_trial_pending": True,
            "trial_strategy": "title-extraction",
        }
    )
    session.update_state({"__browser_phase_budget_state__": state})

    result = BrowserRuntimeRail._record_phase_result(
        session,
        BATCH,
        "browser_batch_interact",
        {"steps": [{"op": "extract_text", "field": "title"}]},
        {"ok": True, "extracted": {"title": "Verified title"}},
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert result["recovered"] is True
    assert updated["status"] == "in_progress"
    assert updated["replan_required"] is False
    assert updated["replan_trial_pending"] is False


def test_partial_batch_keeps_successful_extraction_without_marking_batch_success() -> None:
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Extract the title and price")
    session.update_state({"__browser_phase_budget_state__": state})

    result = BrowserRuntimeRail._record_phase_result(
        session,
        BATCH,
        "browser_batch_interact",
        {
            "steps": [
                {"op": "extract_text", "field": "title"},
                {"op": "extract_text", "field": "price"},
            ]
        },
        {
            "ok": False,
            "status": "partial",
            "steps": [
                {"index": 0, "op": "extract_text", "ok": True},
                {"index": 1, "op": "extract_text", "ok": False, "error": "not found"},
            ],
            "extracted": {"title": "Verified title"},
        },
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert result["success"] is False
    assert result["evidence_added"] is True
    assert "title" in updated["field_coverage"]
    assert "price" not in updated["field_coverage"]


def test_price_interval_ignores_evaluate_script_numbers() -> None:
    assert BrowserRuntimeRail._price_interval_signature(
        command_kind(RunScript("() => [...document.querySelectorAll('.price')].slice(1, 2)")),
        {"function": "() => [...document.querySelectorAll('.price')].slice(1, 2)"},
    ) == ""
    assert BrowserRuntimeRail._price_interval_signature(
        BATCH,
        {
            "steps": [
                {"op": "fill", "field": "minimum price", "value": "100"},
                {"op": "fill", "field": "maximum price", "value": "200"},
            ]
        },
    ) == "100:200"


def test_required_field_ontology_and_generic_evidence_slots_keep_provenance() -> None:
    state = BrowserRuntimeRail._build_phase_state(
        "提取作者、点赞数、收藏数、评论数、店铺、时长、最高温、最低温和排序状态"
    )
    expected_fields = {
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
    assert set(state["required_fields"]) == expected_fields
    assert {slot["field"] for slot in state["required_evidence_slots"]} == expected_fields

    BrowserRuntimeRail._record_structured_evidence(
        state,
        {
            "generation_id": "g8",
            "extracted": {"作者": "Alice", "点赞数": "123"},
            "field_provenance": {
                "作者": {
                    "selector": ".author",
                    "raw_text": "作者 Alice",
                    "generation_id": "g8",
                    "source": "browser_batch_interact",
                },
                "点赞数": {
                    "selector": ".likes",
                    "raw_text": "点赞 123",
                    "generation_id": "g8",
                    "source": "browser_batch_interact",
                },
            },
        },
        kind=BATCH,
        tool_name="browser_batch_interact",
        tool_args={},
    )

    slots = {slot["field"]: slot for slot in state["evidence_slots"]}
    assert slots["author"]["selector"] == ".author"
    assert slots["author"]["raw_text"] == "作者 Alice"
    assert slots["author"]["generation"] == "g8"
    assert slots["likes"]["source"] == "browser_batch_interact"
    assert BrowserRuntimeRail._infer_required_fields("提取商品评分和店铺评分") == [
        "product_rating",
        "shop_rating",
    ]


def test_required_fields_use_requested_output_clause_not_operation_preconditions() -> None:
    task = "打开淘宝搜索机械键盘，可以按价格或销量排序，返回第一条商品标题"
    assert BrowserRuntimeRail._infer_required_fields(task) == ["title"]
    assert BrowserRuntimeRail._infer_required_fields(
        "打开百度查看天气，返回最高温和最低温"
    ) == ["high_temperature", "low_temperature"]
    assert BrowserRuntimeRail._infer_required_fields("查询美元兑人民币并返回汇率") == ["exchange_rate"]
    assert BrowserRuntimeRail._infer_required_fields("搜索 Python 视频，返回播放量") == ["views"]
    assert BrowserRuntimeRail._infer_required_fields("打开淘宝把蓝牙耳机加入购物车") == []


def test_cart_action_feedback_is_sufficient_completion_evidence() -> None:
    state = BrowserRuntimeRail._build_phase_state("打开淘宝把蓝牙耳机加入购物车")
    BrowserWorkingContextStore._merge_semantic_evidence(
        state,
        {
            "semantic_state": {
                "url": "https://item.taobao.com/item.htm?id=1",
                "generation_id": "g1",
                "action_feedback": [{"value": "已加入购物车"}],
            }
        },
    )
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})

    BrowserRuntimeRail._apply_worker_progress_to_task_state(
        session,
        {"status": "completed"},
        "商品已加入购物车。",
    )

    updated = session.get_state("__browser_phase_budget_state__")
    assert updated["status"] == "completed"
    assert updated["terminal_reason"] == "runtime_completion_validated"
    assert updated["structured_evidence"][-1]["fields"] == ["action_confirmation"]


def test_large_evaluate_observation_is_bounded() -> None:
    rail = BrowserRuntimeRail(_mock_runtime())
    tool_message = ToolMessage(
        content=json.dumps({"value": "x" * 20_000}),
        tool_call_id="evaluate-1",
    )
    inputs = SimpleNamespace(tool_msg=tool_message)
    tool_result = {"ok": True, "value": "x" * 20_000}

    rail._compact_large_observation_message(
        inputs,
        command_kind(RunScript("() => 1")),
        EVALUATE,
        tool_result,
    )

    payload = json.loads(tool_message.content)
    assert len(tool_message.content) <= 12_000
    assert payload["observation"] == "bounded_browser_tool_result"
    assert payload["original_chars"] > 20_000


def test_comprehensive_and_latest_create_distinct_title_slots_without_compare_word() -> None:
    slots = BrowserRuntimeRail._infer_required_evidence_slots(
        "在B站分别切换综合排序和最新发布，返回两个标题"
    )
    assert slots == [
        {"entity": "bilibili_search_result", "variant": "comprehensive", "field": "title"},
        {"entity": "bilibili_search_result", "variant": "latest", "field": "title"},
    ]


def test_terminal_state_allows_one_tool_disabled_synthesis_then_force_finishes() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {}
    runtime.service = MagicMock()
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    state.update({"status": "partial", "blockers": ["missing_required_field:title"]})
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    agent = MagicMock()
    agent.system_prompt_builder = SystemPromptBuilder(language="en")
    first_ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs=ModelCallInputs(tools=[MagicMock()]),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(first_ctx))

    assert first_ctx.inputs.tools == []
    assert first_ctx.consume_force_finish() is None
    assert "Do not call tools" in agent.system_prompt_builder.build()

    second_ctx = AgentCallbackContext(agent=agent, session=session, inputs=ModelCallInputs(tools=[]))
    _run(rail.before_model_call(second_ctx))
    finish = second_ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["authoritative_browser_result"]["status"] == "partial"


def test_unfinished_text_tool_intent_retries_once_in_same_task() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    state = BrowserRuntimeRail._build_phase_state("Extract the title")
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    response = SimpleNamespace(
        content="让我继续调用 browser_evaluate 获取标题",
        tool_calls=[],
    )
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=ModelCallInputs(response=response),
    )
    ctx.extra["__browser_task_deadline__"] = runtime_module.time.monotonic() + 60
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_model_call(ctx))

    assert ctx.has_pending_steering() is True
    assert ctx.consume_model_continue_request() is True
    assert ctx.consume_model_continue_request() is False
    assert session.get_state("__browser_phase_budget_state__")["model_protocol_retry_count"] == 1
    assert ctx.consume_force_finish() is None

    _run(rail.after_model_call(ctx))
    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["authoritative_browser_result"]["terminal_reason"] == "model_tool_protocol_error"


def test_invocation_slice_returns_retryable_partial_with_shared_time_remaining() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    state = BrowserRuntimeRail._build_phase_state("比较两个商品并返回标题和价格")
    state["structured_evidence"] = [{"fields": ["price"], "values": {"price": "100"}}]
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)
    rail = BrowserRuntimeRail(runtime)
    rail._bind_shared_task_deadline(ctx, session, state)
    shared_deadline = state["deadline_at"]
    ctx.extra["__browser_invocation_deadline__"] = runtime_module.time.monotonic() - 1

    assert rail._finish_if_task_deadline_exhausted(ctx, session, state) is True

    finish = ctx.consume_force_finish()
    assert finish is not None
    result = finish.result["authoritative_browser_result"]
    assert result["status"] == "partial"
    assert result["retryable"] is True
    assert result["terminal_reason"] == "task_invocation_slice_exhausted"
    assert state["deadline_at"] == shared_deadline
    assert state["deadline_remaining_s"] > 0


def test_resume_keeps_shared_deadline_and_gets_a_fresh_invocation_slice() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.reset_semantic_task = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    state = BrowserRuntimeRail._build_phase_state("Compare two products and return their prices")
    state.update(
        {
            "status": "partial",
            "terminal_reason": "task_invocation_slice_exhausted",
            "blockers": ["task_invocation_slice_exhausted"],
            "structured_evidence": [{"fields": ["price"], "values": {"price": "100"}}],
        }
    )
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})
    first_ctx = AgentCallbackContext(agent=MagicMock(), session=session)
    rail._bind_shared_task_deadline(first_ctx, session, state)
    shared_deadline = state["deadline_at"]

    resumed = rail._resume_task_state(session, state, "Collect the missing second price")
    second_ctx = AgentCallbackContext(agent=MagicMock(), session=session)
    second_remaining = rail._bind_shared_task_deadline(second_ctx, session, resumed)

    assert resumed["deadline_at"] == shared_deadline
    assert resumed["resume_count"] == 1
    assert "task_invocation_slice_exhausted" not in resumed["blockers"]
    assert second_remaining > 0
    assert second_ctx.extra["__browser_invocation_deadline__"] > runtime_module.time.monotonic()


def test_structured_resume_preserves_original_slots_and_evidence() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    state = BrowserRuntimeRail._build_phase_state("返回商品标题和商品评分")
    state.update(
        {
            "status": "partial",
            "terminal_reason": "runtime_completion_requirements_missing",
            "field_coverage": ["title"],
            "evidence_slots": [
                {
                    "entity": "product",
                    "variant": "default",
                    "field": "title",
                    "value": "Keyboard",
                    "source": "browser_probe_cards",
                    "generation": "g2",
                }
            ],
        }
    )
    original_required_slots = list(state["required_evidence_slots"])
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": state})

    resumed = rail._ensure_task_state(
        session,
        "只补充缺失的商品评分，不要重新搜索",
        resume=True,
    )

    assert resumed["status"] == "in_progress"
    assert resumed["resume_count"] == 1
    assert resumed["required_evidence_slots"] == original_required_slots
    assert resumed["evidence_slots"][0]["value"] == "Keyboard"
    assert resumed["resume_instruction"] == "只补充缺失的商品评分，不要重新搜索"
    runtime.reset_semantic_task.assert_called_once()


def test_rail_registered_for_before_invoke_event() -> None:
    """get_callbacks() must return before_invoke so the framework fires it."""
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    rail = BrowserRuntimeRail(runtime)
    callbacks = rail.get_callbacks()
    assert AgentCallbackEvent.BEFORE_INVOKE in callbacks


def test_before_invoke_persists_current_query_for_continuation() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service = MagicMock()
    runtime.service.mcp_cfg = MagicMock()
    runtime.service._progress_by_session = {}
    session = _FakeSession()
    agent = MagicMock()
    agent.ability_manager = MagicMock()
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs=InvokeInputs(query="open example.com", conversation_id=session.get_session_id()),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_invoke(ctx))

    assert session.get_state("__browser_subagent_last_task__") == "open example.com"


def test_before_model_call_skips_dynamic_progress_without_attachment_manager() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    builder = SystemPromptBuilder(language="en")
    session = _FakeSession()
    session.update_state(
        {
            "__browser_subagent_progress_state__": {
                "status": "partial",
                "completed_steps": ["Opened home page"],
                "remaining_steps": ["Submit the form"],
                "next_step": "Fill the last required field",
                "completion_evidence": [],
                "missing_requirements": ["Need the user email"],
                "recent_tool_steps": ["browser_navigate: https://example.com"],
                "last_page": {"url": "https://example.com", "title": "Example"},
                "last_screenshot": None,
                "last_worker_final": "Waiting on the email field",
            }
        }
    )
    agent = MagicMock()
    agent.system_prompt_builder = builder
    ctx = AgentCallbackContext(agent=agent, session=session, inputs=InvokeInputs(query="continue"))
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(ctx))

    prompt = builder.build()
    assert "<browser_progress>{...}</browser_progress>" not in prompt
    assert "Opened home page" not in prompt
    assert not builder.has_section("browser_progress_continuation")


def test_before_model_call_explains_screenshot_is_disabled_without_image_support() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    builder = SystemPromptBuilder(language="en")
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=False, language="en")
    agent.system_prompt_builder = builder
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(AgentCallbackContext(agent=agent)))

    prompt = builder.build()
    assert "Image input is unavailable or unverified" in prompt
    assert "browser_take_screenshot" in prompt
    assert "op=screenshot" in prompt


def test_before_model_call_limits_screenshot_use_for_multimodal_model() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    builder = SystemPromptBuilder(language="en")
    agent = MagicMock()
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=True, language="en")
    agent.system_prompt_builder = builder
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(AgentCallbackContext(agent=agent)))

    prompt = builder.build()
    assert "The current model can inspect image input" in prompt
    assert "pixel-level visual evidence" in prompt


def test_before_model_call_initializes_runtime_task_state_without_progress_attachment() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    builder = SystemPromptBuilder(language="en")
    session = _FakeSession()
    agent = MagicMock()
    agent.system_prompt_builder = builder
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        inputs=ModelCallInputs(messages=[UserMessage(content="continue checkout")]),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.before_model_call(ctx))

    prompt = builder.build()
    assert "<browser_progress>{...}</browser_progress>" not in prompt
    task_state = session.get_state("__browser_phase_budget_state__")
    assert task_state["goal"] == "continue checkout"
    assert task_state["status"] == "in_progress"
    runtime.reset_semantic_task.assert_called_once()


def test_after_tool_call_records_browser_tool_progress() -> None:
    runtime = make_runtime(mcp_fake_driver(results=[result(url="https://example.com", title="Example")]))
    runtime.service.record_tool_progress = MagicMock()
    session = _FakeSession()
    session.update_state({"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("open example.com")})
    output = _invoke(runtime, NAVIGATE, {"url": "https://example.com"})
    ctx = _tool_ctx(runtime, NAVIGATE, {"url": "https://example.com"}, output, session=session)
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_tool_call(ctx))

    runtime.service.record_tool_progress.assert_not_called()
    task_state = session.get_state("__browser_phase_budget_state__")
    assert len(task_state["recent_actions"]) == 1
    assert task_state["recent_actions"][0]["action_class"] == "navigation"
    assert task_state["last_page"] == {"url": "https://example.com", "title": "Example"}
    assert session.get_state("__browser_subagent_progress_state__")["recent_tool_steps"]
    assert ctx.inputs.tool_msg.metadata == {
        "success": True,
        "executed": True,
        "state_changed": True,
        "denied": False,
        "effect": "navigate",
    }


def test_after_tool_call_does_not_advance_state_for_string_mcp_error() -> None:
    driver = mcp_fake_driver()
    driver.fail_next(DriverError("Element is not actionable", dispatched=True))
    runtime = make_runtime(driver)
    session = _FakeSession()
    session.update_state(
        {"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("Open the result")}
    )
    output = _invoke(runtime, CLICK, {"target": "e4"})
    ctx = _tool_ctx(runtime, CLICK, {"target": "e4"}, output, session=session)
    tool_message = ctx.inputs.tool_msg
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_tool_call(ctx))

    assert command_record(ctx.inputs.tool_result).ok is False
    assert tool_message.content.startswith("### Error")
    assert tool_message.metadata["success"] is False
    assert tool_message.metadata["executed"] is True
    assert tool_message.metadata["state_changed"] is False
    state = session.get_state("__browser_phase_budget_state__")
    assert state["recent_actions"][-1]["outcome_status"] == "failed"
    assert state["field_coverage"] == []


def test_after_tool_call_marks_mutating_timeout_as_ambiguous_for_reconciliation() -> None:
    driver = mcp_fake_driver()
    driver.fail_next(ActionTimeout("click timed out after 2500ms", dispatched=True))
    runtime = make_runtime(driver)
    session = _FakeSession()
    session.update_state(
        {"__browser_phase_budget_state__": BrowserRuntimeRail._build_phase_state("Open the result")}
    )
    output = _invoke(runtime, CLICK, {"target": "e4"})
    ctx = _tool_ctx(runtime, CLICK, {"target": "e4"}, output, session=session)
    tool_message = ctx.inputs.tool_msg
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_tool_call(ctx))

    assert tool_message.metadata["success"] is False
    assert tool_message.metadata["state_changed"] is True
    state = session.get_state("__browser_phase_budget_state__")
    assert state["recent_actions"][-1]["outcome_status"] == "ambiguous"
    assert state["recent_actions"][-1]["semantic_delta"] == "awaiting_observation"


def test_after_tool_call_copies_the_record_metadata_onto_the_tool_message() -> None:
    runtime = make_runtime(mcp_fake_driver(observations=[observation([element("e1")])]))
    output = _invoke(runtime, SNAPSHOT, {})
    ctx = _tool_ctx(runtime, SNAPSHOT, {}, output)

    _run(BrowserRuntimeRail(runtime).after_tool_call(ctx))

    expected = command_record(output).message_metadata | {"denied": False, "effect": "read"}
    assert ctx.inputs.tool_msg.metadata == expected


def test_after_tool_call_treats_a_policy_denial_as_not_executed() -> None:
    rail = BrowserRuntimeRail(_mock_runtime())
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name=CLICK,
            tool_args={"target": "e1"},
            tool_result=runtime_module.PolicyDenied("off limits"),
            tool_msg=ToolMessage(tool_call_id="denied", content="### Error\noff limits"),
        ),
    )

    _run(rail.after_tool_call(ctx))

    assert ctx.inputs.tool_msg.metadata["denied"] is True
    assert ctx.inputs.tool_msg.metadata["executed"] is False


def test_after_invoke_rewrites_max_iteration_with_failure_summary() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {
        "browser-session": MagicMock(
            last_page_url="https://example.com",
            last_page_title="Example",
            last_screenshot=None,
        )
    }
    runtime.service.export_progress_state = MagicMock(return_value={"status": "partial"})
    runtime.service.build_failure_summary = MagicMock(return_value="Failure summary for continuation:\n- step")
    session = _FakeSession()
    session.update_state(
        {
            "__browser_subagent_last_task__": "Finish the checkout flow",
            "__browser_subagent_progress_state__": {"status": "partial"},
        }
    )
    result = {"output": MAX_ITERATION_MESSAGE, "result_type": "error"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="Finish checkout", result=result),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_invoke(ctx))

    assert result["output"].startswith("Failure summary for continuation:")
    assert result["failure_summary"].startswith("Failure summary for continuation:")
    assert result["progress_state"]["status"] == "partial"


def test_after_invoke_renders_authoritative_max_iteration_result() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    session = _FakeSession()
    state = BrowserRuntimeRail._build_phase_state("Return the product title and price")
    state["field_coverage"] = ["title"]
    state["structured_evidence"] = [
        {"fields": ["title"], "values": {"title": "Keyboard"}}
    ]
    session.update_state({"__browser_phase_budget_state__": state})
    result = {"output": MAX_ITERATION_MESSAGE, "result_type": "error"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="Return product title and price", result=result),
    )

    _run(BrowserRuntimeRail(runtime).after_invoke(ctx))

    authoritative = result["authoritative_browser_result"]
    assert authoritative["status"] == "partial"
    assert authoritative["terminal_reason"] == "max_iterations_reached"
    assert "price" in authoritative["missing_fields"]
    assert "max_iterations_reached" in authoritative["blockers"]
    assert result["error"] == "browser_task_incomplete"


def test_after_invoke_promotes_completed_progress_block() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service.get_progress_state = MagicMock(return_value=MagicMock(is_empty=MagicMock(return_value=False)))
    runtime.service.record_worker_progress = MagicMock()
    runtime.service.export_progress_state = MagicMock(
        return_value={
            "status": "completed",
            "completion_evidence": ["Saved the settings page"],
        }
    )
    runtime.service.should_treat_as_completed = MagicMock(return_value=True)
    session = _FakeSession()
    result = {
        "output": (
            "Settings saved successfully.\n"
            '<browser_progress>{"status":"completed","completed_steps":["Opened settings"],'
            '"completion_evidence":["Saved the settings page"]}</browser_progress>'
        ),
        "result_type": "error",
    }
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="Save settings", result=result),
    )
    rail = BrowserRuntimeRail(runtime)

    _run(rail.after_invoke(ctx))

    assert result["result_type"] == "answer"
    assert result["output"] == "Settings saved successfully."
    assert session.get_state("__browser_subagent_progress_state__") == {}


def test_after_invoke_cannot_override_runtime_terminal_state_without_progress_block() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service.should_treat_as_completed.return_value = False
    runtime.service.build_failure_summary.return_value = "runtime blocked: captcha"
    state = BrowserRuntimeRail._build_phase_state("submit form")
    state.update(
        {
            "status": "blocked",
            "blockers": ["captcha"],
            "terminal_reason": "runtime_blocked",
        }
    )
    session = _FakeSession()
    session.update_state(
        {
            "__browser_phase_budget_state__": state,
            "__browser_subagent_last_task__": "submit form",
        }
    )
    result = {"output": "Done", "result_type": "answer"}
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        inputs=InvokeInputs(query="submit form", result=result),
    )

    _run(BrowserRuntimeRail(runtime).after_invoke(ctx))

    assert result["result_type"] == "error"
    assert result["failure_summary"] == "runtime blocked: captcha"
    assert session.get_state("__browser_phase_budget_state__")["blockers"] == ["captcha"]


def test_a_failing_status_logger_is_logged_and_stays_inside_the_rail() -> None:
    rail = BrowserRuntimeRail(_mock_runtime())
    error = RuntimeError("status sink closed")
    rail._status_logger = SimpleNamespace(before_invoke=MagicMock(side_effect=error))
    warn = runtime_module.browser_agent_log_warning

    with patch.object(runtime_module, "browser_agent_log_warning", wraps=warn) as warned:
        rail._emit_status("before_invoke", AgentCallbackContext(agent=MagicMock()))

    assert warned.call_args.args[1:] == ("before_invoke", error)
