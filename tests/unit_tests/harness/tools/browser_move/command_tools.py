# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser tools as ``CommandTool`` over ``FakeDriver``, with the Playwright MCP names and arguments."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    ContextRef,
    DriverInfo,
    Hover,
    Observation,
    Observe,
    PressKey,
    RunScript,
    SurfaceProfile,
    ToolSpec,
    TypeText,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import ToolFactory
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

COMMANDS = {
    "browser_click": (Click,),
    "browser_evaluate": (RunScript,),
    "browser_hover": (Hover,),
    "browser_navigate": (Navigate,),
    "browser_press_key": (PressKey,),
    "browser_snapshot": (Observe,),
    "browser_type": (TypeText,),
}
HOME = ContextRef("0", "tab", "Home")
SURFACE = BrowserContext(
    url="https://www.allrecipes.com/",
    title="Home",
    tabs=(HOME,),
    tab_urls=("https://www.allrecipes.com/",),
    current_tab=0,
)
_DEVICE = ProvisionedDevice(kind="fake-device", endpoint="fake://1", extras={})
_MESSAGES = {"driver_restarted": "restarted; observe again", "driver_lost": "the device is gone"}


def fake_driver(results: Iterable[ActionResult] = (), observations: Iterable[Observation] = ()) -> FakeDriver:
    info = DriverInfo(name="fake", surfaces=frozenset({"browser"}), actions={**DEFAULT_ACTIONS, Navigate: frozenset()})
    return FakeDriver(observations, results, info=info)


def command_tools(driver: FakeDriver) -> dict[str, Tool]:
    specs = tuple(
        ToolSpec(name, commands, name, schema={"type": "object"}, build=DECODERS[name])
        for name, commands in COMMANDS.items()
    )
    profile = SurfaceProfile(
        name="browser",
        commands=(),
        renderer=BrowserRenderer(),
        tool_specs=specs,
        capability_tools={"core": tuple(COMMANDS)},
    )
    page_state = BrowserPageState(page_id="page-test")
    session = DriverSession(DriverAssembly(driver, _DEVICE, None), page_state, _MESSAGES)
    built = ToolFactory(profile, driver.info).build(
        ("core",), image_support=False, session=session, page_state=page_state
    )
    return {tool.card.name: tool for tool in built.tools}


def result(value: Any = None, *, detail: str | None = None) -> ActionResult:
    return ActionResult(ok=True, value=value, detail=detail, surface=SURFACE)


def tool_message(tool: Tool, output: ToolOutput, call_id: str) -> ToolMessage:
    """The message the ability manager builds from ``output``."""
    return ToolMessage(content=tool.render_for_llm(output), tool_call_id=call_id, name=tool.card.name)
