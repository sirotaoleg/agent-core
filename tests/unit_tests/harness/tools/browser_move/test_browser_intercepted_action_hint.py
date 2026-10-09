# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Intercepted Playwright actions reach the model as a short, actionable error."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.foundation.tool import McpServerConfig, MCPTool, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import extract_mcp_tool_result_content
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.agents import build_browser_worker_agent
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.intercepted_action import (
    InterceptedActionHintRail,
    explain_intercepted_action,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService

CONSENT_HINT = "This is a cookie-consent dialog: close it first"
GENERIC_HINT = "If it is a dialog, banner or overlay, dismiss or close it first."
ONETRUST_TARGET = '<button type="button" class="css-bnpmeq" data-testid="filter-and-sort-button">…</button>'
ONETRUST_BLOCKER = (
    '<div class="onetrust-pc-dark-filter ot-fade-in"></div> from '
    '<div data-nosnippet="true" id="onetrust-consent-sdk">…</div> subtree'
)
AMAZON_RADIO = '<input name="cop" value="USD" type="radio"/>'


def _dim(line: str) -> str:
    return f"\x1b[2m{line}\x1b[22m"


def _call_log(locator: str, target: str, blocker: str, action: str = "click") -> str:
    """Playwright MCP error text as the worker receives it: ANSI-dimmed call log with retry blocks."""
    attempt = [
        "      - element is visible, enabled and stable",
        "      - scrolling into view if needed",
        "      - done scrolling",
        f"      - {blocker} intercepts pointer events",
    ]
    lines = [
        f"  - waiting for locator('{locator}')",
        f"    - locator resolved to {target}",
        f"  - attempting {action} action",
        "    2 × waiting for element to be visible, enabled and stable",
        *attempt,
    ]
    for wait_ms, repeats in ((20, 2), (100, 10), (500, 10)):
        lines += [f"    - retrying {action} action", f"    - waiting {wait_ms}ms"]
        lines += [f"    {repeats} × waiting for element to be visible, enabled and stable", *attempt]
    return (
        "### Error\nTimeoutError: browserBackend.callTool: Timeout 5000ms exceeded.\nCall log:\n"
        + "\n".join(_dim(line) for line in lines)
        + "\n"
    )


def _envelope(text: str) -> str:
    return json.dumps({"result": text}, ensure_ascii=False)


def _explained_text(content: str) -> str:
    explained = explain_intercepted_action(content)
    assert explained is not None
    return json.loads(explained)["result"]


ONETRUST = _envelope(_call_log('button:has-text("Filter & Sort")', ONETRUST_TARGET, ONETRUST_BLOCKER))


class TestExplainInterceptedAction:
    def test_onetrust_consent_backdrop(self) -> None:
        text = _explained_text(ONETRUST)

        assert text.startswith("### Error\nClick not delivered: ")
        assert 'button.css-bnpmeq[type="button"][data-testid="filter-and-sort-button"]' in text
        assert "div.onetrust-pc-dark-filter (inside div#onetrust-consent-sdk)" in text
        assert CONSENT_HINT in text
        assert len(text) <= 400
        assert "\x1b" not in text
        assert "retrying" not in text

    def test_amazon_redirect_modal_without_container(self) -> None:
        blocker = '<div id="redir-modal" class="redir-dismiss redir-modal-bg"></div>'
        text = _explained_text(_envelope(_call_log("aria-ref=e412", AMAZON_RADIO, blocker)))

        assert "is covered by div#redir-modal.redir-dismiss." in text
        assert "(inside" not in text
        assert GENERIC_HINT in text
        assert CONSENT_HINT not in text

    def test_amazon_radio_icon_suggests_label(self) -> None:
        blocker = '<i class="a-icon a-icon-radio"></i>'
        text = _explained_text(_envelope(_call_log("aria-ref=e412", AMAZON_RADIO, blocker)))

        assert 'input[name="cop"][type="radio"][value="USD"]' in text
        assert "is covered by i.a-icon." in text
        assert "click the control's visible label or text instead" in text

    def test_google_overlay_without_ids(self) -> None:
        blocker = '<div class="eLZYyf">…</div> from <div class="LSCOAf">…</div> subtree'
        target = '<a href="/search?q=x" class="result-link">…</a>'
        text = _explained_text(_envelope(_call_log("a >> nth=3", target, blocker)))

        assert "is covered by div.eLZYyf (inside div.LSCOAf)." in text
        assert GENERIC_HINT in text

    def test_aria_ref_locator_and_non_click_action(self) -> None:
        blocker = '<div id="redir-modal" class="redir-dismiss redir-modal-bg"></div>'
        text = _explained_text(_envelope(_call_log("aria-ref=f9e250", AMAZON_RADIO, blocker, action="hover")))

        assert text.startswith('### Error\nHover not delivered: input[name="cop"]')
        assert "is covered by div#redir-modal.redir-dismiss." in text

    def test_locator_names_the_target_when_it_never_resolved(self) -> None:
        blocker = '<div id="redir-modal" class="redir-dismiss redir-modal-bg"></div>'
        call_log = _call_log("aria-ref=f9e250", AMAZON_RADIO, blocker)
        call_log = "\n".join(line for line in call_log.splitlines() if "locator resolved to" not in line)

        text = _explained_text(_envelope(call_log))

        assert text.startswith("### Error\nClick not delivered: locator('aria-ref=f9e250') is covered by ")

    def test_plain_text_content_is_explained_without_envelope(self) -> None:
        explained = explain_intercepted_action(
            _call_log('button:has-text("Filter & Sort")', ONETRUST_TARGET, ONETRUST_BLOCKER)
        )

        assert explained is not None
        assert explained.startswith("### Error\nClick not delivered: ")

    @pytest.mark.parametrize(
        "content",
        [
            _envelope("### Error\nRef f1e136 not found in the current page snapshot. Try capturing new snapshot."),
            _envelope("### Error\nTimeoutError: browserBackend.callTool: Timeout 5000ms exceeded.\nCall log:\n"),
            "### Ran Playwright code\nawait page.getByRole('button').click();\n### Page\n- Page URL: https://x.test/",
            # The pattern is present but no call-log line names the blocker: left untouched.
            _envelope("### Error\nSomething intercepts pointer events, but not in a call log."),
        ],
        ids=["ref-not-found", "timeout-without-pattern", "success", "unparsable-pattern"],
    )
    def test_other_content_is_left_unchanged(self, content: str) -> None:
        assert explain_intercepted_action(content) is None


class TestInterceptedActionHintRail:
    def test_rewrites_only_the_model_facing_message(self) -> None:
        raw_result = {"success": False, "data": {"result": "raw call log"}}
        blocked = ToolMessage(content=ONETRUST, tool_call_id="call-1", name="browser_click")
        inputs = ToolCallInputs(tool_name="browser_click", tool_result=raw_result, tool_msg=blocked)

        asyncio.run(InterceptedActionHintRail().after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_result is raw_result
        assert inputs.tool_msg.content == json.dumps({"result": _explained_text(ONETRUST)}, ensure_ascii=False)
        assert (inputs.tool_msg.tool_call_id, inputs.tool_msg.name) == ("call-1", "browser_click")
        assert blocked.content == ONETRUST, "the original ToolMessage must not be mutated"

    @pytest.mark.parametrize(
        "tool_msg",
        [
            ToolMessage(content="### Ran Playwright code", tool_call_id="call-2"),
            ToolMessage(content=[{"type": "text", "text": "intercepts pointer events"}], tool_call_id="call-3"),
            None,
        ],
        ids=["success", "non-str-content", "no-message"],
    )
    def test_leaves_other_messages_alone(self, tool_msg: ToolMessage | None) -> None:
        inputs = ToolCallInputs(tool_name="browser_click", tool_result={"ok": True}, tool_msg=tool_msg)

        asyncio.run(InterceptedActionHintRail().after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_msg is tool_msg


class _FakePlaywrightClient:
    """Returns what the Playwright MCP client extracts from an isError CallToolResult."""

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        del tool_name, arguments, kwargs
        raw = CallToolResult(isError=True, content=[TextContent(type="text", text=json.loads(ONETRUST)["result"])])
        return extract_mcp_tool_result_content(raw)


class _ScriptedLLM:
    """Clicks once, then finishes; records every request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            click = ToolCall(id="call-1", type="function", name="browser_click", arguments="{}")
            return AssistantMessage(content="", tool_calls=[click])
        return AssistantMessage(content='{"ok": false, "final": "blocked", "page": {}, "screenshot": null}')


def test_worker_model_sees_compact_error_and_observer_sees_raw_result() -> None:
    observed: list[tuple[str, Any]] = []

    async def observer(tool_name: str, tool_result: Any) -> None:
        observed.append((tool_name, tool_result))

    async def scenario() -> list[dict[str, Any]]:
        worker = build_browser_worker_agent(
            provider="openai",
            api_key="mock-api-key",
            api_base="https://example.invalid/v1",
            model_name="mock-model",
            mcp_cfg=McpServerConfig(server_id="pw-test", server_name="pw-test", server_path="stdio://pw"),
            max_steps=3,
            tool_result_observer=observer,
            allowed_tool_names=(),
        )
        llm = _ScriptedLLM()
        worker.set_llm(llm)
        card = McpToolCard(id="intercepted-hint.browser_click", name="browser_click", server_name="pw-test")
        worker.ability_manager.add_ability(card, MCPTool(_FakePlaywrightClient(), card))
        await worker.register_rail(InterceptedActionHintRail())
        await Runner.run_agent(worker, {"query": "Open the filters", "conversation_id": "intercepted-hint"})
        return llm.requests

    requests = asyncio.run(scenario())

    assert len(requests) == 2
    tool_messages = [message for message in requests[1]["messages"] if getattr(message, "role", "") == "tool"]
    assert [message.content for message in tool_messages] == [
        json.dumps({"result": _explained_text(ONETRUST)}, ensure_ascii=False)
    ]
    ((tool_name, raw_result),) = observed
    assert tool_name == "browser_click"
    assert raw_result.success is False
    assert raw_result.error == json.loads(ONETRUST)["result"], "the observer must keep the raw Playwright call log"


def test_service_registers_the_rail_on_the_worker() -> None:
    service = BrowserService(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        mcp_cfg=McpServerConfig(server_id="pw-test", server_name="pw-test", server_path="stdio://pw"),
        guardrails=BrowserRunGuardrails(max_steps=2, max_failures=1, timeout_s=30),
    )
    worker = MagicMock()
    worker.register_rail = AsyncMock()
    module = "openjiuwen.harness.tools.browser_move.playwright_runtime.service"
    with (
        patch.object(service, "ensure_runtime_ready", AsyncMock()),
        patch(f"{module}.build_browser_worker_agent", return_value=worker),
    ):
        asyncio.run(service.ensure_started())

    rails = [call.args[0] for call in worker.register_rail.await_args_list]
    assert any(isinstance(rail, InterceptedActionHintRail) for rail in rails)


class TestInterceptedActionHintRailInputs:
    def test_rewrites_a_plain_text_error_without_envelope(self) -> None:
        plain = _call_log('button:has-text("Filter & Sort")', ONETRUST_TARGET, ONETRUST_BLOCKER)
        inputs = ToolCallInputs(
            tool_name="browser_click",
            tool_result={"ok": False},
            tool_msg=ToolMessage(content=plain, tool_call_id="call-4"),
        )

        asyncio.run(InterceptedActionHintRail().after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_msg.content == explain_intercepted_action(plain)
        assert inputs.tool_msg.content.startswith("### Error\nClick not delivered: ")
        assert CONSENT_HINT in inputs.tool_msg.content

    @pytest.mark.parametrize(
        "content",
        [
            _envelope("### Error\nSomething intercepts pointer events, but not in a call log."),
            "### Error\nSomething intercepts pointer events, but not in a call log.",
        ],
        ids=["envelope", "plain-text"],
    )
    def test_unparseable_call_log_is_left_unchanged(self, content: str) -> None:
        original = ToolMessage(content=content, tool_call_id="call-5")
        inputs = ToolCallInputs(tool_name="browser_click", tool_result={"ok": False}, tool_msg=original)

        asyncio.run(InterceptedActionHintRail().after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_msg is original
        assert inputs.tool_msg.content == content


@pytest.mark.parametrize("with_rail", [False, True], ids=["without-hint", "with-hint"])
def test_hint_adds_no_model_round_trip(with_rail: bool) -> None:
    """F_06 (plan 8.1): the hint replaces the tool message; the model is called once per turn either way."""

    async def scenario() -> list[dict[str, Any]]:
        worker = build_browser_worker_agent(
            provider="openai",
            api_key="mock-api-key",
            api_base="https://example.invalid/v1",
            model_name="mock-model",
            mcp_cfg=McpServerConfig(server_id="pw-test", server_name="pw-test", server_path="stdio://pw"),
            max_steps=3,
            allowed_tool_names=(),
        )
        llm = _ScriptedLLM()
        worker.set_llm(llm)
        card = McpToolCard(id="intercepted-hint-f06.browser_click", name="browser_click", server_name="pw-test")
        worker.ability_manager.add_ability(card, MCPTool(_FakePlaywrightClient(), card))
        if with_rail:
            await worker.register_rail(InterceptedActionHintRail())
        await Runner.run_agent(worker, {"query": "Open the filters", "conversation_id": f"f06-hint-{with_rail}"})
        return llm.requests

    requests = asyncio.run(scenario())

    assert len(requests) == 2
    (tool_message,) = [message for message in requests[1]["messages"] if getattr(message, "role", "") == "tool"]
    assert ("Click not delivered" in tool_message.content) is with_rail
