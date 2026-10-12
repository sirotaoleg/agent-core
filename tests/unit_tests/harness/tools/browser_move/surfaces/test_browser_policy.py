# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser surface's policy rules: no paste chord and no clipboard read reaches a page, on either dialect."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import PolicyDenied, PressKey, RunScript
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.runtime.rails.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.surfaces.browser.policy import CLIPBOARD_DENIED, policy_denial
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import create_browser_surface
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import BROWSER_TOOL_SPECS
from tests.unit_tests.harness.tools.browser_move.surfaces.test_browser_batch import DONE, _batch, _context, _sent

MCP_BUILDERS = {f"mcp_playwright-official_{name}": build for name, build in DECODERS.items()}
BROWSER_BUILDERS = {spec.name: spec.build for spec in BROWSER_TOOL_SPECS if spec.build is not None}
DIALECTS = [
    pytest.param(MCP_BUILDERS, "mcp_playwright-official_", id="playwright_mcp"),
    pytest.param(BROWSER_BUILDERS, "", id="browser_use"),
]
PASTE_KEYS = [
    "Control+v",
    "Control+V",
    "ctrl+v",
    "Meta+v",
    "Cmd+v",
    "ControlOrMeta+v",
    "Control+Shift+V",
    "Shift+Insert",
]
CLIPBOARD_SCRIPTS = [
    "() => navigator.clipboard.readText()",
    "async () => (await navigator.clipboard.read()).length",
    "() => navigator['clipboard']['readText']()",
    "() => { document.execCommand('paste'); return 1 }",
]


@pytest.mark.parametrize("key", PASTE_KEYS)
def test_paste_chords_are_denied(key: str) -> None:
    assert policy_denial(PressKey(key), None) == CLIPBOARD_DENIED


@pytest.mark.parametrize("key", ["Enter", "v", "Shift+v", "Alt+v", "Control+a", "Control+c", "Insert", "Control+Enter"])
def test_other_keys_pass(key: str) -> None:
    assert policy_denial(PressKey(key), None) is None


@pytest.mark.parametrize("code", CLIPBOARD_SCRIPTS)
def test_page_scripts_that_read_the_clipboard_are_denied(code: str) -> None:
    assert policy_denial(RunScript(code), None) == CLIPBOARD_DENIED


@pytest.mark.parametrize("code", ["() => document.title", "() => document.querySelector('input').value"])
def test_other_page_scripts_pass(code: str) -> None:
    assert policy_denial(RunScript(code), None) is None


def test_the_denial_tells_the_model_to_read_the_value_from_the_page() -> None:
    assert "clipboard" in CLIPBOARD_DENIED and "Read the value from the page instead" in CLIPBOARD_DENIED


def _rail_verdict(builders: dict[str, Any], tool: str, args: dict[str, Any]) -> AgentCallbackContext:
    profile = create_browser_surface({}).profile
    policy = ActionPolicy(builders, profile.policy_rules, BrowserPageState())
    tool_call = ToolCall(id="call-1", type="function", name=tool, arguments=json.dumps(args))
    ctx = AgentCallbackContext(
        agent=MagicMock(), inputs=ToolCallInputs(tool_call=tool_call, tool_name=tool, tool_args=args), extra={}
    )
    asyncio.run(policy.before_tool_call(ctx))
    return ctx


@pytest.mark.parametrize(("builders", "prefix"), DIALECTS)
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("browser_press_key", {"key": "Control+v"}),
        ("browser_press_key", {"key": "Shift+Insert"}),
        ("browser_evaluate", {"function": "() => navigator.clipboard.readText()"}),
    ],
)
def test_the_policy_rail_denies_clipboard_access_on_both_dialects(
    builders: dict[str, Any], prefix: str, tool: str, args: dict[str, Any]
) -> None:
    ctx = _rail_verdict(builders, prefix + tool, args)

    assert ctx.inputs.tool_msg.content == f"### Error\n{CLIPBOARD_DENIED}"
    assert isinstance(ctx.inputs.tool_result, PolicyDenied)
    assert ctx.extra["_skip_tool_calls"] == {"call-1": True}


@pytest.mark.parametrize(("builders", "prefix"), DIALECTS)
def test_the_policy_rail_lets_ordinary_keys_through_on_both_dialects(builders: dict[str, Any], prefix: str) -> None:
    ctx = _rail_verdict(builders, prefix + "browser_press_key", {"key": "Enter"})

    assert ctx.inputs.tool_msg is None
    assert "_skip_tool_calls" not in ctx.extra


@pytest.mark.parametrize("key", ["Control+v", "Shift+Insert"])
def test_a_batch_press_step_with_a_paste_chord_never_reaches_the_driver(key: str) -> None:
    context = _context(DONE)

    payload = _batch(context, [{"op": "press", "key": key}]).data["content"]

    assert _sent(context) == []
    assert payload["ok"] is False and payload["error"] == CLIPBOARD_DENIED


def test_a_batch_stops_at_a_paste_chord_after_the_steps_before_it() -> None:
    context = _context(DONE, DONE)

    payload = _batch(
        context, [{"op": "press", "key": "Tab"}, {"op": "press", "key": "Meta+v"}, {"op": "press", "key": "Enter"}]
    ).data["content"]

    assert _sent(context) == [PressKey("Tab")]
    assert [step["ok"] for step in payload["steps"]] == [True, False]
    assert payload["steps"][1]["error"] == CLIPBOARD_DENIED
