# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Final-answer recovery with the snapshot tool as a ``CommandTool`` over ``FakeDriver``."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.contract import Observation, Observe
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.runtime.agents import build_browser_worker_agent
from openjiuwen.harness.tools.browser_move.runtime.config import BrowserRunGuardrails, CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.rails.final_answer_recovery import (
    RECOVERY_MESSAGE,
    STEP_LIMIT_SENTENCE,
    FinalAnswerRecoveryRail,
)
from openjiuwen.harness.tools.browser_move.runtime.service import BrowserService
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import build_browser_worker_system_prompt

from .command_tools import HOME, command_tools
from .runtime_fixtures import fake_assembly, make_runtime

ANSWER = {
    "ok": True,
    "final": "Easy Vegetarian Spinach Lasagna: 4.6 stars, 117 ratings, serves 6.",
    "page": {"url": "https://www.allrecipes.com/recipe/lasagna", "title": "Lasagna"},
    "screenshot": None,
    "error": None,
}
PROSE = "**Answer:** Easy Vegetarian Spinach Lasagna has 4.6 stars from 117 ratings and serves 6."
WAIT_S = 5
SNAPSHOT_RESULT = "### Page\n- Page URL: https://www.allrecipes.com/recipe/lasagna\n- Page Title: Lasagna"

Respond = Callable[[int, dict[str, Any]], Awaitable[AssistantMessage]]


def _mcp_cfg() -> McpServerConfig:
    return McpServerConfig(server_id="pw-test", server_name="pw-test", server_path="stdio://pw")


def _snapshot_call(index: int) -> AssistantMessage:
    call = ToolCall(id=f"call-{index}", type="function", name="browser_snapshot", arguments="{}")
    return AssistantMessage(content="", tool_calls=[call])


def _answer(content: str) -> AssistantMessage:
    return AssistantMessage(content=content)


def _request_id(request: dict[str, Any]) -> str:
    for message in request["messages"]:
        match = re.search(r"Request id: (\S+)", str(message.content))
        if match:
            return match.group(1)
    return ""


class _SnapshotDriver(FakeDriver):
    def __init__(self, before_result: Callable[[], Awaitable[None]] | None = None) -> None:
        super().__init__()
        self.before_result = before_result

    async def observe(self, command: Observe) -> Observation:
        if self.before_result is not None:
            await self.before_result()
        self.script(observations=[Observation(context=HOME, native_text=SNAPSHOT_RESULT)])
        return await super().observe(command)


class _ScriptedLLM:
    """Records every request; `respond(index, request)` decides the reply."""

    def __init__(self, respond: Respond) -> None:
        self.requests: list[dict[str, Any]] = []
        self.respond = respond

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        return await self.respond(len(self.requests) - 1, kwargs)


def _service(
    respond: Respond, *, max_steps: int = 10, driver: _SnapshotDriver | None = None
) -> tuple[BrowserService, _ScriptedLLM, _SnapshotDriver]:
    worker = build_browser_worker_agent(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        max_steps=max_steps,
        worker_prompt=build_browser_worker_system_prompt,
        driver_name="Playwright MCP",
    )
    llm = _ScriptedLLM(respond)
    worker.set_llm(llm)
    driver = driver or _SnapshotDriver()
    snapshot = command_tools(driver)["browser_snapshot"]
    worker.ability_manager.add_ability(snapshot.card, snapshot)
    service = BrowserService(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        mcp_cfg=_mcp_cfg(),
        guardrails=BrowserRunGuardrails(max_steps=max_steps, max_failures=1, timeout_s=30),
    )
    with patch.object(assembly_module, "assemble_driver", AsyncMock(return_value=fake_assembly(driver))):
        asyncio.run(service.ensure_runtime_ready())
    service.browser_agent = worker
    return service, llm, driver


def _worker_of(service: BrowserService) -> Callable[[], Awaitable[Any]]:
    async def worker() -> Any:
        return service.browser_agent

    return worker


def _run(service: BrowserService, rail: bool = True, request_id: str = "r-1") -> dict[str, Any]:
    async def scenario() -> dict[str, Any]:
        if rail:
            await service.browser_agent.register_rail(FinalAnswerRecoveryRail())
        return await service.run_task_once(
            task="Find a vegetarian lasagna",
            session_id="s-records-1",
            request_id=request_id,
            worker=_worker_of(service),
        )

    return asyncio.run(scenario())


def _replies(*replies: AssistantMessage) -> Respond:
    async def respond(index: int, request: dict[str, Any]) -> AssistantMessage:
        del request
        return replies[index]

    return respond


def _roles(request: dict[str, Any]) -> list[str]:
    return [message.role for message in request["messages"]]


class TestRecovery:
    def test_empty_output_is_recovered_in_the_same_conversation(self) -> None:
        service, llm, _ = _service(_replies(_snapshot_call(1), _answer(""), _answer(json.dumps(ANSWER))))

        result = _run(service)

        assert result == {**ANSWER, "recovered_from": "empty_output"}
        assert len(llm.requests) == 3
        recovery = llm.requests[-1]
        assert not recovery["tools"], "the recovery request must not offer tools"
        assert llm.requests[0]["tools"], "the normal run offers tools"
        last = recovery["messages"][-1]
        assert (last.role, last.content) == ("user", RECOVERY_MESSAGE)
        assert any(getattr(message, "tool_calls", None) for message in recovery["messages"])
        tool_results = [message.content for message in recovery["messages"] if message.role == "tool"]
        assert tool_results and "Page Title: Lasagna" in tool_results[0]
        assert "Request id: r-1" in str(recovery["messages"][1].content)

    def test_prose_output_is_recovered(self) -> None:
        service, llm, _ = _service(_replies(_answer(PROSE), _answer(json.dumps(ANSWER))))

        result = _run(service)

        assert result == {**ANSWER, "recovered_from": "non_json_output"}
        assert len(llm.requests) == 2
        assert not llm.requests[-1]["tools"]

    def test_step_limit_is_recovered_despite_max_steps(self) -> None:
        async def respond(index: int, request: dict[str, Any]) -> AssistantMessage:
            if not request["tools"]:
                return _answer(json.dumps({**ANSWER, "ok": False, "status": "partial"}))
            return _snapshot_call(index)

        service, llm, driver = _service(respond, max_steps=2)

        result = _run(service)

        assert result["recovered_from"] == "max_iterations_reached"
        assert result["ok"] is False, "recovery keeps what the model said about ok"
        assert len(llm.requests) == 3 and driver.kinds().count("observe") == 2
        last = llm.requests[-1]["messages"][-1]
        assert last.role == "user"
        assert last.content == f"{STEP_LIMIT_SENTENCE} {RECOVERY_MESSAGE}"

    def test_tool_call_during_recovery_is_not_executed(self) -> None:
        stray = AssistantMessage(content=json.dumps(ANSWER), tool_calls=_snapshot_call(9).tool_calls)
        service, llm, driver = _service(_replies(_answer(PROSE), stray))

        result = _run(service)

        assert result == {**ANSWER, "recovered_from": "non_json_output"}
        assert len(llm.requests) == 2
        assert driver.kinds() == []


class TestRecoveryFails:
    def test_prose_again_keeps_todays_result(self) -> None:
        service, llm, _ = _service(_replies(_answer(PROSE), _answer("Still no JSON, sorry.")))

        result = _run(service)

        assert result == {
            "ok": False,
            "final": PROSE,
            "page": {"url": "", "title": ""},
            "screenshot": None,
            "error": "Browser worker did not return valid JSON output",
            "status": "failed",
        }
        assert len(llm.requests) == 2, "exactly one recovery request"

    def test_empty_then_prose_carries_the_recovery_text(self) -> None:
        service, llm, _ = _service(_replies(_answer(""), _answer(PROSE)))

        result = _run(service)

        assert (result["ok"], result["final"], result["error"]) == (
            False,
            PROSE,
            "Browser worker did not return valid JSON output",
        )
        assert len(llm.requests) == 2

    def test_step_limit_keeps_the_max_iterations_error(self) -> None:
        async def respond(index: int, request: dict[str, Any]) -> AssistantMessage:
            return _snapshot_call(index) if request["tools"] else _answer(PROSE)

        service, llm, _ = _service(respond, max_steps=2)

        result = _run(service)

        assert (result["ok"], result["error"], result["status"], result["final"]) == (
            False,
            "max_iterations_reached",
            "partial",
            PROSE,
        )
        assert BrowserService._is_max_iteration_result(result)
        assert len(llm.requests) == 3


@pytest.mark.parametrize("recovered_from", ["empty_output", None])
def test_run_task_response_keeps_the_recovery_reason(recovered_from: str | None) -> None:
    service, _, _ = _service(_replies())
    parsed = {**ANSWER, "recovered_from": recovered_from} if recovered_from else dict(ANSWER)

    async def fake_run_task_once(*, task: str, session_id: str, request_id: str, worker: Any) -> dict[str, Any]:
        del task, session_id, request_id, worker
        return parsed

    with patch.object(service, "run_task_once", fake_run_task_once):
        response = asyncio.run(
            service.run_task(
                task="Find a vegetarian lasagna", session_id="s-records-1", request_id="r-1", worker=_worker_of(service)
            )
        )

    assert response["ok"] is True
    assert response["final"] == ANSWER["final"]
    if recovered_from:
        assert response["recovered_from"] == recovered_from
    else:
        assert "recovered_from" not in response


def test_valid_json_needs_no_recovery() -> None:
    service, llm, _ = _service(_replies(_snapshot_call(1), _answer(json.dumps(ANSWER))))

    result = _run(service)

    assert result == ANSWER
    assert len(llm.requests) == 2
    assert all(request["tools"] for request in llm.requests)


class TestIsolation:
    def test_next_run_has_tools_again(self) -> None:
        replies = [_answer(PROSE), _answer(json.dumps(ANSWER)), _snapshot_call(3), _answer(json.dumps(ANSWER))]
        service, llm, _ = _service(_replies(*replies))

        _run(service, request_id="r-1")
        result = _run(service, rail=False, request_id="r-2")

        assert result == ANSWER
        assert [bool(request["tools"]) for request in llm.requests] == [True, False, True, True]

    def test_concurrent_runs_do_not_share_the_recovery_flag(self) -> None:
        events: dict[str, asyncio.Event] = {}

        async def respond(index: int, request: dict[str, Any]) -> AssistantMessage:
            del index
            if _request_id(request) == "run-a":
                if request["tools"]:
                    return _answer(PROSE)
                # Run A's recovery call: let run B make a model call while A's flag is set.
                events["a_recovering"].set()
                await asyncio.wait_for(events["b_second_request"].wait(), WAIT_S)
                return _answer(json.dumps(ANSWER))
            b_requests = [r for r in llm.requests if _request_id(r) == "run-b"]
            if len(b_requests) == 1:
                return _snapshot_call(1)
            events["b_second_request"].set()
            return _answer(json.dumps(ANSWER))

        async def before_tool_result() -> None:
            # Bounded, so a broken implementation fails the assertions instead of hanging.
            await asyncio.wait_for(events["a_recovering"].wait(), WAIT_S)

        async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
            events["a_recovering"] = asyncio.Event()
            events["b_second_request"] = asyncio.Event()
            await service.browser_agent.register_rail(FinalAnswerRecoveryRail())
            worker = _worker_of(service)
            return await asyncio.gather(
                service.run_task_once(task="Task A", session_id="s-records-a", request_id="run-a", worker=worker),
                service.run_task_once(task="Task B", session_id="s-records-b", request_id="run-b", worker=worker),
            )

        service, llm, _ = _service(respond, driver=_SnapshotDriver(before_tool_result))

        result_a, result_b = asyncio.run(scenario())

        assert result_a == {**ANSWER, "recovered_from": "non_json_output"}
        assert result_b == ANSWER
        by_run = {
            run: [bool(request["tools"]) for request in llm.requests if _request_id(request) == run]
            for run in ("run-a", "run-b")
        }
        assert by_run == {"run-a": [True, False], "run-b": [True, True]}


@pytest.mark.parametrize("other_rails", [0, 2])
def test_service_registers_the_recovery_rail(other_rails: int) -> None:
    runtime = make_runtime(image_support=None, core_settings=CoreSettings(driver="playwright_mcp"))
    worker = MagicMock()
    worker.register_rail = AsyncMock()
    module = "openjiuwen.harness.tools.browser_move.runtime.agents"
    with patch(f"{module}.build_browser_worker_agent", return_value=worker):
        asyncio.run(runtime.ensure_started())
        for _ in range(other_rails):
            asyncio.run(worker.register_rail(object()))

    rails = [call.args[0] for call in worker.register_rail.await_args_list]
    assert any(isinstance(rail, FinalAnswerRecoveryRail) for rail in rails)


class TestRecoveryCallFails:
    def test_model_error_during_recovery_keeps_todays_result(self) -> None:
        async def respond(index: int, request: dict[str, Any]) -> AssistantMessage:
            if request["tools"]:
                return _answer(PROSE)
            raise RuntimeError("model endpoint unavailable")

        service, llm, driver = _service(respond)

        result = _run(service)

        assert result == {
            "ok": False,
            "final": PROSE,
            "page": {"url": "", "title": ""},
            "screenshot": None,
            "error": "Browser worker did not return valid JSON output",
            "status": "failed",
        }
        assert [bool(request["tools"]) for request in llm.requests] == [True, False], "one recovery call, no retry"
        assert driver.kinds() == []
