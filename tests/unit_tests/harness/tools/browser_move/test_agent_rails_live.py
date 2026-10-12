# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The core rails and the worker live on each driver against the scene, driven by a scripted model."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, ModelClientConfig, ModelRequestConfig, ToolCall
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.runtime.assembly import build_agent
from openjiuwen.harness.tools.browser_move.runtime.config import (
    BrowserRunGuardrails,
    CoreSettings,
    playwright_mcp_config,
)
from openjiuwen.harness.tools.browser_move.runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.runtime.rails.final_answer_recovery import (
    NON_JSON_OUTPUT,
    RECOVERY_MESSAGE,
)
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import CORE_BROWSER_TOOL_NAMES
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene import PAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene.server import SceneServer
from tests.unit_tests.harness.tools.browser_move.drivers.playwright_mcp.test_conformance_playwright_mcp_live import (
    SERVER_ARGS,
)
from tests.unit_tests.harness.tools.browser_move.drivers.playwright_mcp.test_conformance_playwright_mcp_live import (
    _skip_reason as mcp_skip_reason,
)
from tests.unit_tests.harness.tools.browser_move.drivers.test_conformance_browser_use_live import (
    _skip_reason as browser_use_skip_reason,
)
from tests.unit_tests.harness.tools.browser_move.drivers.test_conformance_browser_use_live import (
    chrome_device,  # noqa: F401
)

Step = Callable[[], tuple[str, dict[str, Any]]]

HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}
TITLE = "() => document.title"
COVER = "() => { cover(); return true; }"
NOTE = "### Repeated call"
ANSWER = json.dumps({"ok": True, "final": "The scene is open.", "page": {}, "screenshot": None, "error": None})
# How each dialect addresses an element the driver listed: the MCP tools take the ref as ``target``.
CLICK: dict[str, Callable[[str, BrowserPageState], dict[str, Any]]] = {
    "playwright_mcp": lambda ref, page_state: {"target": ref},
    "browser_use": lambda ref, page_state: {"ref": ref, "generation_id": page_state.generation_id},
}


class ScriptedModel(Model):
    """One scripted tool call per turn while tools are offered, then the answers in order; keeps every request."""

    def __init__(self, steps: list[Step], answers: tuple[str, ...] = ("done",)) -> None:
        super().__init__(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="mock-api-key", api_base="http://mock.invalid", verify_ssl=False
            ),
            model_config=ModelRequestConfig(model="scripted"),
        )
        self.steps = list(steps)
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        self.requests.append(kwargs)
        tools = kwargs.get("tools") or []
        if tools and self.steps:
            suffix, arguments = self.steps.pop(0)()
            name = next(tool.name for tool in tools if tool.name.endswith(suffix))
            call = ToolCall(
                id=f"call-{len(self.requests)}", type="function", name=name, arguments=json.dumps(arguments)
            )
            return AssistantMessage(content="", tool_calls=[call])
        return AssistantMessage(content=self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])


@pytest.fixture(scope="module")
def scene_url() -> Iterator[str]:
    server = SceneServer(PAGES)
    base = server.start()
    try:
        yield base + "index.html"
    finally:
        server.stop()


def _step(name: str, arguments: dict[str, Any]) -> Step:
    return lambda: (name, arguments)


def _tool_messages(request: dict[str, Any]) -> list[str]:
    return [message.content for message in request["messages"] if getattr(message, "role", "") == "tool"]


def _ref_of(page_state: BrowserPageState, name: str) -> str:
    return next(element.ref.ref for element in page_state.last_observation.elements if element.name == name)


def _run_agent(environ: dict[str, str], model: ScriptedModel, seen: dict[str, Any]) -> None:
    async def scenario() -> None:
        agent, session = await build_agent(
            CoreSettings.from_env(environ),
            environ,
            model=model,
            capabilities=("core",),
            image_support=False,
            language="en",
            enable_task_loop=False,
            enable_read_image_multimodal=False,
            max_iterations=12,
        )
        seen["page_state"] = session.page_state
        try:
            await agent.invoke({"query": "Work the scene", "conversation_id": f"rails-live-{uuid.uuid4().hex}"})
        finally:
            await session.close()

    asyncio.run(scenario())


class _OnEachDriver:
    driver = ""

    @pytest.fixture
    def environ(self, chrome_device: ProvisionedDevice, tmp_path: Path) -> dict[str, str]:  # noqa: F811
        (tmp_path / "mcp").mkdir()
        return {
            **os.environ,
            "BROWSER_DRIVER": self.driver,
            "BROWSER_PROVISIONING": "remote",
            "PLAYWRIGHT_MCP_CDP_ENDPOINT": chrome_device.endpoint,
            "PLAYWRIGHT_MCP_ARGS": SERVER_ARGS,
            "PLAYWRIGHT_RUNTIME_MCP_CWD": str(tmp_path / "mcp"),
        }

    def test_identical_calls_lose_the_tools_after_the_fifth(self, environ: dict[str, str], scene_url: str) -> None:
        steps = [_step("browser_navigate", {"url": scene_url})] + [_step("browser_evaluate", {"function": TITLE})] * 5
        model = ScriptedModel(steps)

        _run_agent(environ, model, {})

        assert [bool(request.get("tools")) for request in model.requests] == [True] * 6 + [False]
        assert NOTE in _tool_messages(model.requests[6])[-1]

    def test_a_click_on_a_covered_element_gets_the_hint(self, environ: dict[str, str], scene_url: str) -> None:
        seen: dict[str, Any] = {}

        def covered() -> tuple[str, dict[str, Any]]:
            page_state = seen["page_state"]
            return "browser_click", CLICK[self.driver](_ref_of(page_state, "Covered button"), page_state)

        model = ScriptedModel(
            [
                _step("browser_navigate", {"url": scene_url}),
                _step("browser_snapshot", {}),
                _step("browser_evaluate", {"function": COVER}),
                covered,
            ]
        )

        _run_agent(environ, model, seen)

        explained = _tool_messages(model.requests[4])[-1]
        assert explained.startswith("### Error\nClick not delivered: ") and " is covered by " in explained, explained
        assert explained.endswith(MESSAGES["retry"]), explained

    def test_the_worker_has_the_core_tools_and_the_helpers_and_recovers_a_prose_answer(
        self, environ: dict[str, str], scene_url: str
    ) -> None:
        async def scenario() -> tuple[str, set[str], ScriptedModel, dict[str, Any]]:
            settings = BrowserSettings.from_env(environ)
            runtime = BrowserAgentRuntime(
                provider="openai",
                api_key="mock-api-key",
                api_base="https://example.invalid/v1",
                model_name="mock-model",
                mcp_cfg=playwright_mcp_config(settings),
                guardrails=BrowserRunGuardrails(max_steps=4, max_failures=1, timeout_s=120),
                browser_settings=settings,
                core_settings=dataclasses.replace(CoreSettings.from_env(environ), topology="delegated_worker"),
            )
            await runtime.ensure_started()
            try:
                service = runtime.service
                worker = service.browser_agent
                prefix = service.session.assembly.stack.info.tool_name_prefix
                names = {tool.name for tool in await worker.ability_manager.list_tool_info()}
                model = ScriptedModel([_step("browser_navigate", {"url": scene_url})], ("I opened the scene.", ANSWER))
                worker.set_llm(model)

                async def current() -> Any:
                    return service.browser_agent

                result = await service.run_task_once(
                    task="Open the scene", session_id="rails-live", request_id="r-1", worker=current
                )
                return prefix, names, model, result
            finally:
                await runtime.shutdown()

        prefix, names, model, result = asyncio.run(scenario())

        assert {name.removeprefix(prefix) for name in names} == set(CORE_BROWSER_TOOL_NAMES) | HELPERS
        assert (result["ok"], result["recovered_from"]) == (True, NON_JSON_OUTPUT), result
        assert [bool(request.get("tools")) for request in model.requests] == [True, True, False]
        assert RECOVERY_MESSAGE in str(model.requests[2]["messages"][-1].content)


@pytest.mark.live
@pytest.mark.skipif(mcp_skip_reason() is not None, reason=mcp_skip_reason() or "")
class TestOnPlaywrightMcp(_OnEachDriver):
    driver = "playwright_mcp"


@pytest.mark.live
@pytest.mark.skipif(browser_use_skip_reason() is not None, reason=browser_use_skip_reason() or "")
class TestOnBrowserUse(_OnEachDriver):
    driver = "browser_use"
