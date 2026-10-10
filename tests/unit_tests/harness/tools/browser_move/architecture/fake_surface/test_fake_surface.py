# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A new surface, driver and provisioner reach a running agent through registries alone, with no core change."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, ModelClientConfig, ModelRequestConfig, ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    ConnectionLost,
    Observe,
    PointRef,
    PolicyDenied,
    StaleRef,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import build_agent
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import command_record

from .surface import DEVICE, MESSAGES, Poke, ScreenDriver, ScreenProvisioner, screen_registries

SCRIPT = (
    ("look", {}),
    ("poke", {"x": 500, "y": 5}),
    ("click", {"x": 3, "y": 4}),
    ("poke", {"x": 10, "y": 10}),
    ("look", {}),
    ("click", {"x": 3, "y": 4}),
)


class ScriptedModel(Model):
    """Calls the scripted tools one per turn, then answers; keeps every request it was sent."""

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
        turn = len(self.requests)
        if turn > len(SCRIPT):
            return AssistantMessage(content="done")
        name, arguments = SCRIPT[turn - 1]
        call = ToolCall(id=f"call-{turn}", type="function", name=name, arguments=json.dumps(arguments))
        return AssistantMessage(content="", tool_calls=[call])


class ToolResults(AgentRail):
    def __init__(self) -> None:
        super().__init__()
        self.by_call: dict[str, Any] = {}

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        self.by_call[ctx.inputs.tool_call.id] = ctx.inputs.tool_result


@dataclass
class Run:
    model: ScriptedModel = field(default_factory=ScriptedModel)
    driver: ScreenDriver = field(
        default_factory=lambda: ScreenDriver(
            [ActionResult(ok=True, context_changed=True), ConnectionLost("the cable came out", dispatched=True)]
        )
    )
    provisioner: ScreenProvisioner = field(default_factory=ScreenProvisioner)
    results: ToolResults = field(default_factory=ToolResults)
    output: Any = None

    def tool_message(self, turn: int) -> str:
        """What the model read for the tool it called on ``turn`` (1-based)."""
        messages = [message.content for message in self.model.requests[turn]["messages"] if message.role == "tool"]
        return messages[-1]


@pytest.fixture(scope="module")
def run() -> Run:
    run = Run()

    async def scenario() -> None:
        agent, session = await build_agent(
            CoreSettings(driver="screen_driver", surface="fake"),
            {},
            model=run.model,
            capabilities=("core",),
            image_support=True,
            language="en",
            registries=screen_registries(run.driver, run.provisioner),
            enable_task_loop=False,
            enable_read_image_multimodal=False,
            max_iterations=len(SCRIPT) + 2,
        )
        agent.add_rail(run.results)
        try:
            run.output = await agent.invoke({"query": "Poke the board", "conversation_id": "fake-surface"})
        finally:
            await session.close()

    asyncio.run(scenario())
    return run


def _schemas(request: dict[str, Any]) -> dict[str, Any]:
    return {tool.name: tool.parameters for tool in request["tools"]}


def _images(request: dict[str, Any]) -> list[str]:
    return [
        part["image_url"]["url"]
        for message in request["messages"]
        if message.role == "user" and isinstance(message.content, list)
        for part in message.content
        if part.get("type") == "image_url"
    ]


def test_the_scripted_task_runs_to_its_answer(run: Run) -> None:
    assert run.output["output"] == "done"
    assert len(run.model.requests) == len(SCRIPT) + 1


def test_the_model_is_offered_the_surface_tools_with_point_arguments(run: Run) -> None:
    schemas = _schemas(run.model.requests[0])

    assert set(schemas) == {"look", "click", "poke"}
    assert schemas["poke"]["properties"] == {"x": {"type": "integer"}, "y": {"type": "integer"}}
    assert schemas["click"]["properties"] == {"x": {"type": "integer"}, "y": {"type": "integer"}}


def test_the_model_receives_the_screenshot_as_an_image(run: Run) -> None:
    first_shot = "data:image/png;base64," + base64.b64encode(b"\x89PNG-1").decode("ascii")

    assert _images(run.model.requests[0]) == []
    assert _images(run.model.requests[1]) == [first_shot]


def test_a_poke_off_the_board_is_denied_before_the_driver(run: Run) -> None:
    assert isinstance(run.results.by_call["call-2"], PolicyDenied)
    assert run.tool_message(2) == "### Error\n(500, 5) is off the board"
    assert not [call for call in run.driver.calls if isinstance(call, Poke)]


def test_a_stale_point_observes_once_and_reports_the_fresh_observation(run: Run) -> None:
    click = run.driver.calls.index(Click(PointRef(3, 4, "g0")))
    next_look = run.driver.calls.index(Observe(include_screenshot=True), click)

    assert run.driver.calls[click + 1 : next_look] == [Observe()]
    assert command_record(run.results.by_call["call-4"]).error == StaleRef.__name__
    assert run.tool_message(4) == (
        "### Error\nStaleRef: Stale PointRef belongs to g0; current generation is g1.\nfresh look at screen-1 in g1"
    )


def test_a_lost_connection_restarts_the_driver_once(run: Run) -> None:
    last_click = run.driver.calls.index(Click(PointRef(3, 4, "g1")))

    assert run.driver.calls[last_click + 1 :] == ["stop", "start", "stop"]
    assert run.driver.calls.count("start") == 2
    assert run.tool_message(6) == f"### Error\nConnectionLost: {MESSAGES['driver_restarted']}"
    assert [entry[0] for entry in run.provisioner.log] == ["provision", "release"]
    assert run.provisioner.log[-1] == ("release", DEVICE)
