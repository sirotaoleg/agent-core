# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``BrowserAgentRuntime`` as the WebVoyager runner's facade: one session, live builders, parts and core rails."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.runtime import runtime as runtime_module
from openjiuwen.harness.tools.browser_move.runtime.config import BrowserRunGuardrails, CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.rails.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.runtime.rails.intercepted_action import InterceptedActionHintRail
from openjiuwen.harness.tools.browser_move.runtime.rails.repeated_call_guard import RepeatedCallGuardRail
from openjiuwen.harness.tools.browser_move.runtime.runtime import BrowserAgentRuntime
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    DEVICE,
    MCP_PREFIX,
    element,
    fake_assembly,
    mcp_fake_driver,
    observation,
    result,
    run,
)

HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}


def _runtime(core: CoreSettings | None = None, language: str | None = "en") -> BrowserAgentRuntime:
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
        **({} if language is None else {"language": language}),
    )


def _assembling(driver: Any = None) -> Any:
    assembly = fake_assembly(driver or mcp_fake_driver())
    return patch.object(assembly_module, "assemble_driver", AsyncMock(return_value=assembly))


def test_the_main_agent_and_the_worker_share_the_services_session() -> None:
    runtime = _runtime()
    with _assembling() as assemble:
        run(runtime.ensure_started())
        run(runtime.ensure_runtime_ready())
        parts = runtime.agent_parts(image_support=False, language="en")

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


def test_capture_reuses_the_models_full_snapshot_but_never_a_scoped_one() -> None:
    runtime = _runtime()
    page = observation([element("e1")])
    metadata = result(value={"page_position": {}, "semantic_state": {}})
    driver = mcp_fake_driver(observations=[page, page, page], results=[metadata, metadata])
    with _assembling(driver):
        run(runtime.ensure_runtime_ready())
        snapshot = next(
            tool
            for tool in runtime.agent_parts(image_support=False, language="en").tools
            if tool.card.name == f"{MCP_PREFIX}browser_snapshot"
        )
        run(snapshot.invoke({}))
        run(runtime.capture_browser_state())
        reused_after_full = runtime.observations_reused
        run(snapshot.invoke({"depth": 1}))
        run(runtime.capture_browser_state())

    assert reused_after_full == runtime.observations_reused == 1
    assert driver.kinds() == ["observe", "execute", "observe", "observe", "execute"]


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


def test_the_surface_is_built_once_from_the_settings_before_any_driver() -> None:
    runtime = _runtime(CoreSettings(driver="browser_use"))

    with _assembling() as assemble:
        bundle = runtime.surface

    assert assemble.await_count == 0
    assert bundle is runtime.surface
    assert bundle.profile.name == "browser" and bundle.settings is runtime.service._settings


def test_core_rails_read_the_profile_and_the_facades_live_builders() -> None:
    runtime = _runtime(CoreSettings(driver="browser_use"))

    rails = runtime.core_rails()
    with _assembling():
        run(runtime.ensure_runtime_ready())
        runtime.agent_parts(image_support=False, language="en")

    assert [type(rail) for rail in rails] == [ActionPolicy, RepeatedCallGuardRail, InterceptedActionHintRail]
    assert rails[0]._builders is runtime.builders
    assert rails[0]._rules == runtime.surface.profile.policy_rules
    assert rails[0]._page_state is runtime.page_state
    assert rails[2]._retry == runtime.surface.profile.messages["retry"]
    assert MCP_PREFIX + "browser_click" in runtime.builders


def test_agent_parts_are_built_once_per_image_support_and_language() -> None:
    runtime = _runtime()
    with _assembling():
        run(runtime.ensure_runtime_ready())
        en = runtime.agent_parts(image_support=False, language="en")
        cn = runtime.agent_parts(image_support=False, language="cn")
        vision = runtime.agent_parts(image_support=True, language="en")

    assert runtime.agent_parts(image_support=False, language="en") is en
    assert en is not cn and en is not vision
    assert {tool.card.name for tool in en.tools} == {tool.card.name for tool in cn.tools}
    assert en.rails == () and cn.rails == ()


def test_register_runtime_tools_adds_the_two_custom_action_tools_once() -> None:
    runtime = _runtime()
    with patch.object(runtime_module.Runner.resource_mgr, "add_tool", MagicMock(return_value=None)) as add_tool:
        runtime.register_runtime_tools()
        runtime.register_runtime_tools()

    assert [call.args[0].card.name for call in add_tool.call_args_list] == [
        "browser_custom_action",
        "browser_list_custom_actions",
    ]
