# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser worker stops looping on identical tool calls that return identical results."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.foundation.tool import McpServerConfig, MCPTool, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import extract_mcp_tool_result_content
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import ModelCallInputs, ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.agents import build_browser_worker_agent
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.repeated_call_guard import (
    RepeatedCallGuardRail,
    _is_noop_evaluate,
    _normalise_call_key,
    _result_fingerprint,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService

NOTE = "### Repeated call"
NOOP_WORDING = "only returned constants or the page title/URL"
FINAL_JSON = {
    "ok": True,
    "final": "Easy Vegetarian Spinach Lasagna: 4.6 stars, 117 ratings, serves 6.",
    "page": {"url": "https://www.allrecipes.com/recipe/easy-vegetarian-spinach-lasagna", "title": "Lasagna"},
    "screenshot": None,
    "error": None,
}
EVALUATE = "mcp_playwright-official_browser_evaluate"


def _playwright_response(result: str, code: str, *, call: int = 0, page_changed: bool = False) -> str:
    """A Playwright MCP 0.0.78 response; `call` varies only the parts the MCP makes volatile."""
    lines = ["### Result", result, "### Ran Playwright code", "```js", f"await page.evaluate({code!r});", "```"]
    if page_changed:
        lines += [
            "### Page",
            "- Page URL: https://www.allrecipes.com/recipe/easy-vegetarian-spinach-lasagna",
            "- Page Title: Easy Vegetarian Spinach Lasagna",
            f"- Console: {call} errors, {call * 3} warnings",
        ]
    if call:
        lines += [
            "### Events",
            f"- New console entries: .playwright-mcp/console-2026-09-18T10-{call:02d}.log#L1-L{call}",
        ]
    return "\n".join(lines)


class _Run:
    """Drives the rail the way one ReActAgent invoke does (tool contexts get a copy of extra)."""

    def __init__(self, rail: RepeatedCallGuardRail | None = None) -> None:
        self.rail = rail or RepeatedCallGuardRail()
        self.agent = SimpleNamespace(system_prompt_builder=SystemPromptBuilder())
        self.extra: dict[str, Any] = {}
        asyncio.run(self.rail.before_invoke(SimpleNamespace(agent=self.agent, extra=self.extra, inputs=None)))

    def call(self, name: str, arguments: dict[str, Any], content: str) -> ToolCallInputs:
        inputs = ToolCallInputs(
            tool_name=name,
            tool_args=json.dumps(arguments),
            tool_result={"result": content},
            tool_msg=ToolMessage(content=content, tool_call_id=f"call-{len(self.extra)}"),
        )
        ctx = SimpleNamespace(agent=self.agent, extra=dict(self.extra), inputs=inputs)
        asyncio.run(self.rail.after_tool_call(ctx))
        return inputs

    def repeat_count(self) -> int:
        return self.extra["_browser_repeated_call_guard"].repeat_count

    def evaluate(self, function: str, result: str) -> ToolCallInputs:
        return self.call(EVALUATE, {"function": function}, _playwright_response(result, function))

    def model_call(self) -> ModelCallInputs:
        inputs = ModelCallInputs(tools=[{"name": EVALUATE}])
        asyncio.run(self.rail.before_model_call(SimpleNamespace(agent=self.agent, extra=self.extra, inputs=inputs)))
        return inputs


class TestNormalisation:
    def test_whitespace_in_evaluate_function_is_ignored(self) -> None:
        assert _normalise_call_key(EVALUATE, '{"function": "() => 1"}') == _normalise_call_key(
            "browser_evaluate", '{"function": "()=>1"}'
        )

    def test_different_functions_differ(self) -> None:
        title = _normalise_call_key(EVALUATE, '{"function": "() => document.title"}')
        assert title != _normalise_call_key(EVALUATE, '{"function": "() => location.href"}')

    def test_argument_order_does_not_matter(self) -> None:
        assert _normalise_call_key("browser_click", '{"ref": "e1", "element": "Search"}') == _normalise_call_key(
            "browser_click", {"element": "Search", "ref": "e1"}
        )

    def test_whitespace_matters_outside_evaluate_functions(self) -> None:
        assert _normalise_call_key("browser_type", '{"text": "a b"}') != _normalise_call_key(
            "browser_type", '{"text": "ab"}'
        )

    def test_non_json_arguments_do_not_crash(self) -> None:
        assert _normalise_call_key(EVALUATE, "{not json") == _normalise_call_key(EVALUATE, "{not json")
        assert _normalise_call_key(EVALUATE, None) != _normalise_call_key(EVALUATE, "{not json")


class TestFingerprint:
    def test_volatile_events_and_console_counts_are_ignored(self) -> None:
        first = _playwright_response("1", "() => 1", call=1, page_changed=True)
        second = _playwright_response("1", "() => 1", call=7, page_changed=True)

        assert first != second
        assert _result_fingerprint(first) == _result_fingerprint(second)

    def test_different_results_differ(self) -> None:
        assert _result_fingerprint(_playwright_response('"ok"', "() => 'ok'")) != _result_fingerprint(
            _playwright_response('"done"', "() => 'ok'")
        )


class TestCounting:
    def test_note_on_third_and_fourth_identical_call(self) -> None:
        run = _Run()
        navigate = run.call("browser_navigate", {"url": "https://www.allrecipes.com"}, "### Ran Playwright code")
        counts = [run.repeat_count()]
        messages = [navigate.tool_msg.content]
        for call in range(1, 5):
            inputs = run.call(EVALUATE, {"function": "() => 1"}, _playwright_response("1", "() => 1", call=call))
            counts.append(run.repeat_count())
            messages.append(inputs.tool_msg.content)

        assert counts == [1, 1, 2, 3, 4]
        assert [NOTE in message for message in messages] == [False, False, False, True, True]
        assert "has returned the same result 3 times in a row" in messages[3]
        assert "has returned the same result 4 times in a row" in messages[4]
        assert messages[3].count(NOTE) == 1

    def test_same_key_with_different_result_resets(self) -> None:
        run = _Run()
        heading = "() => document.querySelector('h1').innerText"
        for _ in range(3):
            run.call(EVALUATE, {"function": heading}, _playwright_response('"A"', "t"))
        inputs = run.call(EVALUATE, {"function": heading}, _playwright_response('"B"', "t"))

        assert run.repeat_count() == 1
        assert NOTE not in inputs.tool_msg.content

    def test_different_key_resets(self) -> None:
        run = _Run()
        for _ in range(3):
            run.call(EVALUATE, {"function": "() => document.body.innerText.length"}, _playwright_response("1", "t"))
        run.call(EVALUATE, {"function": "() => document.body.innerHTML.length"}, _playwright_response("1", "t"))

        assert run.repeat_count() == 1

    def test_note_is_appended_inside_the_error_envelope(self) -> None:
        run = _Run()
        error = json.dumps({"result": "### Error\nRef e12 not found in the current page snapshot."})
        for _ in range(2):
            run.call("browser_click", {"ref": "e12"}, error)
        inputs = run.call("browser_click", {"ref": "e12"}, error)

        result = json.loads(inputs.tool_msg.content)["result"]
        assert result.startswith("### Error\nRef e12 not found")
        assert result.endswith("Otherwise take a different action.")

    def test_structured_result_is_untouched(self) -> None:
        run = _Run()
        content = _playwright_response("1", "() => 1")
        for _ in range(3):
            inputs = run.call(EVALUATE, {"function": "() => 1"}, content)

        assert inputs.tool_result == {"result": content}
        assert NOTE in inputs.tool_msg.content


class TestWithholdingTools:
    def test_tools_withheld_after_fifth_identical_call(self) -> None:
        run = _Run()
        withheld = []
        for _ in range(5):
            withheld.append(run.model_call().tools == [])
            run.call(EVALUATE, {"function": "() => 'ok'"}, _playwright_response('"ok"', "() => 'ok'"))

        inputs = run.model_call()

        assert withheld == [False] * 5
        assert inputs.tools == []
        assert "Tools are disabled for this reply" in run.agent.system_prompt_builder.build()

    def test_new_run_resets_state_and_finish_section(self) -> None:
        run = _Run()
        for _ in range(5):
            run.call(EVALUATE, {"function": "() => 'ok'"}, _playwright_response('"ok"', "() => 'ok'"))
        run.model_call()

        asyncio.run(run.rail.before_invoke(SimpleNamespace(agent=run.agent, extra=run.extra, inputs=None)))

        assert run.model_call().tools == [{"name": EVALUATE}]
        assert run.repeat_count() == 0
        assert not run.agent.system_prompt_builder.has_section("browser_repeated_call_finish")

    def test_loading_page_snapshots_are_not_repeats(self) -> None:
        run = _Run()
        for step in range(5):
            content = (
                f"### Page\n- Page URL: https://example.test/\n### Snapshot\n```yaml\n- progressbar: {step * 20}%\n```"
            )
            inputs = run.call("browser_snapshot", {}, content)
            assert NOTE not in inputs.tool_msg.content

        assert run.repeat_count() == 1
        assert run.model_call().tools == [{"name": EVALUATE}]


NOOP_FUNCTIONS = [
    "() => 1",
    "()=>34",
    "() => 'done19'",
    '()=>"ok"',
    "() => `x`",
    "async () => 0",
    "() => { return 'ok'; }",
    "() => document.title",
    "()=>location.href",
    "() => window.location.href",
    "() => document.URL",
    "() => ({u: location.href, t: document.title})",
    "() => [location.href, document.title]",
    "() => null",
]
PAGE_READING_FUNCTIONS = [
    "() => document.body.innerText.length",
    "() => document.querySelector('h1').innerText",
    "() => window.scrollBy(0, 800)",
    "() => document.readyState",
    "() => `${document.title}`",
    "() => location.href.includes('x')",
    "(el) => 1",
    "() => 'a' + document.body.innerText",
]


class TestNoopEvaluate:
    @pytest.mark.parametrize("function", NOOP_FUNCTIONS)
    def test_constant_and_title_or_url_functions_are_noops(self, function: str) -> None:
        assert _is_noop_evaluate(EVALUATE, json.dumps({"function": function}))

    @pytest.mark.parametrize("function", PAGE_READING_FUNCTIONS)
    def test_functions_that_read_or_change_the_page_are_not(self, function: str) -> None:
        assert not _is_noop_evaluate(EVALUATE, json.dumps({"function": function}))

    def test_other_tools_and_bad_arguments_are_not(self) -> None:
        assert not _is_noop_evaluate("browser_click", json.dumps({"function": "() => 1"}))
        assert not _is_noop_evaluate(EVALUATE, "{not json")
        assert not _is_noop_evaluate(EVALUATE, json.dumps({"element": "x"}))


class TestDigitInsensitiveEvaluate:
    @pytest.mark.parametrize(
        ("function_for", "result_for"),
        [
            (lambda n: f"()=>{31 + n}", lambda n: str(31 + n)),
            (lambda n: f"() => document.body.innerText.length+{n}", lambda n: str(6781 + n)),
        ],
        ids=["constant-counter", "innerText-length-plus-n"],
    )
    def test_counter_variants_count_as_one_streak(
        self, function_for: Callable[[int], str], result_for: Callable[[int], str]
    ) -> None:
        run = _Run()
        counts, noted = [], []
        for n in range(1, 6):
            inputs = run.evaluate(function_for(n), result_for(n))
            counts.append(run.repeat_count())
            noted.append(NOTE in inputs.tool_msg.content)

        assert counts == [1, 2, 3, 4, 5]
        assert noted == [False, False, True, True, True]
        assert run.model_call().tools == []

    def test_other_tools_still_compare_exactly(self) -> None:
        assert _normalise_call_key("browser_click", '{"target": "e12"}') != _normalise_call_key(
            "browser_click", '{"target": "e13"}'
        )
        run = _Run()
        for index in range(5):
            run.call("browser_click", {"target": f"e1{index}"}, "### Ran Playwright code")

        assert run.repeat_count() == 1
        assert run.model_call().tools == [{"name": EVALUATE}]


class TestNoopStreak:
    CAMBRIDGE = ["()=>location.href", "() => 'ok'", "() => 'done'", "() => document.title", "()=>1"]
    RESULTS = ['"https://dictionary.cambridge.org/"', '"ok"', '"done"', '"Cambridge Dictionary"', "1"]

    def test_mixed_noop_bodies_warn_and_withhold(self) -> None:
        run = _Run()
        messages = [run.evaluate(f, r).tool_msg.content for f, r in zip(self.CAMBRIDGE, self.RESULTS)]

        assert [NOTE in message for message in messages] == [False, False, True, True, True]
        assert all(NOOP_WORDING in message for message in messages[2:])
        assert "The last 3 calls only returned constants" in messages[2]
        assert "The last 5 calls only returned constants" in messages[4]
        assert run.model_call().tools == []
        assert "the same result 5 times in a row" in run.agent.system_prompt_builder.build()

    @pytest.mark.parametrize(
        ("name", "arguments"),
        [("browser_snapshot", {}), ("browser_click", {"target": "e7"})],
    )
    def test_any_other_call_resets_the_streak(self, name: str, arguments: dict[str, Any]) -> None:
        run = _Run()
        for function, result in zip(self.CAMBRIDGE[:2], self.RESULTS[:2]):
            run.evaluate(function, result)
        run.call(name, arguments, "### Page\n- Page URL: https://dictionary.cambridge.org/")
        messages = [run.evaluate(f, r).tool_msg.content for f, r in zip(self.CAMBRIDGE[2:], self.RESULTS[2:])]

        assert [NOTE in message for message in messages] == [False, False, True]
        assert run.model_call().tools == [{"name": EVALUATE}]


class TestNoFalsePositives:
    def test_reading_changing_page_content(self) -> None:
        run = _Run()
        price = "() => document.querySelector('.price').innerText"
        for text in ('"Laptop"', '"Phone"', '"Tablet"', '"Monitor"', '"Keyboard"'):
            assert NOTE not in run.evaluate(price, text).tool_msg.content

        assert run.model_call().tools == [{"name": EVALUATE}]

    def test_normal_navigation_sequence(self) -> None:
        run = _Run()
        steps = [
            run.call(
                "browser_navigate", {"url": "https://www.apple.com"}, "### Page\n- Page URL: https://www.apple.com/"
            ),
            run.evaluate("() => document.title", '"Apple"'),
            run.call("browser_click", {"target": "e21"}, "### Page\n- Page URL: https://www.apple.com/mac/"),
            run.evaluate("() => document.title", '"Mac - Apple"'),
        ]

        assert all(NOTE not in inputs.tool_msg.content for inputs in steps)
        assert run.model_call().tools == [{"name": EVALUATE}]


class _FakePlaywrightClient:
    """Answers every browser_evaluate like Playwright MCP, varying only the volatile parts."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        del tool_name, kwargs
        self.calls += 1
        function = arguments.get("function", "")
        literal = re.search(r"'([^']*)'", function)
        result = json.dumps(literal.group(1) if literal else "ok")
        text = _playwright_response(result, function, call=self.calls)
        return extract_mcp_tool_result_content(CallToolResult(content=[TextContent(type="text", text=text)]))


class _LoopingLLM:
    """Calls browser_evaluate while it has tools; answers with final JSON once it has none."""

    def __init__(self, function_for: Callable[[int], str] = lambda n: "() => 'ok'") -> None:
        self.requests: list[dict[str, Any]] = []
        self.function_for = function_for

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        if not kwargs.get("tools"):
            return AssistantMessage(content=json.dumps(FINAL_JSON))
        call = ToolCall(
            id=f"call-{len(self.requests)}",
            type="function",
            name="browser_evaluate",
            arguments=json.dumps({"function": self.function_for(len(self.requests))}),
        )
        return AssistantMessage(content="", tool_calls=[call])


def _build_worker(llm: _LoopingLLM, max_steps: int) -> Any:
    worker = build_browser_worker_agent(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        mcp_cfg=McpServerConfig(server_id="pw-test", server_name="pw-test", server_path="stdio://pw"),
        max_steps=max_steps,
        allowed_tool_names=(),
    )
    worker.set_llm(llm)
    card = McpToolCard(
        id="repeated-guard.browser_evaluate",
        name="browser_evaluate",
        server_name="pw-test",
        input_params={"type": "object", "properties": {"function": {"type": "string"}}},
    )
    worker.ability_manager.add_ability(card, MCPTool(_FakePlaywrightClient(), card))
    return worker


def test_worker_loop_ends_with_final_answer_once_tools_are_withheld() -> None:
    max_steps = 20

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        llm = _LoopingLLM()
        worker = _build_worker(llm, max_steps)
        await worker.register_rail(RepeatedCallGuardRail())
        result = await Runner.run_agent(worker, {"query": "Find a vegetarian lasagna", "conversation_id": "loop-1"})
        first_requests = list(llm.requests)
        llm.requests.clear()
        await Runner.run_agent(worker, {"query": "Find a vegetarian lasagna", "conversation_id": "loop-2"})
        return result, first_requests, list(llm.requests)

    result, requests, next_run_requests = asyncio.run(scenario())

    assert json.loads(result["output"]) == FINAL_JSON
    assert len(requests) == 6 < max_steps
    assert [bool(request["tools"]) for request in requests] == [True] * 5 + [False]
    tool_messages = [message.content for message in requests[-1]["messages"] if message.role == "tool"]
    assert [NOTE in message for message in tool_messages] == [False, False, True, True, True]
    system_prompt = requests[-1]["messages"][0].content
    assert "Tools are disabled for this reply" in system_prompt
    # The next run starts clean: tools are offered again and the finish instruction is gone.
    assert next_run_requests[0]["tools"]
    assert "Tools are disabled for this reply" not in next_run_requests[0]["messages"][0].content


def test_worker_loop_with_a_counter_in_the_argument_also_ends() -> None:
    max_steps = 20

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        llm = _LoopingLLM(lambda n: f"() => 'done{n}'")
        worker = _build_worker(llm, max_steps)
        await worker.register_rail(RepeatedCallGuardRail())
        result = await Runner.run_agent(worker, {"query": "Find a vegetarian lasagna", "conversation_id": "loop-3"})
        return result, list(llm.requests)

    result, requests = asyncio.run(scenario())

    assert json.loads(result["output"]) == FINAL_JSON
    assert len(requests) == 6 < max_steps
    assert [bool(request["tools"]) for request in requests] == [True] * 5 + [False]
    tool_messages = [message.content for message in requests[-1]["messages"] if message.role == "tool"]
    assert ['"done1"' in tool_messages[0], '"done5"' in tool_messages[4]] == [True, True]
    assert [NOTE in message for message in tool_messages] == [False, False, True, True, True]


def test_service_registers_the_guard_on_the_worker() -> None:
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
    assert any(isinstance(rail, RepeatedCallGuardRail) for rail in rails)


class _SameTurnLLM:
    """First turn: `count` identical browser_press_key calls in one message; then the final JSON."""

    def __init__(self, count: int) -> None:
        self.requests: list[dict[str, Any]] = []
        self.count = count

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        if len(self.requests) > 1:
            return AssistantMessage(content=json.dumps(FINAL_JSON))
        calls = [
            ToolCall(
                id=f"call-{index}",
                type="function",
                name="browser_press_key",
                arguments=json.dumps({"key": "PageDown"}),
            )
            for index in range(self.count)
        ]
        return AssistantMessage(content="", tool_calls=calls)


def test_identical_calls_issued_in_one_model_turn_withhold_tools() -> None:
    # F1: known false positive, deferred to benchmark phase (D6)
    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        llm = _SameTurnLLM(8)
        worker = _build_worker(llm, max_steps=20)
        client = _FakePlaywrightClient()
        card = McpToolCard(
            id="repeated-guard.browser_press_key",
            name="browser_press_key",
            server_name="pw-test",
            input_params={"type": "object", "properties": {"key": {"type": "string"}}},
        )
        worker.ability_manager.add_ability(card, MCPTool(client, card))
        await worker.register_rail(RepeatedCallGuardRail())
        result = await Runner.run_agent(worker, {"query": "Move the price slider", "conversation_id": "f1"})
        return result, list(llm.requests), client.calls

    result, requests, executed = asyncio.run(scenario())

    assert json.loads(result["output"]) == FINAL_JSON
    assert executed == 8
    assert len(requests) == 2
    assert requests[0]["tools"]
    assert not requests[1]["tools"], "the 8 same-turn calls count as a loop and tools are withheld"
    tool_messages = [message.content for message in requests[1]["messages"] if message.role == "tool"]
    assert [NOTE in message for message in tool_messages] == [False, False] + [True] * 6


class _RepeatThenAnswerLLM:
    """Sends the same browser_evaluate `repeats` times, then the final JSON."""

    def __init__(self, repeats: int) -> None:
        self.requests: list[dict[str, Any]] = []
        self.repeats = repeats

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        if len(self.requests) > self.repeats:
            return AssistantMessage(content=json.dumps(FINAL_JSON))
        call = ToolCall(
            id=f"call-{len(self.requests)}",
            type="function",
            name="browser_evaluate",
            arguments=json.dumps({"function": "() => 'ok'"}),
        )
        return AssistantMessage(content="", tool_calls=[call])


@pytest.mark.parametrize("with_rail", [False, True], ids=["without-guard", "with-guard"])
def test_guard_adds_no_model_round_trip(with_rail: bool) -> None:
    """F_06 (plan 8.1): the note rides on the tool message; the model is called once per turn either way."""

    async def scenario() -> list[dict[str, Any]]:
        llm = _RepeatThenAnswerLLM(4)
        worker = _build_worker(llm, max_steps=20)
        if with_rail:
            await worker.register_rail(RepeatedCallGuardRail())
        await Runner.run_agent(
            worker, {"query": "Find a vegetarian lasagna", "conversation_id": f"f06-guard-{with_rail}"}
        )
        return list(llm.requests)

    requests = asyncio.run(scenario())

    assert len(requests) == 5
    tool_messages = [message.content for message in requests[-1]["messages"] if message.role == "tool"]
    assert [NOTE in message for message in tool_messages] == ([False, False, True, True] if with_rail else [False] * 4)
