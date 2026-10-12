# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Each driver finishes one task through the facade in both topologies, on the scene with a scripted model."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import model as model_module
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.harness import deep_agent as deep_agent_module

# The conftests snapshot the context-processor registry per test; the browser processors must be registered
# before the first test starts, not by Direct.start's lazy import during it.
from openjiuwen.harness.subagents import browser_agent  # noqa: F401
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.runtime.config import (
    BrowserRunGuardrails,
    CoreSettings,
    playwright_mcp_config,
)
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import CORE_BROWSER_TOOL_NAMES
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
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
from tests.unit_tests.harness.tools.browser_move.test_agent_rails_live import ScriptedModel, _step, _tool_messages

# The helpers agent-eval's WebVoyager runner strips from the worker after ensure_started().
RUNNER_HELPERS = (
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_batch_interact",
    "browser_custom_action",
    "browser_list_custom_actions",
)
MAIN_AGENT_HELPERS = {
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_batch_interact",
    "browser_recall_offload",
}
FINAL = "The scene is open."
# The scripted model has no image input, so the factory leaves the screenshot out.
CORE_WITHOUT_IMAGES = tuple(name for name in CORE_BROWSER_TOOL_NAMES if name != "browser_take_screenshot")
WORKER_ANSWER = json.dumps(
    {
        "ok": True,
        "final": FINAL,
        "page": {"url": "", "title": ""},
        "screenshot": None,
        "error": None,
        "status": "completed",
    }
)


class ScriptedClient(BaseModelClient):
    """The scripted model at the client level: the main agent's model is a copy, so the script lives in the client."""

    __client_name__ = "topology-live"
    script: ScriptedModel

    async def invoke(self, messages, *, tools=None, **kwargs):
        return await self.script.invoke(messages=messages, tools=tools)

    async def stream(self, messages, *, tools=None, **kwargs):
        message = await self.script.invoke(messages=messages, tools=tools)
        yield AssistantMessageChunk(content=message.content)

    async def generate_image(self, *args, **kwargs):
        raise RuntimeError("scripted client: generate_image not supported")

    async def generate_speech(self, *args, **kwargs):
        raise RuntimeError("scripted client: generate_speech not supported")

    async def generate_video(self, *args, **kwargs):
        raise RuntimeError("scripted client: generate_video not supported")


def _scripted_client_factory(model: ScriptedModel):
    def create(client_config, model_config):
        client = ScriptedClient(model_config=model_config, model_client_config=client_config)
        client.script = model
        return client

    return create


@pytest.fixture(scope="module")
def scene_url() -> Iterator[str]:
    server = SceneServer(PAGES)
    base = server.start()
    try:
        yield base + "index.html"
    finally:
        server.stop()


def _runtime(environ: dict[str, str], topology: str) -> BrowserAgentRuntime:
    settings = BrowserSettings.from_env(environ)
    return BrowserAgentRuntime(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        mcp_cfg=playwright_mcp_config(settings),
        guardrails=BrowserRunGuardrails(max_steps=4, max_failures=1, timeout_s=120),
        browser_settings=settings,
        core_settings=dataclasses.replace(CoreSettings.from_env(environ), topology=topology),
    )


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

    def test_the_worker_finishes_a_task_through_the_facade(self, environ: dict[str, str], scene_url: str) -> None:
        model = ScriptedModel([_step("browser_navigate", {"url": scene_url})], (WORKER_ANSWER,))

        async def scenario() -> tuple[str, dict[str, Any]]:
            runtime = _runtime(environ, "delegated_worker")
            await runtime.ensure_started()
            try:
                service = runtime.service
                worker = service.browser_agent
                prefix = service.session.assembly.stack.info.tool_name_prefix
                worker.set_llm(model)
                for name in RUNNER_HELPERS:
                    try:
                        worker.ability_manager.remove_ability(name)
                    except Exception:  # noqa: BLE001 - the runner swallows these too
                        pass
                answer = await runtime.run_browser_task(task="Open the scene", session_id="topology-live")
                return prefix, answer
            finally:
                await runtime.shutdown()

        prefix, answer = asyncio.run(scenario())

        sent = {tool.name for tool in model.requests[0]["tools"]}
        assert sent == {prefix + name for name in CORE_WITHOUT_IMAGES}, sent
        assert not _tool_messages(model.requests[1])[-1].startswith("### Error"), _tool_messages(model.requests[1])
        assert (answer["ok"], answer["final"], answer["error"], answer["screenshot"]) == (True, FINAL, None, None)
        assert {"ok", "final", "error", "screenshot", "page", "session_id", "request_id"} <= set(answer), answer

    def test_the_main_agent_finishes_a_task_on_the_facade(self, environ: dict[str, str], scene_url: str) -> None:
        model = ScriptedModel([_step("browser_navigate", {"url": scene_url})], (FINAL,))

        async def scenario() -> tuple[str, dict[str, Any], Any]:
            runtime = _runtime(environ, "direct")
            with (
                patch.object(model_module, "create_model_client", _scripted_client_factory(model)),
                patch.object(deep_agent_module, "schedule_image_support_probe"),
            ):
                await runtime.ensure_started()
                try:
                    worker = runtime.service.browser_agent
                    answer = await runtime.run_browser_task(task="Open the scene", session_id="topology-live")
                    prefix = runtime.service.session.assembly.stack.info.tool_name_prefix
                    return prefix, answer, worker
                finally:
                    await runtime.shutdown()

        prefix, answer, worker = asyncio.run(scenario())

        assert worker is None, "the main agent calls the tools itself; the runner's helper removal is skipped"
        assert (answer["ok"], answer["error"], answer["screenshot"]) == (True, None, None), answer
        assert FINAL in answer["final"], answer
        sent = {tool.name for tool in model.requests[0]["tools"]}
        assert {prefix + name for name in CORE_WITHOUT_IMAGES} | MAIN_AGENT_HELPERS <= sent, sent
        assert not _tool_messages(model.requests[1])[-1].startswith("### Error"), _tool_messages(model.requests[1])
        assert {"ok", "final", "error", "screenshot", "page", "session_id", "request_id"} <= set(answer), answer
        assert scene_url.rsplit("/", 1)[0] in answer["page"]["url"], answer["page"]


@pytest.mark.live
@pytest.mark.skipif(mcp_skip_reason() is not None, reason=mcp_skip_reason() or "")
class TestOnPlaywrightMcp(_OnEachDriver):
    driver = "playwright_mcp"


@pytest.mark.live
@pytest.mark.skipif(browser_use_skip_reason() is not None, reason=browser_use_skip_reason() or "")
class TestOnBrowserUse(_OnEachDriver):
    driver = "browser_use"
