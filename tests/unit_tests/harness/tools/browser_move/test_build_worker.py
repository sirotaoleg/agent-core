# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``build_worker``: the worker's tools, rails and prompt from a started session and the driver's display name."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.runtime import agents as agents_module
from openjiuwen.harness.tools.browser_move.runtime.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.runtime.agents import WORKER_LANGUAGE, build_worker
from openjiuwen.harness.tools.browser_move.runtime.browser_capabilities import CORE_BROWSER_TOOL_NAMES
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.runtime.final_answer_recovery import FinalAnswerRecoveryRail
from openjiuwen.harness.tools.browser_move.runtime.intercepted_action import InterceptedActionHintRail
from openjiuwen.harness.tools.browser_move.runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.runtime.repeated_call_guard import RepeatedCallGuardRail
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import MCP_PREFIX, fake_assembly, mcp_driver_info

WORKER_HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}
CORE = CoreSettings(worker_temperature=0.3, worker_top_p=0.9)


async def _observer(tool_name: str, tool_result: Any) -> None:
    del tool_name, tool_result


def _session(display_name: str = "") -> DriverSession:
    info = dataclasses.replace(mcp_driver_info(), display_name=display_name)
    assembly = fake_assembly(FakeDriver(info=info))
    return DriverSession(assembly, BrowserPageState(), assembly.surface.profile.messages)


def _build(session: DriverSession) -> Any:
    return asyncio.run(
        build_worker(
            session,
            session.assembly.surface,
            provider="openai",
            api_key="test-key",
            api_base="https://example.invalid/v1",
            model_name="test-model",
            max_steps=3,
            core=CORE,
            capabilities=("core",),
            tool_result_observer=_observer,
            screenshot_subdir="shots",
            artifacts_subdir="out",
        )
    )


def test_the_worker_gets_the_core_tools_the_probes_the_batch_and_find_but_no_recall() -> None:
    worker = _build(_session())

    names = {tool.name for tool in asyncio.run(worker.ability_manager.list_tool_info())}
    assert names == {MCP_PREFIX + name for name in CORE_BROWSER_TOOL_NAMES} | WORKER_HELPERS


def test_the_worker_card_names_the_session_driver() -> None:
    worker = _build(_session("Playwright MCP"))

    assert worker.card.description == "Browser worker that executes web tasks using Playwright MCP tools."


def test_the_worker_is_built_with_the_model_identity_the_core_settings_and_the_observer() -> None:
    session = _session()
    worker = MagicMock()
    worker.register_rail = AsyncMock()
    with patch.object(agents_module, "build_browser_worker_agent", return_value=worker) as build:
        assert _build(session) is worker

    kwargs = build.call_args.kwargs
    assert (kwargs["provider"], kwargs["api_key"], kwargs["api_base"], kwargs["model_name"]) == (
        "openai",
        "test-key",
        "https://example.invalid/v1",
        "test-model",
    )
    assert (kwargs["max_steps"], kwargs["settings"], kwargs["tool_result_observer"]) == (3, CORE, _observer)
    assert (kwargs["screenshot_subdir"], kwargs["artifacts_subdir"], kwargs["driver_name"]) == ("shots", "out", "fake")
    assert [type(call.args[0]) for call in worker.register_rail.await_args_list] == [
        ActionPolicy,
        RepeatedCallGuardRail,
        InterceptedActionHintRail,
        FinalAnswerRecoveryRail,
    ]
    policy = worker.register_rail.await_args_list[0].args[0]
    assert policy._page_state is session.page_state
    assert MCP_PREFIX + "browser_click" in policy._builders
    assert set(policy._builders) <= {tool.card.name for tool in kwargs["tools"]}


def test_the_prompt_names_the_driver_by_its_display_name_and_the_model_uses_the_core_settings() -> None:
    worker = _build(_session("browser-use"))

    prompt = worker.config.prompt_template[0]["content"]
    assert "step-by-step with browser-use tools and approved runtime helper tools only." in prompt
    assert "required by a browser-use action." in prompt
    assert "shots/" in prompt and "out/" in prompt
    assert worker.config.max_iterations == 3
    assert (worker.config.model_config_obj.temperature, worker.config.model_config_obj.top_p) == (0.3, 0.9)


def test_the_parts_are_built_in_the_worker_language() -> None:
    with patch.object(agents_module, "build_agent_parts", wraps=agents_module.build_agent_parts) as parts:
        _build(_session())

    assert parts.call_args.kwargs["language"] == WORKER_LANGUAGE == "en"
    assert parts.call_args.kwargs["worker"] is True
