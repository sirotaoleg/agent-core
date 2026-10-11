# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Intercepted actions reach the model as a short, actionable error built from the command record."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import (
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    DriverError,
    ElementNotFound,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.runtime.agents import build_browser_worker_agent
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.intercepted_action import (
    InterceptedActionHintRail,
    explain_blocked_command,
)
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import command_record
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES

from .command_tools import command_tools, fake_driver, result, tool_message
from .runtime_fixtures import make_runtime

RETRY = MESSAGES["retry"]

CONSENT_HINT = "This is a cookie-consent dialog: close it first"
GENERIC_HINT = "If it is a dialog, banner or overlay, dismiss or close it first."
CALL_LOG = (
    "### Error\nTimeoutError: browserBackend.callTool: Timeout 5000ms exceeded.\nCall log:\n"
    "\x1b[2m  - attempting click action\x1b[22m\n"
    "\x1b[2m    - retrying click action\x1b[22m\n"
    '\x1b[2m      - <div class="onetrust-pc-dark-filter ot-fade-in"></div> intercepts pointer events\x1b[22m\n'
)
ONETRUST_BY = "div.onetrust-pc-dark-filter.ot-fade-in in div#onetrust-consent-sdk"
REDIRECT_BY = "div#redir-modal.redir-dismiss.redir-modal-bg"


def _blocked(by: str, message: str = CALL_LOG) -> ActionIntercepted:
    return ActionIntercepted(message, by=by)


def _output(name: str, arguments: dict[str, Any], answer: DriverError | ActionResult) -> tuple[Any, ToolOutput]:
    driver = fake_driver()
    if isinstance(answer, DriverError):
        driver.fail_next(answer)
    else:
        driver.script(results=[answer])
    tool = command_tools(driver)[name]
    return tool, asyncio.run(tool.invoke(arguments))


def _explained_text(name: str, arguments: dict[str, Any], answer: DriverError | ActionResult) -> str | None:
    _, output = _output(name, arguments, answer)
    return explain_blocked_command(command_record(output), RETRY)


def _inputs(name: str, arguments: dict[str, Any], answer: DriverError | ActionResult) -> ToolCallInputs:
    tool, output = _output(name, arguments, answer)
    return ToolCallInputs(tool_name=name, tool_result=output, tool_msg=tool_message(tool, output, "call-1"))


class TestExplainInterceptedAction:
    def test_onetrust_consent_backdrop(self) -> None:
        text = _explained_text("browser_click", {"target": "e57"}, _blocked(ONETRUST_BY))

        assert text.startswith("### Error\nClick not delivered: e57 is covered by ")
        assert f"is covered by {ONETRUST_BY}." in text
        assert CONSENT_HINT in text
        assert len(text) <= 400
        assert "\x1b" not in text
        assert "retrying" not in text

    def test_amazon_redirect_modal_without_container(self) -> None:
        text = _explained_text("browser_click", {"target": "e412"}, _blocked(REDIRECT_BY))

        assert f"is covered by {REDIRECT_BY}." in text
        assert " in div" not in text
        assert GENERIC_HINT in text
        assert CONSENT_HINT not in text

    def test_amazon_radio_icon_suggests_label(self) -> None:
        text = _explained_text("browser_click", {"target": "e412"}, _blocked("i.a-icon.a-icon-radio"))

        assert "Click not delivered: e412 is covered by i.a-icon.a-icon-radio." in text
        assert "click the control's visible label or text instead" in text

    def test_google_overlay_without_ids(self) -> None:
        text = _explained_text("browser_click", {"target": "e3"}, _blocked("div.eLZYyf in div.LSCOAf"))

        assert "is covered by div.eLZYyf in div.LSCOAf." in text
        assert GENERIC_HINT in text

    def test_aria_ref_locator_and_non_click_action(self) -> None:
        text = _explained_text("browser_hover", {"target": "f9e250"}, _blocked(REDIRECT_BY))

        assert text.startswith("### Error\nHover not delivered: f9e250 is covered by ")
        assert f"is covered by {REDIRECT_BY}." in text

    def test_locator_names_the_target_when_it_never_resolved(self) -> None:
        text = _explained_text("browser_click", {"target": 'button:has-text("Filter & Sort")'}, _blocked(REDIRECT_BY))

        assert text.startswith('### Error\nClick not delivered: button:has-text("Filter & Sort") is covered by ')

    def test_plain_text_content_is_explained_without_envelope(self) -> None:
        text = _explained_text("browser_click", {"target": "e57"}, _blocked(ONETRUST_BY))

        assert text.startswith("### Error\nClick not delivered: ")
        assert "Call log" not in text

    @pytest.mark.parametrize(
        "answer",
        [
            ElementNotFound("Ref f1e136 not found in the current page snapshot. Try capturing new snapshot."),
            ActionTimeout("TimeoutError: browserBackend.callTool: Timeout 5000ms exceeded.\nCall log:\n"),
            result(detail="### Ran Playwright code\nawait page.getByRole('button').click();"),
            # Intercepted, but the driver could not name the blocker: left untouched.
            _blocked(""),
        ],
        ids=["ref-not-found", "timeout-without-pattern", "success", "unparsable-pattern"],
    )
    def test_other_content_is_left_unchanged(self, answer: DriverError | ActionResult) -> None:
        assert _explained_text("browser_click", {"target": "e57"}, answer) is None


class TestInterceptedActionHintRail:
    def test_rewrites_only_the_model_facing_message(self) -> None:
        inputs = _inputs("browser_click", {"target": "e57"}, _blocked(ONETRUST_BY))
        raw_result, blocked = inputs.tool_result, inputs.tool_msg

        asyncio.run(InterceptedActionHintRail(RETRY).after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_result is raw_result
        assert raw_result.error == f"### Error\n{CALL_LOG}"
        assert inputs.tool_msg.content == explain_blocked_command(command_record(raw_result), RETRY)
        assert (inputs.tool_msg.tool_call_id, inputs.tool_msg.name) == ("call-1", "browser_click")
        assert blocked.content == f"### Error\n{CALL_LOG}", "the original ToolMessage must not be mutated"

    @pytest.mark.parametrize(
        ("answer", "tool_msg"),
        [
            (result(), ToolMessage(content="### Ran Playwright code", tool_call_id="call-2")),
            (
                _blocked(ONETRUST_BY),
                ToolMessage(content=[{"type": "text", "text": "intercepts pointer events"}], tool_call_id="call-3"),
            ),
            (_blocked(ONETRUST_BY), None),
        ],
        ids=["success", "non-str-content", "no-message"],
    )
    def test_leaves_other_messages_alone(
        self, answer: DriverError | ActionResult, tool_msg: ToolMessage | None
    ) -> None:
        _, output = _output("browser_click", {"target": "e57"}, answer)
        inputs = ToolCallInputs(tool_name="browser_click", tool_result=output, tool_msg=tool_msg)

        asyncio.run(InterceptedActionHintRail(RETRY).after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_msg is tool_msg


class _ScriptedLLM:
    """Clicks once, then finishes; records every request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            click = ToolCall(id="call-1", type="function", name="browser_click", arguments='{"target": "e57"}')
            return AssistantMessage(content="", tool_calls=[click])
        return AssistantMessage(content='{"ok": false, "final": "blocked", "page": {}, "screenshot": null}')


def _worker(llm: _ScriptedLLM, driver: FakeDriver, **kwargs: Any) -> Any:
    worker = build_browser_worker_agent(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        max_steps=3,
        driver_name="Playwright MCP",
        **kwargs,
    )
    worker.set_llm(llm)
    click = command_tools(driver)["browser_click"]
    worker.ability_manager.add_ability(click.card, click)
    return worker


def _intercepting_driver() -> FakeDriver:
    driver = fake_driver()
    driver.fail_next(_blocked(ONETRUST_BY))
    return driver


def test_worker_model_sees_compact_error_and_observer_sees_raw_result() -> None:
    observed: list[tuple[str, Any]] = []

    async def observer(tool_name: str, tool_result: Any) -> None:
        observed.append((tool_name, tool_result))

    async def scenario() -> list[dict[str, Any]]:
        llm = _ScriptedLLM()
        worker = _worker(llm, _intercepting_driver(), tool_result_observer=observer)
        await worker.register_rail(InterceptedActionHintRail(RETRY))
        await Runner.run_agent(worker, {"query": "Open the filters", "conversation_id": "intercepted-hint-records"})
        return llm.requests

    requests = asyncio.run(scenario())

    assert len(requests) == 2
    tool_messages = [message for message in requests[1]["messages"] if getattr(message, "role", "") == "tool"]
    ((tool_name, raw_result),) = observed
    explained = explain_blocked_command(command_record(raw_result), RETRY)
    assert [message.content for message in tool_messages] == [explained]
    assert tool_name == "browser_click"
    assert raw_result.success is False
    assert raw_result.error == f"### Error\n{CALL_LOG}", "the observer must keep the driver's own message"


def test_service_registers_the_rail_on_the_worker() -> None:
    runtime = make_runtime(image_support=None, core_settings=CoreSettings(driver="playwright_mcp"))
    worker = MagicMock()
    worker.register_rail = AsyncMock()
    module = "openjiuwen.harness.tools.browser_move.runtime.agents"
    with patch(f"{module}.build_browser_worker_agent", return_value=worker):
        asyncio.run(runtime.ensure_started())

    rails = [call.args[0] for call in worker.register_rail.await_args_list]
    assert any(isinstance(rail, InterceptedActionHintRail) for rail in rails)


class TestInterceptedActionHintRailInputs:
    def test_rewrites_a_plain_text_error_without_envelope(self) -> None:
        inputs = _inputs("browser_click", {"target": "e57"}, _blocked(ONETRUST_BY, "covered"))

        asyncio.run(InterceptedActionHintRail(RETRY).after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_msg.content == explain_blocked_command(command_record(inputs.tool_result), RETRY)
        assert inputs.tool_msg.content.startswith("### Error\nClick not delivered: ")
        assert CONSENT_HINT in inputs.tool_msg.content

    @pytest.mark.parametrize(
        "make",
        [lambda: _output("browser_click", {"target": "e57"}, _blocked("")), lambda: _undecoded()],
        ids=["no-blocker", "no-record"],
    )
    def test_unparseable_call_log_is_left_unchanged(self, make: Callable[[], tuple[Any, ToolOutput]]) -> None:
        tool, output = make()
        original = tool_message(tool, output, "call-5")
        inputs = ToolCallInputs(tool_name="browser_click", tool_result=output, tool_msg=original)

        asyncio.run(InterceptedActionHintRail(RETRY).after_tool_call(SimpleNamespace(inputs=inputs)))

        assert inputs.tool_msg is original


def _undecoded() -> tuple[Any, ToolOutput]:
    tool = command_tools(fake_driver())["browser_type"]
    return tool, asyncio.run(tool.invoke({"target": "e57"}))


@pytest.mark.parametrize("with_rail", [False, True], ids=["without-hint", "with-hint"])
def test_hint_adds_no_model_round_trip(with_rail: bool) -> None:
    async def scenario() -> list[dict[str, Any]]:
        llm = _ScriptedLLM()
        worker = _worker(llm, _intercepting_driver())
        if with_rail:
            await worker.register_rail(InterceptedActionHintRail(RETRY))
        await Runner.run_agent(
            worker, {"query": "Open the filters", "conversation_id": f"f06-hint-records-{with_rail}"}
        )
        return llm.requests

    requests = asyncio.run(scenario())

    assert len(requests) == 2
    (tool_message_,) = [message for message in requests[1]["messages"] if getattr(message, "role", "") == "tool"]
    assert ("Click not delivered" in tool_message_.content) is with_rail
