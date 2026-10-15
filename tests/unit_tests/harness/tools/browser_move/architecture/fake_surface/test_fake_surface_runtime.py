# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The screen surface runs a task through the core runtime on both topologies, with no browser input anywhere."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, ModelClientConfig, ModelRequestConfig, ToolCall
from openjiuwen.harness.tools.browser_move.contract import ActionResult, Click, PointRef, SurfaceBundle
from openjiuwen.harness.tools.browser_move.runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.runtime import topology as topology_module
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings, RunGuardrails
from openjiuwen.harness.tools.browser_move.runtime.page_state import PageState
from openjiuwen.harness.tools.browser_move.runtime.session import AgentRuntime
from openjiuwen.harness.tools.browser_move.runtime.topology import DelegatedWorker, Direct

from .surface import DEVICE, PROFILE, Poke, ScreenDriver, ScreenProvisioner, screen_registries

SCRIPT = (("look", {}), ("poke", {"x": 10, "y": 10}), ("look", {}), ("click", {"x": 3, "y": 4}))
ANSWERS = {"direct": "poked", "delegated_worker": json.dumps({"ok": True, "final": "poked"})}
TOPOLOGIES = {"direct": Direct, "delegated_worker": DelegatedWorker}


class ScriptedModel(Model):
    """One scripted tool call per turn, then ``answer``; keeps every request it was sent."""

    def __init__(self, answer: str) -> None:
        super().__init__(
            model_client_config=ModelClientConfig(
                client_provider="openai", api_key="mock-api-key", api_base="http://mock.invalid", verify_ssl=False
            ),
            model_config=ModelRequestConfig(model="scripted"),
        )
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        self.requests.append(kwargs)
        turn = len(self.requests)
        if turn > len(SCRIPT):
            return AssistantMessage(content=self.answer)
        name, arguments = SCRIPT[turn - 1]
        call = ToolCall(id=f"call-{turn}", type="function", name=name, arguments=json.dumps(arguments))
        return AssistantMessage(content="", tool_calls=[call])


class Run:
    def __init__(self, topology: str, tmp_path: Path) -> None:
        self.driver = ScreenDriver([ActionResult(ok=True, context_changed=True), ActionResult(ok=True)])
        self.provisioner = ScreenProvisioner()
        self.model = ScriptedModel(ANSWERS[topology])
        self.output_dir = tmp_path / "out"
        self.runtime = AgentRuntime(
            CoreSettings(driver="screen_driver", surface="fake", topology=topology),
            SurfaceBundle(PROFILE.settings, PROFILE),
            provider="openai",
            api_key="mock-api-key",
            api_base="http://mock.invalid",
            model_name="scripted",
            guardrails=RunGuardrails(max_steps=len(SCRIPT) + 2, max_failures=1, timeout_s=30, retry_once=False),
            workspace=str(tmp_path / "ws"),
            language="en",
            output_dir=self.output_dir,
            identity="screen:test",
        )
        self.answer: dict[str, Any] = {}
        self.topology: type | None = None

    async def scenario(self) -> None:
        registries = screen_registries(self.driver, self.provisioner)
        build_worker = topology_module.build_worker

        async def scripted_worker(*args: Any, **kwargs: Any) -> Any:
            worker = await build_worker(*args, **kwargs)
            worker.set_llm(self.model)
            return worker

        with (
            patch.object(assembly_module, "DRIVERS", registries.drivers),
            patch.object(assembly_module, "SURFACES", registries.surfaces),
            patch.object(assembly_module, "PROVISIONERS", registries.provisioners),
            patch.object(topology_module, "_model", return_value=self.model),
            patch.object(topology_module, "probe_image_support", AsyncMock(return_value=True)),
            patch.object(topology_module, "build_worker", scripted_worker),
        ):
            await self.runtime.ensure_started()
            try:
                self.answer = await self.runtime.run_task("Poke the board", session_id="screen")
                self.topology = type(self.runtime.topology)
            finally:
                await self.runtime.shutdown()


@pytest.fixture(params=sorted(TOPOLOGIES))
def run(request: pytest.FixtureRequest, tmp_path: Path) -> Run:
    run = Run(request.param, tmp_path)
    asyncio.run(run.scenario())
    return run


def test_the_task_runs_to_its_answer_on_the_core_page_state(run: Run) -> None:
    assert (run.answer["ok"], run.answer["final"], run.answer["session_id"]) == (True, "poked", "screen")
    assert run.answer["error"] is None
    assert type(run.runtime.page_state) is PageState
    assert run.topology is TOPOLOGIES[run.runtime.core.topology]


def test_the_answer_carries_the_screen_identity_and_no_metadata(run: Run) -> None:
    assert run.answer["context"] == {"identity": "board", "metadata": {}}


def test_the_model_called_the_surface_tools_and_the_driver_saw_the_commands(run: Run) -> None:
    assert len(run.model.requests) == len(SCRIPT) + 1
    assert {tool.name for tool in run.model.requests[0]["tools"]} == {"look", "click", "poke"}
    assert Poke(PointRef(10, 10, "g0")) in run.driver.calls
    assert Click(PointRef(3, 4, "g1")) in run.driver.calls


def test_the_device_is_provisioned_once_and_released_at_shutdown(run: Run) -> None:
    assert run.driver.calls.count("start") == 1 and run.driver.calls[-1] == "stop"
    assert [entry[0] for entry in run.provisioner.log] == ["provision", "release"]
    assert run.provisioner.log[-1] == ("release", DEVICE)


def test_the_output_folder_and_the_identity_are_the_given_ones(run: Run) -> None:
    assert (run.output_dir / "screenshots").is_dir()
    assert run.runtime.service.identity == "screen:test"
