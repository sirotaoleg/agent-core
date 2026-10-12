# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``topology_for`` and the two strategies behind the facade's ``ensure_started`` and ``run_browser_task``."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.logging.browser_context import is_browser_agent_log_context
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness import deep_agent as deep_agent_module
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.subagents import browser_agent as browser_agent_module
from openjiuwen.harness.subagents.browser_agent import DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT
from openjiuwen.harness.tools.browser_move.contract import ConnectionLost
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.runtime import agents as agents_module
from openjiuwen.harness.tools.browser_move.runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.runtime import session as runtime_module
from openjiuwen.harness.tools.browser_move.runtime import topology as topology_module
from openjiuwen.harness.tools.browser_move.runtime.config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
    CoreSettings,
)
from openjiuwen.harness.tools.browser_move.runtime.rails.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.runtime.rails.final_answer_recovery import FinalAnswerRecoveryRail
from openjiuwen.harness.tools.browser_move.runtime.rails.intercepted_action import InterceptedActionHintRail
from openjiuwen.harness.tools.browser_move.runtime.rails.repeated_call_guard import RepeatedCallGuardRail
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.runtime.topology import (
    WORKER_LANGUAGE,
    DelegatedWorker,
    Direct,
    topology_for,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails import runtime_rail as rail_module
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.runtime_rail import BrowserRuntimeRail
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    MCP_PREFIX,
    fake_assembly,
    mcp_driver_info,
    mcp_fake_driver,
    run,
)

HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}
MCP = CoreSettings(driver="playwright_mcp")
BROWSER_USE = CoreSettings(driver="browser_use")


def _runtime(
    core: CoreSettings | None = None,
    language: str | None = "en",
    instance: BrowserInstanceConfig | None = None,
    workspace: Path | None = None,
) -> BrowserAgentRuntime:
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
        instance=instance,
        core_settings=core,
        workspace=None if workspace is None else str(workspace),
        **({} if language is None else {"language": language}),
    )


def _assembling(driver: Any = None) -> Any:
    assembly = fake_assembly(driver or mcp_fake_driver())
    return patch.object(assembly_module, "assemble_driver", AsyncMock(return_value=assembly))


def _registering_runtime_tools() -> Any:
    return patch.object(runtime_module.Runner.resource_mgr, "add_tool", MagicMock(return_value=None))


@pytest.mark.parametrize(
    ("driver", "topology", "expected"),
    [
        ("playwright_mcp", "", DelegatedWorker),
        ("browser_use", "", Direct),
        ("playwright_mcp", "direct", Direct),
        ("browser_use", "delegated_worker", DelegatedWorker),
    ],
)
def test_topology_for_takes_the_setting_and_falls_back_to_the_drivers_default(
    driver: str, topology: str, expected: type
) -> None:
    strategy = topology_for(CoreSettings(driver=driver, topology=topology))

    assert type(strategy) is expected
    assert strategy.name == topology or strategy.name == expected.name


def test_a_registered_default_that_no_strategy_implements_is_refused() -> None:
    with patch.object(topology_module, "default_topology", return_value="remote"):
        with pytest.raises(ValueError, match=r"driver 'browser_use' declares the topology 'remote', which is none of"):
            topology_for(BROWSER_USE)


def test_the_instances_driver_override_decides_the_default() -> None:
    runtime = _runtime(MCP, instance=BrowserInstanceConfig(driver="browser_use"))
    with patch.object(browser_agent_module, "create_browser_agent", return_value=MagicMock()) as create:
        run(runtime.ensure_started())

    create.assert_called_once()
    assert runtime.service.browser_agent is None


class TestDirect:
    def test_start_builds_the_main_agent_on_the_facade_once_and_run_maps_the_answer(self) -> None:
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
            run(runtime.ensure_started())
            answer = run(runtime.run_browser_task(task="Report the heading", session_id="wv-agent"))

        assert assemble.await_count == 0, "the main agent opens the session at its first invoke"
        assert runtime.service.browser_agent is None
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        assert kwargs["runtime"] is runtime
        assert (kwargs["language"], kwargs["max_iterations"], kwargs["browser_capabilities"]) == ("en", 7, ["core"])
        assert kwargs["temperature"] == 0.3
        model = create.call_args.args[0]
        assert (model.model_client_config.api_key, model.model_config.model_name) == ("test-key", "test-model")
        assert (model.model_config.temperature, model.model_config.top_p) == (0.3, 0.9)
        assert run_agent.await_args.args[0] is agent
        inputs = run_agent.await_args.args[1]
        assert inputs["query"] == "Report the heading" and inputs["conversation_id"].startswith("wv-agent.direct.")
        assert ContextUtils.offload_session_dir_name(inputs["conversation_id"]) == inputs["conversation_id"]
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

    def test_start_makes_no_model_call_and_run_awaits_the_probe_before_the_agent_runs(self) -> None:
        runtime = _runtime(BROWSER_USE)
        events: list[str] = []

        async def probe(model: Any) -> bool:
            events.append(f"probe {model.model_client_config.api_base} {model.model_config.model_name}")
            return True

        async def run_agent(agent: Any, inputs: Any) -> dict[str, Any]:
            events.append("agent")
            return {"output": "done", "result_type": "answer"}

        with (
            patch.object(topology_module, "probe_image_support", side_effect=probe),
            patch.object(browser_agent_module, "create_browser_agent", return_value=MagicMock()),
            patch.object(runtime_module.Runner, "run_agent", side_effect=run_agent),
        ):
            run(runtime.ensure_started())
            started = list(events)
            run(runtime.run_browser_task(task="Report the heading", session_id="s", request_id="r"))

        assert started == []
        assert events == ["probe https://example.invalid/v1 test-model", "agent"]

    def test_the_agent_speaks_the_workers_language_and_gets_the_core_rails_at_construction(self) -> None:
        runtime = _runtime(BROWSER_USE, language=None)
        built: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> MagicMock:
            built.append(kwargs)
            return MagicMock()

        with patch.object(browser_agent_module, "create_deep_agent", side_effect=capture), _assembling() as assemble:
            run(runtime.ensure_started())

        (agent,) = built
        assert assemble.await_count == 0
        context_rail = next(rail for rail in agent["rails"] if isinstance(rail, ContextProcessorRail))
        working_context = dict(context_rail._user_processors)["BrowserWorkingContextProcessor"]
        assert (agent["language"], working_context.language) == (WORKER_LANGUAGE, "en")
        assert agent["system_prompt"] == DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["en"]
        kinds = [type(rail) for rail in agent["rails"]]
        assert kinds[:4] == [BrowserRuntimeRail, ActionPolicy, RepeatedCallGuardRail, InterceptedActionHintRail]
        policy = agent["rails"][1]
        assert policy._builders is runtime.builders and dict(policy._builders) == {}
        assert policy._page_state is runtime.page_state

    def test_the_policy_built_before_the_session_sees_the_tools_built_after_it(self) -> None:
        runtime = _runtime(BROWSER_USE)
        policy = runtime.core_rails()[0]
        with _assembling():
            run(runtime.ensure_runtime_ready())
            parts = runtime.agent_parts(image_support=False, language=WORKER_LANGUAGE)

        prefixed = {tool.card.name for tool in parts.tools if tool.card.name.startswith(MCP_PREFIX)}
        decoded = prefixed - {MCP_PREFIX + "browser_find"}
        assert MCP_PREFIX + "browser_click" in decoded and decoded <= set(policy._builders)
        assert HELPERS <= {tool.card.name for tool in parts.tools}

    def test_the_agent_runs_outside_the_browser_log_context_like_the_worker(self) -> None:
        direct = _runtime(BROWSER_USE)
        with patch.object(browser_agent_module, "create_browser_agent", return_value=MagicMock()):
            run(direct.ensure_started())

        async def invoked(runtime: BrowserAgentRuntime) -> tuple[bool, bool]:
            agent = MagicMock(
                deep_config=SimpleNamespace(enable_read_image_multimodal=False, language="en"),
                register_rail=AsyncMock(),
            )
            ctx = AgentCallbackContext(agent=agent)
            await BrowserRuntimeRail(runtime).before_invoke(ctx)
            return is_browser_agent_log_context(), rail_module._BROWSER_LOG_CONTEXT_TOKEN_KEY in ctx.extra

        with _assembling():
            assert (direct.dedicated_log, run(invoked(direct))) == (False, (False, False))
            assert (_runtime().dedicated_log, run(invoked(_runtime()))) == (True, (True, True))

    def test_a_session_that_cannot_start_fails_the_task_before_any_model_call(self, tmp_path: Path) -> None:
        runtime = _runtime(BROWSER_USE, workspace=tmp_path)
        failing = AsyncMock(side_effect=ConnectionLost("connect: No valid agent focus available", dispatched=True))
        model_calls = AsyncMock(side_effect=RuntimeError("the model was called"))
        with (
            patch.object(assembly_module, "assemble_driver", failing),
            patch.object(OpenAIModelClient, "invoke", model_calls),
            patch.object(OpenAIModelClient, "stream", model_calls),
            patch.object(deep_agent_module, "schedule_image_support_probe"),
        ):
            answer = run(runtime.run_browser_task(task="Report the heading", session_id="s", request_id="r"))

        assert (answer["ok"], answer["error"]) == (False, "connect: No valid agent focus available")
        assert failing.await_count == 2, "the first open is retried once"
        model_calls.assert_not_awaited()

    def test_a_failed_or_timed_out_run_is_reported_in_the_answer(self) -> None:
        runtime = _runtime(BROWSER_USE)
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

    def test_shutdown_drops_the_agent_so_the_next_start_builds_a_new_one(self) -> None:
        runtime = _runtime(BROWSER_USE)
        with (
            patch.object(browser_agent_module, "create_browser_agent", return_value=MagicMock()) as create,
            patch.object(runtime_module.OpenAIModelClient, "aclose", AsyncMock()),
        ):
            run(runtime.ensure_started())
            run(runtime.shutdown())
            run(runtime.ensure_started())

        assert create.call_count == 2


class TestDelegatedWorker:
    def test_start_opens_the_session_builds_the_worker_once_and_registers_the_runtime_tools(self) -> None:
        runtime = _runtime(MCP)
        worker = MagicMock()
        worker.register_rail = AsyncMock()
        with (
            _assembling() as assemble,
            patch.object(agents_module, "build_browser_worker_agent", return_value=worker) as build,
            _registering_runtime_tools() as add_tool,
        ):
            run(runtime.ensure_started())
            run(runtime.ensure_started())

        assert assemble.await_count == 1
        assert runtime.service.browser_agent is worker
        build.assert_called_once()
        kwargs = build.call_args.kwargs
        names = {tool.card.name for tool in kwargs["tools"]}
        assert {name for name in names if not name.startswith(MCP_PREFIX)} == HELPERS
        assert {name.removeprefix(MCP_PREFIX) for name in names if name.startswith(MCP_PREFIX)} == {
            "browser_click",
            "browser_evaluate",
            "browser_file_upload",
            "browser_fill_form",
            "browser_find",
            "browser_handle_dialog",
            "browser_hover",
            "browser_navigate",
            "browser_navigate_back",
            "browser_press_key",
            "browser_select_option",
            "browser_snapshot",
            "browser_tabs",
            "browser_type",
        }
        service = runtime.service
        assert (kwargs["provider"], kwargs["api_key"], kwargs["model_name"]) == ("openai", "test-key", "test-model")
        assert (kwargs["max_steps"], kwargs["settings"], kwargs["tool_result_observer"]) == (
            7,
            service.core,
            service.observe_worker_tool_result,
        )
        assert (kwargs["screenshot_subdir"], kwargs["artifacts_subdir"], kwargs["driver_name"]) == (
            "screenshots",
            "artifacts",
            "fake",
        )
        assert [type(call.args[0]) for call in worker.register_rail.await_args_list] == [
            ActionPolicy,
            RepeatedCallGuardRail,
            InterceptedActionHintRail,
            FinalAnswerRecoveryRail,
        ]
        assert [call.args[0].card.name for call in add_tool.call_args_list] == [
            "browser_custom_action",
            "browser_list_custom_actions",
        ]

    def test_the_worker_is_built_without_image_support_and_probes_nothing(self) -> None:
        runtime = _runtime(MCP)
        probe = AsyncMock(return_value=True)
        with (
            _assembling(),
            _registering_runtime_tools(),
            patch.object(topology_module, "probe_image_support", probe),
            patch.object(agents_module, "build_agent_parts", wraps=agents_module.build_agent_parts) as parts,
        ):
            run(runtime.ensure_started())

        probe.assert_not_awaited()
        assert parts.call_args.kwargs["image_support"] is False
        names = {tool.name for tool in run(runtime.service.browser_agent.ability_manager.list_tool_info())}
        assert MCP_PREFIX + "browser_take_screenshot" not in names

    def test_the_worker_is_built_again_after_a_task_release(self) -> None:
        runtime = _runtime(MCP)
        with _assembling(), _registering_runtime_tools():
            run(runtime.ensure_started())
            first = runtime.service.browser_agent
            run(runtime.release_task_resources())
            assert runtime.service.browser_agent is None
            run(runtime.acquire_task_resources())
            run(runtime.ensure_started())

        assert runtime.service.browser_agent is not first
        assert runtime.service.started is True

    def test_run_hands_the_task_to_the_service_with_a_worker_that_rebuilds_after_a_reset(self) -> None:
        runtime = _runtime(MCP)
        run_task = AsyncMock(return_value={"ok": True})
        with (
            _assembling() as assemble,
            _registering_runtime_tools(),
            patch.object(runtime.service, "run_task", run_task),
        ):
            answer = run(runtime.run_browser_task(task="Open the scene", session_id="s", request_id="r", timeout_s=5))
            worker = run_task.await_args.kwargs["worker"]
            first = run(worker())
            run(runtime.service.reset())
            second = run(worker())

        assert answer == {"ok": True}
        assert run_task.await_args.kwargs | {"worker": None} == {
            "task": "Open the scene",
            "session_id": "s",
            "request_id": "r",
            "timeout_s": 5,
            "worker": None,
        }
        assert first is not None and second is not None and second is not first
        assert first is not runtime.service.browser_agent and second is runtime.service.browser_agent
        assert assemble.await_count == 2

    def test_on_a_browser_use_shaped_driver_the_worker_reads_browser_use_tools_with_plain_names(self) -> None:
        info = dataclasses.replace(mcp_driver_info(), native_tools={}, tool_name_prefix="", display_name="browser-use")
        runtime = _runtime(CoreSettings(driver="browser_use", topology="delegated_worker"))
        with _assembling(FakeDriver(info=info)), _registering_runtime_tools():
            run(runtime.ensure_started())

        worker = runtime.service.browser_agent
        prompt = worker.config.prompt_template[0]["content"]
        assert "with browser-use tools and approved runtime helper tools only." in prompt
        assert "required by a browser-use action." in prompt
        names = {tool.name for tool in run(worker.ability_manager.list_tool_info())}
        assert {"browser_click", "browser_navigate", "browser_snapshot"} | HELPERS <= names
        assert not any(name.startswith(MCP_PREFIX) for name in names)

    def test_shutdown_leaves_no_worker_and_a_new_start_builds_one(self) -> None:
        runtime = _runtime(MCP)
        with _assembling() as assemble, _registering_runtime_tools():
            run(runtime.ensure_started())
            first = runtime.service.browser_agent
            with patch.object(runtime_module.OpenAIModelClient, "aclose", AsyncMock()):
                run(runtime.shutdown())
            assert runtime.service.browser_agent is None
            run(runtime.ensure_started())

        assert runtime.service.browser_agent is not first
        assert assemble.await_count == 2
