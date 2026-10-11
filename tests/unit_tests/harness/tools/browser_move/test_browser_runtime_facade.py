# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``BrowserAgentRuntime`` as the WebVoyager runner's facade: one session for the main agent and the worker."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.subagents import browser_agent as browser_agent_module
from openjiuwen.harness.tools.browser_move.playwright_runtime import runtime as runtime_module
from openjiuwen.harness.tools.browser_move.playwright_runtime import service as service_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails, CoreSettings
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    DEVICE,
    MCP_PREFIX,
    fake_assembly,
    mcp_fake_driver,
    run,
)

HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}


def _runtime(core: CoreSettings | None = None) -> BrowserAgentRuntime:
    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=McpServerConfig(
            server_id="playwright_official_stdio",
            server_name="playwright-official",
            server_path="stdio://playwright",
            client_type="stdio",
            params={"cwd": str(Path.cwd())},
        ),
        guardrails=BrowserRunGuardrails(max_steps=7, max_failures=1, timeout_s=30, retry_once=False),
        core_settings=core,
        language="en",
    )


def _assembling(driver: Any = None) -> Any:
    assembly = fake_assembly(driver or mcp_fake_driver())
    return patch.object(service_module, "assemble_driver", AsyncMock(return_value=assembly))


def test_the_main_agent_and_the_worker_share_the_services_session() -> None:
    runtime = _runtime()
    with _assembling() as assemble:
        run(runtime.ensure_started())
        run(runtime.ensure_runtime_ready())
        parts = runtime.agent_parts(image_support=False)

    assert assemble.await_count == 1
    assert runtime.session is runtime.service.session is not None
    worker = runtime.service.browser_agent
    names = {tool.name for tool in run(worker.ability_manager.list_tool_info())}
    assert HELPERS <= names and "browser_recall_offload" not in names
    assert {tool.card.name for tool in parts.tools} == names | {"browser_recall_offload"}
    assert all(name.startswith(MCP_PREFIX) for name in names - HELPERS)


def test_a_task_release_stops_the_stack_and_the_next_task_restarts_it_on_the_same_session() -> None:
    runtime = _runtime()
    driver = mcp_fake_driver()
    with _assembling(driver) as assemble:
        run(runtime.ensure_runtime_ready())
        session = runtime.session
        run(runtime.release_task_resources())
        assert (driver.kinds(), runtime.service.started) == (["stop"], False)
        run(runtime.acquire_task_resources())
        run(runtime.ensure_runtime_ready())

    assert assemble.await_count == 1
    assert runtime.session is session
    assert driver.kinds() == ["stop", "start"]
    assert session.assembly.provisioner.released == []


def test_runtime_health_reports_the_session_health() -> None:
    runtime = _runtime()

    before = run(runtime.runtime_health())
    with _assembling():
        run(runtime.ensure_runtime_ready())
    after = run(runtime.runtime_health())

    assert (before["ok"], before["started"], before["detail"]) == (False, False, "not started")
    assert (after["ok"], after["started"], after["detail"]) == (False, True, "not started")
    run(runtime.session.start())
    assert run(runtime.runtime_health())["ok"] is True
    assert after["model_name"] == "test-model"


def test_shutdown_closes_the_session_and_the_model_clients() -> None:
    runtime = _runtime()
    with _assembling() as assemble:
        run(runtime.ensure_started())
    provisioner = runtime.session.assembly.provisioner

    with patch.object(runtime_module.OpenAIModelClient, "aclose", AsyncMock()) as aclose:
        run(runtime.shutdown())

    assert provisioner.released == [DEVICE]
    assert (runtime.session, runtime.service.session, runtime.service.browser_agent) == (None, None, None)
    aclose.assert_awaited_once()
    assert assemble.await_count == 1


def test_on_browser_use_the_facade_runs_the_main_agent_on_itself_and_maps_the_answer() -> None:
    runtime = _runtime(CoreSettings(driver="browser_use", worker_temperature=0.3, worker_top_p=0.9))
    agent = MagicMock()
    result = {
        "output": "The main heading is Example Domain.",
        "result_type": "answer",
        "error": None,
        "authoritative_browser_result": {"status": "completed"},
        "progress_state": {"status": "completed", "completed_steps": ["read the heading"]},
    }
    with (
        patch.object(browser_agent_module, "create_browser_agent", return_value=agent) as create,
        patch.object(runtime_module.Runner, "run_agent", AsyncMock(return_value=result)) as run_agent,
        _assembling() as assemble,
    ):
        run(runtime.ensure_started())
        answer = run(runtime.run_browser_task(task="Report the heading", session_id="wv-agent"))

    assert assemble.await_count == 0, "the main agent opens the session at its first invoke"
    assert runtime.service.browser_agent is None
    kwargs = create.call_args.kwargs
    assert kwargs["runtime"] is runtime
    assert (kwargs["language"], kwargs["max_iterations"], kwargs["browser_capabilities"]) == ("en", 7, ["core"])
    assert kwargs["temperature"] == 0.3
    model = create.call_args.args[0]
    assert (model.model_client_config.api_key, model.model_config.model_name) == ("test-key", "test-model")
    assert (model.model_config.temperature, model.model_config.top_p) == (0.3, 0.9)
    inputs = run_agent.await_args.args[1]
    assert inputs["query"] == "Report the heading" and inputs["conversation_id"].startswith("wv-agent:direct:")
    assert answer == {
        "ok": True,
        "session_id": "wv-agent",
        "request_id": inputs["request_id"],
        "final": "The main heading is Example Domain.",
        "page": {"url": "", "title": ""},
        "screenshot": None,
        "error": None,
        "status": "completed",
        "progress": {"status": "completed", "completed_steps": ["read the heading"]},
    }


def test_on_browser_use_a_failed_run_is_reported_in_the_answer() -> None:
    runtime = _runtime(CoreSettings(driver="browser_use"))
    with (
        patch.object(browser_agent_module, "create_browser_agent", return_value=MagicMock()),
        patch.object(runtime_module.Runner, "run_agent", AsyncMock(side_effect=RuntimeError("model gone"))),
    ):
        failed = run(runtime.run_browser_task(task="Report the heading", session_id="s", request_id="r"))
    async def slow(agent: Any, inputs: Any) -> None:
        await asyncio.sleep(10)

    with (
        patch.object(browser_agent_module, "create_browser_agent", return_value=MagicMock()),
        patch.object(runtime_module.Runner, "run_agent", AsyncMock(side_effect=slow)),
    ):
        timed_out = run(runtime.run_browser_task(task="Report the heading", session_id="s", timeout_s=1))

    assert (failed["ok"], failed["error"], failed["request_id"]) == (False, "model gone", "r")
    assert (timed_out["ok"], timed_out["error"]) == (False, "task_timeout: exceeded 1s")
