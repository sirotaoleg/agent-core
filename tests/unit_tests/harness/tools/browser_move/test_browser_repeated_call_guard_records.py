# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser worker stops looping on identical commands with identical results, read from command records."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import ModelCallInputs, ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    DriverRef,
    Element,
    ElementNotFound,
    Observation,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime import service as service_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.agents import build_browser_worker_agent
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.repeated_call_guard import (
    RepeatedCallGuardRail,
    _is_noop_script,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import command_record

from .command_tools import HOME, command_tools, fake_driver, result, tool_message
from .runtime_fixtures import fake_assembly, mcp_fake_driver

NOTE = "### Repeated call"
NOOP_WORDING = "only returned constants or the page title/URL"
FINAL_JSON = {
    "ok": True,
    "final": "Easy Vegetarian Spinach Lasagna: 4.6 stars, 117 ratings, serves 6.",
    "page": {"url": "https://www.allrecipes.com/recipe/easy-vegetarian-spinach-lasagna", "title": "Lasagna"},
    "screenshot": None,
    "error": None,
}
EVALUATE = "browser_evaluate"


def _events(call: int) -> str:
    return f"### Events\n- New console entries: .playwright-mcp/console-2026-09-18T10-{call:02d}.log#L1-L{call}"


class _Run:
    """Drives the rail the way one ReActAgent invoke does (tool contexts get a copy of extra)."""

    def __init__(self) -> None:
        self.rail = RepeatedCallGuardRail()
        self.agent = SimpleNamespace(system_prompt_builder=SystemPromptBuilder())
        self.extra: dict[str, Any] = {}
        self.driver = fake_driver()
        self.tools = command_tools(self.driver)
        self.calls = 0
        asyncio.run(self.rail.before_invoke(SimpleNamespace(agent=self.agent, extra=self.extra, inputs=None)))

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        answer: ActionResult | Observation | Exception | None = None,
        *,
        raw_arguments: str | None = None,
    ) -> ToolCallInputs:
        if isinstance(answer, Exception):
            self.driver.fail_next(answer)
        elif isinstance(answer, Observation):
            self.driver.script(observations=[answer])
        elif answer is not None:
            self.driver.script(results=[answer])
        tool = self.tools[name]
        output = asyncio.run(tool.invoke(arguments))
        self.calls += 1
        inputs = ToolCallInputs(
            tool_name=name,
            tool_args=json.dumps(arguments) if raw_arguments is None else raw_arguments,
            tool_result=output,
            tool_msg=tool_message(tool, output, f"call-{self.calls}"),
        )
        ctx = SimpleNamespace(agent=self.agent, extra=dict(self.extra), inputs=inputs)
        asyncio.run(self.rail.after_tool_call(ctx))
        return inputs

    def repeat_count(self) -> int:
        return self.extra["_browser_repeated_call_guard"].repeat_count

    def evaluate(self, function: str, value: Any, *, detail: str | None = None) -> ToolCallInputs:
        return self.call(EVALUATE, {"function": function}, result(value, detail=detail))

    def model_call(self) -> ModelCallInputs:
        inputs = ModelCallInputs(tools=[{"name": EVALUATE}])
        asyncio.run(self.rail.before_model_call(SimpleNamespace(agent=self.agent, extra=self.extra, inputs=inputs)))
        return inputs


class TestNormalisation:
    def test_whitespace_in_evaluate_function_is_ignored(self) -> None:
        run = _Run()
        run.evaluate("() => 1", 1)
        run.evaluate("()=>1", 1)

        assert run.repeat_count() == 2

    def test_different_functions_differ(self) -> None:
        run = _Run()
        run.evaluate("() => document.title", "Home")
        run.evaluate("() => location.href", "Home")

        assert run.repeat_count() == 1

    def test_argument_order_does_not_matter(self) -> None:
        run = _Run()
        run.call("browser_click", {"target": "e1", "element": "Search"}, result())
        run.call("browser_click", {"element": "Search", "target": "e1"}, result())

        assert run.repeat_count() == 2

    def test_whitespace_matters_outside_evaluate_functions(self) -> None:
        run = _Run()
        run.call("browser_type", {"target": "e1", "text": "a b"}, result())
        run.call("browser_type", {"target": "e1", "text": "ab"}, result())

        assert run.repeat_count() == 1

    def test_non_json_arguments_do_not_crash(self) -> None:
        run = _Run()
        first = run.call(EVALUATE, {}, raw_arguments="{not json")
        run.call(EVALUATE, {}, raw_arguments="{not json")

        assert command_record(first.tool_result) is None
        assert run.repeat_count() == 2
        run.call(EVALUATE, {}, raw_arguments=None)
        assert run.repeat_count() == 1
        assert run.driver.calls == []


class TestFingerprint:
    def test_volatile_events_and_console_counts_are_ignored(self) -> None:
        run = _Run()
        first = run.evaluate("() => 1", 1, detail=_events(1))
        second = run.evaluate("() => 1", 1, detail=_events(7))

        assert first.tool_msg.content != second.tool_msg.content
        assert run.repeat_count() == 2

    def test_different_results_differ(self) -> None:
        run = _Run()
        run.evaluate("() => 'ok'", "ok")
        run.evaluate("() => 'ok'", "done")

        assert run.repeat_count() == 1


class TestCounting:
    def test_note_on_third_and_fourth_identical_call(self) -> None:
        run = _Run()
        navigate = run.call("browser_navigate", {"url": "https://www.allrecipes.com"}, result())
        counts = [run.repeat_count()]
        messages = [navigate.tool_msg.content]
        for call in range(1, 5):
            inputs = run.evaluate("() => 1", 1, detail=_events(call))
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
            run.evaluate(heading, "A")
        inputs = run.evaluate(heading, "B")

        assert run.repeat_count() == 1
        assert NOTE not in inputs.tool_msg.content

    def test_different_key_resets(self) -> None:
        run = _Run()
        for _ in range(3):
            run.evaluate("() => document.body.innerText.length", 1)
        run.evaluate("() => document.body.innerHTML.length", 1)

        assert run.repeat_count() == 1

    def test_note_is_appended_inside_the_error_envelope(self) -> None:
        run = _Run()
        for _ in range(3):
            inputs = run.call(
                "browser_click", {"target": "e12"}, ElementNotFound("Ref e12 not found in the current page snapshot.")
            )

        assert inputs.tool_msg.content.startswith("### Error\nRef e12 not found")
        assert inputs.tool_msg.content.endswith("Otherwise take a different action.")

    def test_structured_result_is_untouched(self) -> None:
        run = _Run()
        for _ in range(3):
            inputs = run.evaluate("() => 1", 1)

        assert inputs.tool_result.data["content"] == "### Result\n1"
        assert NOTE in inputs.tool_msg.content


class TestWithholdingTools:
    def test_tools_withheld_after_fifth_identical_call(self) -> None:
        run = _Run()
        withheld = []
        for _ in range(5):
            withheld.append(run.model_call().tools == [])
            run.evaluate("() => 'ok'", "ok")

        inputs = run.model_call()

        assert withheld == [False] * 5
        assert inputs.tools == []
        assert "Tools are disabled for this reply" in run.agent.system_prompt_builder.build()

    def test_new_run_resets_state_and_finish_section(self) -> None:
        run = _Run()
        for _ in range(5):
            run.evaluate("() => 'ok'", "ok")
        run.model_call()

        asyncio.run(run.rail.before_invoke(SimpleNamespace(agent=run.agent, extra=run.extra, inputs=None)))

        assert run.model_call().tools == [{"name": EVALUATE}]
        assert run.repeat_count() == 0
        assert not run.agent.system_prompt_builder.has_section("browser_repeated_call_finish")

    def test_loading_page_snapshots_are_not_repeats(self) -> None:
        run = _Run()
        for step in range(5):
            progress = Element(ref=DriverRef("e5", ""), role="progressbar", name="", value=f"{step * 20}%")
            loading = Observation(context=HOME, elements=(progress,), native_text=f"- progressbar: {step * 20}%")
            inputs = run.call("browser_snapshot", {}, loading)
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
        assert _is_noop_script(command_record(_Run().evaluate(function, "x").tool_result))

    @pytest.mark.parametrize("function", PAGE_READING_FUNCTIONS)
    def test_functions_that_read_or_change_the_page_are_not(self, function: str) -> None:
        assert not _is_noop_script(command_record(_Run().evaluate(function, "x").tool_result))

    def test_other_tools_and_bad_arguments_are_not(self) -> None:
        run = _Run()
        click = run.call("browser_click", {"target": "e1", "function": "() => 1"}, result())
        undecoded = run.call(EVALUATE, {"element": "x"})

        assert not _is_noop_script(command_record(click.tool_result))
        assert not _is_noop_script(command_record(undecoded.tool_result))


class TestDigitInsensitiveEvaluate:
    @pytest.mark.parametrize(
        ("function_for", "result_for"),
        [
            (lambda n: f"()=>{31 + n}", lambda n: 31 + n),
            (lambda n: f"() => document.body.innerText.length+{n}", lambda n: 6781 + n),
        ],
        ids=["constant-counter", "innerText-length-plus-n"],
    )
    def test_counter_variants_count_as_one_streak(
        self, function_for: Callable[[int], str], result_for: Callable[[int], int]
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
        run = _Run()
        for index in range(5):
            run.call("browser_click", {"target": f"e1{index}"}, result())

        assert run.repeat_count() == 1
        assert run.model_call().tools == [{"name": EVALUATE}]


class TestNoopStreak:
    CAMBRIDGE = ["()=>location.href", "() => 'ok'", "() => 'done'", "() => document.title", "()=>1"]
    RESULTS = ["https://dictionary.cambridge.org/", "ok", "done", "Cambridge Dictionary", 1]

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
        for function, value in zip(self.CAMBRIDGE[:2], self.RESULTS[:2]):
            run.evaluate(function, value)
        answer = Observation(context=HOME, native_text="- heading") if name == "browser_snapshot" else result()
        run.call(name, arguments, answer)
        messages = [run.evaluate(f, r).tool_msg.content for f, r in zip(self.CAMBRIDGE[2:], self.RESULTS[2:])]

        assert [NOTE in message for message in messages] == [False, False, True]
        assert run.model_call().tools == [{"name": EVALUATE}]


class TestNoFalsePositives:
    def test_reading_changing_page_content(self) -> None:
        run = _Run()
        price = "() => document.querySelector('.price').innerText"
        for text in ("Laptop", "Phone", "Tablet", "Monitor", "Keyboard"):
            assert NOTE not in run.evaluate(price, text).tool_msg.content

        assert run.model_call().tools == [{"name": EVALUATE}]

    def test_normal_navigation_sequence(self) -> None:
        run = _Run()
        steps = [
            run.call("browser_navigate", {"url": "https://www.apple.com"}, result()),
            run.evaluate("() => document.title", "Apple"),
            run.call("browser_click", {"target": "e21"}, result()),
            run.evaluate("() => document.title", "Mac - Apple"),
        ]

        assert all(NOTE not in inputs.tool_msg.content for inputs in steps)
        assert run.model_call().tools == [{"name": EVALUATE}]


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
            name=EVALUATE,
            arguments=json.dumps({"function": self.function_for(len(self.requests))}),
        )
        return AssistantMessage(content="", tool_calls=[call])


def _build_worker(llm: Any, max_steps: int, driver: FakeDriver, *names: str) -> Any:
    worker = build_browser_worker_agent(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        max_steps=max_steps,
    )
    worker.set_llm(llm)
    tools = command_tools(driver)
    for name in names or (EVALUATE,):
        worker.ability_manager.add_ability(tools[name].card, tools[name])
    return worker


def _results(*values: Any) -> list[ActionResult]:
    return [result(value) for value in values]


def test_worker_loop_ends_with_final_answer_once_tools_are_withheld() -> None:
    max_steps = 20

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        llm = _LoopingLLM()
        worker = _build_worker(llm, max_steps, fake_driver(_results(*["ok"] * 10)))
        await worker.register_rail(RepeatedCallGuardRail())
        outcome = await Runner.run_agent(
            worker, {"query": "Find a vegetarian lasagna", "conversation_id": "loop-records-1"}
        )
        first_requests = list(llm.requests)
        llm.requests.clear()
        await Runner.run_agent(worker, {"query": "Find a vegetarian lasagna", "conversation_id": "loop-records-2"})
        return outcome, first_requests, list(llm.requests)

    outcome, requests, next_run_requests = asyncio.run(scenario())

    assert json.loads(outcome["output"]) == FINAL_JSON
    assert len(requests) == 6 < max_steps
    assert [bool(request["tools"]) for request in requests] == [True] * 5 + [False]
    tool_messages = [message.content for message in requests[-1]["messages"] if message.role == "tool"]
    assert [NOTE in message for message in tool_messages] == [False, False, True, True, True]
    system_prompt = requests[-1]["messages"][0].content
    assert "Tools are disabled for this reply" in system_prompt
    assert next_run_requests[0]["tools"]
    assert "Tools are disabled for this reply" not in next_run_requests[0]["messages"][0].content


def test_worker_loop_with_a_counter_in_the_argument_also_ends() -> None:
    max_steps = 20

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        llm = _LoopingLLM(lambda n: f"() => 'done{n}'")
        worker = _build_worker(llm, max_steps, fake_driver(_results(*[f"done{n}" for n in range(1, 6)])))
        await worker.register_rail(RepeatedCallGuardRail())
        outcome = await Runner.run_agent(
            worker, {"query": "Find a vegetarian lasagna", "conversation_id": "loop-records-3"}
        )
        return outcome, list(llm.requests)

    outcome, requests = asyncio.run(scenario())

    assert json.loads(outcome["output"]) == FINAL_JSON
    assert len(requests) == 6 < max_steps
    assert [bool(request["tools"]) for request in requests] == [True] * 5 + [False]
    tool_messages = [message.content for message in requests[-1]["messages"] if message.role == "tool"]
    assert ["done1" in tool_messages[0], "done5" in tool_messages[4]] == [True, True]
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
        patch.object(service_module, "assemble_driver", AsyncMock(return_value=fake_assembly(mcp_fake_driver()))),
        patch(f"{module}.build_browser_worker_agent", return_value=worker),
    ):
        asyncio.run(service.ensure_started())

    rails = [call.args[0] for call in worker.register_rail.await_args_list]
    assert any(isinstance(rail, RepeatedCallGuardRail) for rail in rails)


class _SameTurnLLM:
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
    # A known false positive, pinned as it behaves today.
    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        llm = _SameTurnLLM(8)
        driver = fake_driver(_results(*[None] * 8))
        worker = _build_worker(llm, 20, driver, "browser_press_key")
        await worker.register_rail(RepeatedCallGuardRail())
        outcome = await Runner.run_agent(worker, {"query": "Move the price slider", "conversation_id": "f1-records"})
        return outcome, list(llm.requests), driver.kinds().count("execute")

    outcome, requests, executed = asyncio.run(scenario())

    assert json.loads(outcome["output"]) == FINAL_JSON
    assert executed == 8
    assert len(requests) == 2
    assert requests[0]["tools"]
    assert not requests[1]["tools"], "the 8 same-turn calls count as a loop and tools are withheld"
    tool_messages = [message.content for message in requests[1]["messages"] if message.role == "tool"]
    assert [NOTE in message for message in tool_messages] == [False, False] + [True] * 6


class _RepeatThenAnswerLLM:
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
            name=EVALUATE,
            arguments=json.dumps({"function": "() => 'ok'"}),
        )
        return AssistantMessage(content="", tool_calls=[call])


@pytest.mark.parametrize("with_rail", [False, True], ids=["without-guard", "with-guard"])
def test_guard_adds_no_model_round_trip(with_rail: bool) -> None:
    async def scenario() -> list[dict[str, Any]]:
        llm = _RepeatThenAnswerLLM(4)
        worker = _build_worker(llm, 20, fake_driver(_results(*["ok"] * 4)))
        if with_rail:
            await worker.register_rail(RepeatedCallGuardRail())
        await Runner.run_agent(
            worker, {"query": "Find a vegetarian lasagna", "conversation_id": f"f06-guard-records-{with_rail}"}
        )
        return list(llm.requests)

    requests = asyncio.run(scenario())

    assert len(requests) == 5
    tool_messages = [message.content for message in requests[-1]["messages"] if message.role == "tool"]
    assert [NOTE in message for message in tool_messages] == ([False, False, True, True] if with_rail else [False] * 4)


def test_surface_context_is_part_of_the_result() -> None:
    run = _Run()
    run.evaluate("() => 1", 1)
    run.call(EVALUATE, {"function": "() => 1"}, ActionResult(ok=True, value=1, surface=None))

    assert run.repeat_count() == 1
