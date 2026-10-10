# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser helpers live on each driver against the conformance scene pages."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import CloseContext, Driver, Observe, RunScript
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import create_browser_use_driver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.driver import create_playwright_mcp_driver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import result_text
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import split_sections
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolContext
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate, OpenContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers import (
    BatchTool,
    FindTool,
    ProbeInteractivesTool,
    activate_page,
    drag_and_drop,
    set_input_files,
)
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


async def _fill_save_and_read_back(context: ToolContext) -> tuple[Any, str]:
    probed = (await ProbeInteractivesTool(context).invoke({"max_items": 40, "viewport_only": False})).data["content"]
    page_state = context.page_state
    ids = {page_state.get_target(element["target_id"]).selector: element["target_id"] for element in probed["elements"]}
    steps = [
        {"op": "fill", "target_id": ids["#name"], "value": "Ada"},
        {"op": "set_checked", "target_id": ids["#gift"], "checked": True},
        {"op": "click", "target_id": ids["#save"]},
        {"op": "wait_for_selector", "selector": "#save"},
        {"op": "extract_value", "selector": "#name", "field": "name"},
    ]
    output = await BatchTool(context).invoke({"steps": steps, "generation_id": probed["generation_id"]})
    title = await context.execute(RunScript(code="() => document.title"))
    return output.data["content"], title.value


def _upload_then_drag(path: Path) -> Callable[[ToolContext], Awaitable[tuple[Any, ...]]]:
    async def run(context: ToolContext) -> tuple[Any, ...]:
        title = RunScript(code="() => document.title")
        uploaded = await set_input_files(context, selector="#upload", paths=[str(path)])
        uploaded_title = (await context.execute(title)).value
        dragged = await drag_and_drop(context, element_source="#drag-source", element_target="#drop-target")
        return uploaded, uploaded_title, dragged, (await context.execute(title)).value

    return run


def _assert_uploaded_then_dragged(outcome: tuple[Any, ...]) -> None:
    uploaded, uploaded_title, dragged, dragged_title = outcome
    assert uploaded["ok"] is True, uploaded
    assert uploaded_title == "upload:note.txt"
    assert dragged["ok"] is True, dragged
    assert dragged_title == "dragged"


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "note.txt"
    path.write_text("note", encoding="utf-8")
    return path


def _assert_the_batch_filled_saved_and_read_back(outcome: tuple[Any, str]) -> None:
    payload, title = outcome
    assert payload["ok"] is True, payload
    assert [step["status"] for step in payload["steps"]] == ["completed"] * 5
    assert payload["extracted"] == {"name": "Ada"}
    assert title == "clicked"


def _assert_probe_found_the_save_button(output: Any) -> None:
    assert output.success, output.error
    payload = output.data["content"]
    saves = [element for element in payload["elements"] if "Save order" in str(element.get("text"))]
    assert saves and saves[0]["target_id"].startswith(f"t_{payload['generation_id']}_")


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

    def test_batch_fills_checks_clicks_and_reads_back_in_one_call(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        mcp_settings: BrowserSettings,  # noqa: F811
        scene_url: str,
    ) -> None:
        driver = create_playwright_mcp_driver(chrome_device, mcp_settings)
        _assert_the_batch_filled_saved_and_read_back(_on_page(driver, scene_url, _fill_save_and_read_back))

    def test_custom_actions_upload_through_the_chooser_and_drag_between_elements(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        mcp_settings: BrowserSettings,  # noqa: F811
        scene_url: str,
        note: Path,
    ) -> None:
        driver = create_playwright_mcp_driver(chrome_device, mcp_settings)
        _assert_uploaded_then_dragged(_on_page(driver, scene_url, _upload_then_drag(note)))


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

    def test_activate_page_fronts_the_tab_at_an_address(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        scene_url: str,
    ) -> None:
        driver = create_browser_use_driver(chrome_device, BrowserSettings.from_process_env())
        second = scene_url.replace("index.html", "second.html")

        async def front_the_first(context: ToolContext) -> tuple[bool, BrowserContext]:
            await context.execute(OpenContext(second))
            listed = (await context.observe(Observe())).surface
            fronted = await activate_page(context, scene_url)
            surface = (await context.observe(Observe())).surface
            await context.execute(CloseContext(listed.tabs[listed.tab_urls.index(second)]))
            return fronted, surface

        fronted, surface = _on_page(driver, scene_url, front_the_first)
        assert fronted and surface.url == scene_url
        assert surface.tab_urls[surface.current_tab] == scene_url

    def test_batch_fills_checks_clicks_and_reads_back_in_one_call(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        scene_url: str,
    ) -> None:
        driver = create_browser_use_driver(chrome_device, BrowserSettings.from_process_env())
        _assert_the_batch_filled_saved_and_read_back(_on_page(driver, scene_url, _fill_save_and_read_back))

    def test_custom_actions_upload_on_the_input_and_drag_between_elements(
        self,
        chrome_device: ProvisionedDevice,  # noqa: F811
        scene_url: str,
        note: Path,
    ) -> None:
        driver = create_browser_use_driver(chrome_device, BrowserSettings.from_process_env())
        _assert_uploaded_then_dragged(_on_page(driver, scene_url, _upload_then_drag(note)))
