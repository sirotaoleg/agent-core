# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``build_worker``: the worker's tools, rails and prompt from a started session and the driver's display name."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, ModelClientConfig, ModelRequestConfig, ToolCall
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import _screenshot as decode_screenshot
from openjiuwen.harness.tools.browser_move.runtime import agents as agents_module
from openjiuwen.harness.tools.browser_move.runtime.agents import WORKER_LANGUAGE, build_worker, default_worker_prompt
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.runtime.rails.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.runtime.rails.final_answer_recovery import FinalAnswerRecoveryRail
from openjiuwen.harness.tools.browser_move.runtime.rails.intercepted_action import InterceptedActionHintRail
from openjiuwen.harness.tools.browser_move.runtime.rails.repeated_call_guard import RepeatedCallGuardRail
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import CORE_BROWSER_TOOL_NAMES
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    MCP_PREFIX,
    fake_assembly,
    mcp_driver_info,
    mcp_fake_driver,
    observation,
)

SCREENSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "screenshot_element.json"
WORKER_HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}
CORE = CoreSettings(worker_temperature=0.3, worker_top_p=0.9)


async def _observer(tool_name: str, tool_result: Any) -> None:
    del tool_name, tool_result


def _session(display_name: str = "") -> DriverSession:
    info = dataclasses.replace(mcp_driver_info(), display_name=display_name)
    assembly = fake_assembly(FakeDriver(info=info))
    return DriverSession(assembly, BrowserPageState(), assembly.surface.profile.messages)


def _build(session: DriverSession, **options: Any) -> Any:
    kwargs: dict[str, Any] = {
        "provider": "openai",
        "api_key": "test-key",
        "api_base": "https://example.invalid/v1",
        "model_name": "test-model",
        "max_steps": 3,
        "core": CORE,
        "capabilities": ("core",),
        "tool_result_observer": _observer,
        "screenshot_subdir": "shots",
        "artifacts_subdir": "out",
    }
    return asyncio.run(build_worker(session, session.assembly.surface, **(kwargs | options)))


def test_the_worker_gets_the_core_tools_the_probes_the_batch_and_find_but_no_recall() -> None:
    worker = _build(_session())

    names = {tool.name for tool in asyncio.run(worker.ability_manager.list_tool_info())}
    core = {MCP_PREFIX + name for name in CORE_BROWSER_TOOL_NAMES} - {MCP_PREFIX + "browser_take_screenshot"}
    assert names == core | WORKER_HELPERS


def test_the_worker_card_names_the_session_driver() -> None:
    worker = _build(_session("Playwright MCP"))

    assert worker.card.description == "Worker that executes tasks using Playwright MCP tools."


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
    assert kwargs["worker_prompt"] is session.assembly.surface.profile.worker_prompt
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


@pytest.mark.parametrize("image_support", [True, False])
def test_the_worker_gets_the_screenshot_tool_only_with_image_support(image_support: bool) -> None:
    options = {"image_support": image_support} if image_support else {}
    with patch.object(agents_module, "build_agent_parts", wraps=agents_module.build_agent_parts) as parts:
        worker = _build(_session(), **options)

    assert parts.call_args.kwargs["image_support"] is image_support
    names = {tool.name for tool in asyncio.run(worker.ability_manager.list_tool_info())}
    assert (MCP_PREFIX + "browser_take_screenshot" in names) is image_support


def test_a_surface_without_a_worker_prompt_gets_the_neutral_one() -> None:
    session = _session("Playwright MCP")
    profile = dataclasses.replace(session.assembly.surface.profile, worker_prompt=None)
    bundle = dataclasses.replace(session.assembly.surface, profile=profile)
    worker = asyncio.run(
        build_worker(
            session,
            bundle,
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

    prompt = worker.config.prompt_template[0]["content"]
    assert prompt == default_worker_prompt("shots", "out", driver_name="Playwright MCP")
    assert "browser" not in prompt.lower()


class _ScreenshotModel(Model):
    """Asks for one screenshot, then answers."""

    def __init__(self) -> None:
        super().__init__(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="mock-api-key", api_base="http://mock.invalid", verify_ssl=False
            ),
            model_config=ModelRequestConfig(model="scripted"),
        )
        self.requests: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            call = ToolCall(id="c1", type="function", name=MCP_PREFIX + "browser_take_screenshot", arguments="{}")
            return AssistantMessage(content="", tool_calls=[call])
        return AssistantMessage(content="done")


def test_a_worker_with_images_sends_the_servers_screenshot_as_a_valid_png_image() -> None:
    recorded = json.loads(SCREENSHOT_FIXTURE.read_text(encoding="utf-8"))["raw_result"]["content"][1]["data"]
    png = base64.b64decode(recorded)
    driver = mcp_fake_driver([observation(screenshot=decode_screenshot(base64.b64encode(png).decode("ascii")))])
    assembly = fake_assembly(driver)
    session = DriverSession(assembly, BrowserPageState(), assembly.surface.profile.messages)
    worker = _build(session, image_support=True)
    model = _ScreenshotModel()
    worker.set_llm(model)

    asyncio.run(worker.invoke({"query": "take a screenshot", "conversation_id": "c"}))

    messages = model.requests[1]["messages"]
    parts = [part for message in messages if isinstance(message.content, list) for part in message.content]
    urls = [part["image_url"]["url"] for part in parts if part.get("type") == "image_url"]
    assert len(urls) == 1 and urls[0].startswith("data:image/png;base64,")
    assert base64.b64decode(urls[0].partition(",")[2], validate=True) == png
