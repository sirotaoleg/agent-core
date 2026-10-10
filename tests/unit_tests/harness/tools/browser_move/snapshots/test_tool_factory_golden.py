# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The golden tool lists rebuilt from ``ToolFactory`` alone, every difference named."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.harness.tools.browser_move.contract import Driver, DriverInfo
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import BrowserUseDriver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.driver import create_playwright_mcp_driver
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolFactory
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES, create_browser_surface
from tests.unit_tests.harness.tools.browser_move.snapshots import builder

MCP_PREFIX = "mcp_playwright-official_"
DEVICE = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9222", extras={})
CAPABILITY_VIEWS = [view for view in builder.list_views() if view != builder.RUNNER_VIEW]

# Extra tools the worker removes after the build.
PROBES = frozenset({"browser_probe_interactives", "browser_probe_cards"})
# Golden tools no extra tool of the surface builds yet.
NOT_BUILT = frozenset({"browser_batch_interact", "browser_recall_offload"})
# Server tools with no command: dropped from the agent, each selection logged once.
NO_COMMAND: dict[str, frozenset[str]] = {
    "unsafe_dev": frozenset({"browser_run_code_unsafe"}),
    "vision": frozenset({"browser_mouse_down", "browser_mouse_up"}),
    "config": frozenset({"browser_get_config"}),
    "devtools": frozenset(
        {
            "browser_annotate",
            "browser_hide_highlight",
            "browser_highlight",
            "browser_resize",
            "browser_resume",
            "browser_start_tracing",
            "browser_start_video",
            "browser_stop_tracing",
            "browser_stop_video",
            "browser_video_chapter",
            "browser_video_hide_actions",
            "browser_video_show_actions",
        }
    ),
    "network": frozenset(
        {
            "browser_network_request",
            "browser_network_state_set",
            "browser_route",
            "browser_route_list",
            "browser_unroute",
        }
    ),
    "storage": frozenset(
        {
            "browser_cookie_clear",
            "browser_cookie_delete",
            "browser_cookie_get",
            "browser_cookie_list",
            "browser_cookie_set",
            "browser_localstorage_clear",
            "browser_localstorage_delete",
            "browser_localstorage_get",
            "browser_localstorage_list",
            "browser_localstorage_set",
            "browser_sessionstorage_clear",
            "browser_sessionstorage_delete",
            "browser_sessionstorage_get",
            "browser_sessionstorage_list",
            "browser_sessionstorage_set",
            "browser_set_storage_state",
            "browser_storage_state",
        }
    ),
    "testing": frozenset(
        {
            "browser_generate_locator",
            "browser_verify_element_visible",
            "browser_verify_list_visible",
            "browser_verify_text_visible",
            "browser_verify_value",
        }
    ),
}
# browser-use: run control is not a model tool; screenshot, close, drag and drop belong to their own capabilities.
BROWSER_USE_DROPPED = frozenset({"browser_cancel_run", "browser_clear_cancel"})
BROWSER_USE_OUTSIDE_CORE = frozenset({"browser_close", "browser_drag", "browser_drop"})
SCREENSHOT = "browser_take_screenshot"


def _session(driver: Driver) -> DriverSession:
    assembly = DriverAssembly(stack=driver, device=DEVICE, provisioner=None, surface_settings=None)
    return DriverSession(assembly, BrowserPageState(), MESSAGES)


def _built(profile: Any, driver: Driver, capabilities: list[str]) -> list[dict[str, Any]]:
    built = ToolFactory(profile, driver.info).build(
        capabilities, image_support=False, session=_session(driver), page_state=BrowserPageState()
    )
    return sorted((builder._tool_dict(tool.card.tool_info()) for tool in built.tools), key=lambda tool: tool["name"])


def _golden(directory: Path, view: str) -> list[dict[str, Any]]:
    return json.loads((directory / f"{view}.json").read_text(encoding="utf-8"))["agents"][0]["tools"]


def _capabilities(view: str) -> list[str]:
    return ["core"] if view in ("core", builder.RUNNER_VIEW) else ["core", view]


def _without(tools: list[dict[str, Any]], names: frozenset[str], prefix: str = "") -> list[dict[str, Any]]:
    return [tool for tool in tools if tool["name"].removeprefix(prefix) not in names]


@pytest.fixture(scope="module")
def surface(tmp_path_factory: pytest.TempPathFactory) -> Any:
    with builder.isolated_env(tmp_path_factory.mktemp("factory-golden")):
        return create_browser_surface(os.environ)


@pytest.fixture(scope="module")
def mcp_driver(surface: Any) -> Driver:
    import mcp
    import mcp.client.stdio

    fields = {"tools": builder.load_mcp_tools(), "calls": [], "replies": {}}
    session = type("Session", (builder.FakeMcpSession,), fields)
    driver = create_playwright_mcp_driver(DEVICE, surface.settings)
    with (
        patch.object(mcp, "ClientSession", session),
        patch.object(mcp.client.stdio, "stdio_client", builder._fake_stdio_client),
    ):
        asyncio.run(driver.start())
    return driver


@pytest.fixture(scope="module")
def mcp_info(mcp_driver: Driver) -> DriverInfo:
    return mcp_driver.info


def test_runner_tools_without_the_probes_equal_the_golden_byte_for_byte(surface: Any, mcp_driver: Driver) -> None:
    golden = _golden(builder.GOLDEN_DIR, builder.RUNNER_VIEW)
    built = _without(_built(surface.profile, mcp_driver, _capabilities(builder.RUNNER_VIEW)), PROBES)

    assert builder.render(built) == builder.render(golden)
    assert len(built) == len(golden) == 14


def test_core_tools_equal_the_golden_byte_for_byte_but_the_tools_not_built_yet(
    surface: Any, mcp_driver: Driver
) -> None:
    golden = _golden(builder.GOLDEN_DIR, "core")
    built = _built(surface.profile, mcp_driver, _capabilities("core"))

    assert builder.render(built) == builder.render(_without(golden, NOT_BUILT))
    assert len(built) == len(golden) - len(NOT_BUILT) == 16


@pytest.mark.parametrize("view", CAPABILITY_VIEWS)
def test_playwright_mcp_view_equals_the_golden_but_tools_not_built_and_tools_with_no_command(
    view: str, surface: Any, mcp_driver: Driver
) -> None:
    golden = _golden(builder.GOLDEN_DIR, view)
    expected = _without(_without(golden, NOT_BUILT), NO_COMMAND.get(view, frozenset()), MCP_PREFIX)

    assert builder.render(_built(surface.profile, mcp_driver, _capabilities(view))) == builder.render(expected)


@pytest.mark.parametrize("view", CAPABILITY_VIEWS)
def test_every_dropped_tool_is_a_golden_server_tool(view: str) -> None:
    names = {tool["name"].removeprefix(MCP_PREFIX) for tool in _golden(builder.GOLDEN_DIR, view)}
    assert NO_COMMAND.get(view, frozenset()) <= names


def test_mcp_tools_carry_the_server_prefix_description_and_schema(mcp_info: DriverInfo) -> None:
    listed = {tool["name"]: tool for tool in builder.load_mcp_tools()}
    assert mcp_info.tool_name_prefix == MCP_PREFIX
    for name, native in mcp_info.native_tools.items():
        assert native.description == listed[name]["description"]


@pytest.mark.parametrize("view", CAPABILITY_VIEWS)
def test_browser_use_view_equals_the_golden_but_the_one_catalog_differences(view: str, surface: Any) -> None:
    golden = {tool["name"]: tool for tool in _golden(builder.BROWSER_USE_GOLDEN_DIR, view)}
    built = {tool["name"]: tool for tool in _built(surface.profile, BrowserUseDriver(), _capabilities(view))}

    missing = set(golden) - set(built)
    expected_missing = BROWSER_USE_DROPPED | NOT_BUILT | {SCREENSHOT}
    if view != "extended_interaction":
        expected_missing |= BROWSER_USE_OUTSIDE_CORE
    assert missing == expected_missing & set(golden)
    assert set(built) - set(golden) == PROBES
    for name, tool in built.items():
        if name == "browser_tabs" or name in PROBES:
            continue
        assert tool == golden[name], name


def test_browser_use_tabs_differ_only_by_opening_a_tab(surface: Any) -> None:
    golden = {tool["name"]: tool for tool in _golden(builder.BROWSER_USE_GOLDEN_DIR, "core")}["browser_tabs"]
    built = {tool["name"]: tool for tool in _built(surface.profile, BrowserUseDriver(), ["core"])}["browser_tabs"]

    golden["parameters"]["properties"]["action"]["enum"].append("new")
    assert built == golden


def test_vision_offers_the_screenshot_only_with_image_support(surface: Any, mcp_driver: Driver) -> None:
    def names(image_support: bool) -> set[str]:
        built = ToolFactory(surface.profile, mcp_driver.info).build(
            ["core", "vision"], image_support=image_support, session=_session(mcp_driver), page_state=BrowserPageState()
        )
        return {tool.card.name.removeprefix(MCP_PREFIX) for tool in built.tools}

    assert names(True) - names(False) == {SCREENSHOT}
