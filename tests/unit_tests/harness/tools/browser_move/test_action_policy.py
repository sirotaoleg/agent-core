# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ActionPolicy``: policy rules on decoded commands, with today's per-call denial and skip."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping
from unittest.mock import MagicMock

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    DriverRef,
    Observation,
    Observe,
    PolicyDenied,
    PolicyRule,
    RefResolver,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

NAVIGATE_TOOL = "mcp_playwright-official_browser_navigate"
CLICK_TOOL = "mcp_playwright-official_browser_click"
SHOP = BrowserContext(url="https://shop.test/", title="Shop")


def _navigate(args: Mapping[str, Any], refs: RefResolver) -> Navigate:
    return Navigate(str(args["url"]))


def _click(args: Mapping[str, Any], refs: RefResolver) -> Click:
    if not str(args.get("target", "")).startswith("e"):
        raise ValueError(f"not an element ref: {args.get('target')!r}")
    return Click(DriverRef(args["target"], refs.generation_of(args["target"])))


class _Recorder:
    def __init__(self) -> None:
        self.seen: list[tuple[Any, Any]] = []

    def check(self, action: Navigate, context: Any) -> str | None:
        self.seen.append((action, context))
        return None if action.url.startswith(context.url) else f"leaves {context.url}"


def _policy(recorder: _Recorder | None = None) -> tuple[ActionPolicy, BrowserPageState]:
    page_state = BrowserPageState(page_id="page-test")
    page_state.record(Observe(), Observation(context=MagicMock(id="0", title="Shop"), surface=SHOP))
    rules = [PolicyRule(Navigate, (recorder or _Recorder()).check)]
    return ActionPolicy({NAVIGATE_TOOL: _navigate, CLICK_TOOL: _click}, rules, page_state), page_state


def _context(tool_name: str, arguments: Any, call_id: str = "call-1") -> AgentCallbackContext:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    tool_call = ToolCall(id=call_id, type="function", name=tool_name, arguments=raw)
    return AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(tool_call=tool_call, tool_name=tool_name, tool_args=arguments),
        extra={},
    )


def test_the_rail_registers_its_before_tool_call_callback() -> None:
    policy, _ = _policy()

    assert set(policy.get_callbacks()) == {AgentCallbackEvent.BEFORE_TOOL_CALL}


def test_a_denied_command_gets_the_error_message_and_a_per_call_skip() -> None:
    policy, _ = _policy()
    ctx = _context(NAVIGATE_TOOL, '{"url": "https://elsewhere.test/"}')

    asyncio.run(policy.before_tool_call(ctx))

    assert ctx.inputs.tool_msg.content == "### Error\nleaves https://shop.test/"
    assert ctx.inputs.tool_msg.tool_call_id == "call-1"
    assert ctx.inputs.tool_msg.metadata == {"executed": False, "state_changed": False, "denied": True}
    assert isinstance(ctx.inputs.tool_result, PolicyDenied)
    assert ctx.inputs.tool_result.dispatched is False
    assert ctx.extra["_skip_tool_calls"] == {"call-1": True}
    assert "_skip_tool" not in ctx.extra


def test_an_allowed_command_passes_untouched() -> None:
    policy, _ = _policy()
    ctx = _context(NAVIGATE_TOOL, {"url": "https://shop.test/cart"})

    asyncio.run(policy.before_tool_call(ctx))

    assert ctx.inputs.tool_msg is None
    assert "_skip_tool_calls" not in ctx.extra


def test_the_rule_gets_the_decoded_command_and_the_current_surface_context() -> None:
    recorder = _Recorder()
    policy, page_state = _policy(recorder)

    asyncio.run(policy.before_tool_call(_context(NAVIGATE_TOOL, '{"url": "https://shop.test/a"}')))

    assert recorder.seen == [(Navigate("https://shop.test/a"), SHOP)]
    assert recorder.seen[0][1] is page_state.surface_context


def test_rules_of_other_commands_and_unknown_tools_are_not_consulted() -> None:
    recorder = _Recorder()
    policy, _ = _policy(recorder)

    asyncio.run(policy.before_tool_call(_context(CLICK_TOOL, '{"target": "e1"}')))
    asyncio.run(policy.before_tool_call(_context("read_file", '{"path": "x"}')))

    assert recorder.seen == []


def test_arguments_that_do_not_decode_are_denied() -> None:
    policy, _ = _policy()
    ctx = _context(CLICK_TOOL, '{"target": "#css"}')

    asyncio.run(policy.before_tool_call(ctx))

    assert ctx.inputs.tool_msg.content == "### Error\nnot an element ref: '#css'"
    assert ctx.extra["_skip_tool_calls"] == {"call-1": True}


def test_a_denied_call_never_reaches_the_driver() -> None:
    driver = FakeDriver(results=[ActionResult(ok=True)])
    policy, _ = _policy()
    manager = AbilityManager.__new__(AbilityManager)

    async def execute_tool(**_kwargs: Any) -> Any:
        return await driver.execute(Click(DriverRef("e1", "g0"))), None

    manager._execute_single_tool_call = execute_tool
    ctx = _context(NAVIGATE_TOOL, '{"url": "https://elsewhere.test/"}')

    async def run() -> Any:
        await policy.before_tool_call(ctx)
        raw_execute = AbilityManager._railed_execute_single_tool_call.__wrapped__
        return await raw_execute(manager, ctx, ctx.inputs.tool_call, MagicMock())

    result, message = asyncio.run(run())

    assert driver.calls == []
    assert isinstance(result, PolicyDenied)
    assert message.content.startswith("### Error\n")
