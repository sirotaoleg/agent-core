# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser helpers live on each driver against the conformance scene pages."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import Driver, Observe
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import create_browser_use_driver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.driver import create_playwright_mcp_driver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import result_text
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import split_sections
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolContext
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers import FindTool, ProbeInteractivesTool
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene import PAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene.server import SceneServer
from tests.unit_tests.harness.tools.browser_move.drivers.playwright_mcp.test_conformance_playwright_mcp_live import (
    _skip_reason as mcp_skip_reason,
)
from tests.unit_tests.harness.tools.browser_move.drivers.playwright_mcp.test_conformance_playwright_mcp_live import (
    mcp_settings,  # noqa: F401
)
from tests.unit_tests.harness.tools.browser_move.drivers.test_conformance_browser_use_live import (
    _skip_reason as browser_use_skip_reason,
)
from tests.unit_tests.harness.tools.browser_move.drivers.test_conformance_browser_use_live import (
    chrome_device,  # noqa: F401
)


@pytest.fixture(scope="module")
def scene_url() -> Iterator[str]:
    server = SceneServer(PAGES)
    base = server.start()
    try:
        yield base + "index.html"
    finally:
        server.stop()


def _on_page(driver: Driver, url: str, use: Callable[[ToolContext], Awaitable[Any]]) -> Any:
    async def run() -> Any:
        page_state = BrowserPageState()
        assembly = DriverAssembly(stack=driver, device=None, provisioner=None, surface_settings=None)
        session = DriverSession(assembly, page_state, MESSAGES)
        await driver.start()
        try:
            navigate = Navigate(url=url)
            page_state.record(navigate, await session.execute(navigate))
            context = ToolContext(session, page_state, BrowserRenderer(), driver.info, BrowserSettings(), False)
            return await use(context)
        finally:
            await driver.stop()

    return asyncio.run(run())


async def _probe(context: ToolContext) -> Any:
    return await ProbeInteractivesTool(context).invoke({"query": "save"})


def _assert_probe_found_the_save_button(output: Any) -> None:
    assert output.success, output.error
    saves = [element for element in output.data["elements"] if "Save order" in str(element.get("text"))]
    assert saves and saves[0]["target_id"].startswith(f"t_{output.data['generation_id']}_")


@pytest.mark.live
@pytest.mark.skipif(mcp_skip_reason() is not None, reason=mcp_skip_reason() or "")
class TestHelpersOnPlaywrightMcp:
    def test_probe_interactives_finds_the_save_button(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        mcp_settings: BrowserSettings,  # noqa: F811
        scene_url: str,
    ) -> None:
        driver = create_playwright_mcp_driver(chrome_device, mcp_settings)
        _assert_probe_found_the_save_button(_on_page(driver, scene_url, _probe))

    def test_find_answers_like_the_server(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        mcp_settings: BrowserSettings,  # noqa: F811
        scene_url: str,
    ) -> None:
        driver = create_playwright_mcp_driver(chrome_device, mcp_settings)
        queries = [{"text": "save order"}, {"regex": "/^\\s*- (link|button)/i"}, {"text": "nowhere to be seen"}]

        async def both(context: ToolContext) -> list[tuple[str, str]]:
            context.page_state.record(Observe(), await context.driver.observe(Observe()))
            answers = []
            for query in queries:
                ours = await FindTool(context).invoke(query)
                server = await driver._call("browser_find", query)
                answers.append((ours.data["content"], f"### Result\n{split_sections(result_text(server))['Result']}"))
            return answers

        for ours, server in _on_page(driver, scene_url, both):
            assert ours == server


@pytest.mark.live
@pytest.mark.skipif(browser_use_skip_reason() is not None, reason=browser_use_skip_reason() or "")
class TestHelpersOnBrowserUse:
    def test_probe_interactives_finds_the_save_button(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        scene_url: str,
    ) -> None:
        driver = create_browser_use_driver(chrome_device, BrowserSettings.from_process_env())
        _assert_probe_found_the_save_button(_on_page(driver, scene_url, _probe))
