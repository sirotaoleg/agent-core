# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``PlaywrightMcpDriver`` on a fake MCP session: tool calls sent, observation, lifecycle, context tracking, factory."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    ActionTimeout,
    Click,
    CloseContext,
    ConnectionLost,
    ContextRef,
    Drag,
    DriverError,
    DriverRef,
    HandleDialog,
    Health,
    Hover,
    IndexRef,
    Locator,
    Observation,
    Observe,
    PointRef,
    PressKey,
    RunScript,
    Scroll,
    StaleRef,
    SwitchContext,
    TypeText,
    Unsupported,
    Wait,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp import driver as driver_module
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.clients import BrowserMoveStdioClient
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.driver import (
    PlaywrightMcpConfig,
    PlaywrightMcpDriver,
    create_playwright_mcp_driver,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import result_text
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import without_boxes
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
    build_playwright_mcp_config,
)
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FieldKind,
    FillForm,
    FormField,
    GetConsole,
    GetNetwork,
    GetPdf,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from tests.unit_tests.harness.tools.browser_move.snapshots.builder import FakeMcpSession, load_mcp_tools

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "mcp"
TIMESTAMP = "2026-10-10T00-00-00-000Z"
SNAPSHOT_FILE = Path(".playwright-mcp") / f"page-{TIMESTAMP}.yml"
OK = {
    "content": [
        {
            "type": "text",
            "text": (
                "### Ran Playwright code\n```js\nawait page.x();\n```\n### Page\n- Page URL: http://x/\n- Page Title: X"
            ),
        }
    ],
    "isError": False,
}

E12 = DriverRef("e12", "g3")
E5 = DriverRef("e5", "g3")
CSS = Locator("#save", None, "g3")
POINT = PointRef(10, 20, "g3")
POINT_2 = PointRef(30, 40, "g3")
TAB_1 = ContextRef(id="1", kind="tab", title="Second")

HANG = "hang"

Call = tuple[str, dict[str, Any]]


def _tab(index: int, title: str) -> ContextRef:
    return ContextRef(id=str(index), kind="tab", title=title)


def _raw(case: str) -> dict[str, Any]:
    fixture = json.loads((FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8"))
    return json.loads(json.dumps(fixture["raw_result"]).replace("<TS>", TIMESTAMP))


def _inline_yaml(case: str) -> str:
    text = _raw(case)["content"][0]["text"]
    return text.split("```yaml\n", 1)[1].rsplit("```", 1)[0]


class _Session(FakeMcpSession):
    """Replies per tool name with a raw result, a list of them in order, or an exception to raise."""

    broken = False

    async def initialize(self) -> Any:
        return None

    async def list_tools(self) -> Any:
        if self.broken:
            raise RuntimeError("server gone")
        return await super().list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        from mcp import types

        del kwargs
        self.calls.append((name, dict(arguments or {})))
        reply = self.replies[name]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply == HANG:
            await asyncio.sleep(3600)
        return types.CallToolResult.model_validate(reply)


@contextlib.asynccontextmanager
async def _no_process(*args: Any, **kwargs: Any):
    del args, kwargs
    yield None, None


@contextlib.contextmanager
def _mcp(replies: dict[str, Any]) -> Iterator[list[Call]]:
    import mcp
    import mcp.client.stdio

    calls: list[Call] = []
    session = type("Session", (_Session,), {"tools": load_mcp_tools(), "calls": calls, "replies": replies})
    with patch.object(mcp, "ClientSession", session), patch.object(mcp.client.stdio, "stdio_client", _no_process):
        yield calls


def _config(cwd: Path, timeout_s: float = 5) -> PlaywrightMcpConfig:
    server = McpServerConfig(
        server_id="playwright_official_stdio",
        server_name="playwright-official",
        server_path="stdio://playwright",
        client_type="stdio",
        params={
            "command": sys.executable,
            "args": ["-y", "@playwright/mcp@0.0.78", "--caps=vision,pdf"],
            "cwd": str(cwd),
            "timeout_s": timeout_s,
        },
    )
    return PlaywrightMcpConfig(server)


def _run(cwd: Path, replies: dict[str, Any], body: Callable[[PlaywrightMcpDriver], Any]) -> tuple[Any, list[Call]]:
    """Start a driver on the fake session, run ``body`` on it, stop it; returns its outcome and the tool calls sent."""

    async def scenario() -> Any:
        driver = PlaywrightMcpDriver(_config(cwd))
        await driver.start()
        try:
            return await body(driver)
        finally:
            await driver.stop()

    with _mcp(replies) as calls:
        outcome = asyncio.run(scenario())
    return outcome, [call for call in calls if call != "tools/list"]


def _execute(cwd: Path, replies: dict[str, Any], *actions: Any) -> tuple[list[Any], list[Call]]:
    """Outcomes of the actions in order: the ``ActionResult`` or the ``DriverError`` each raised."""

    async def body(driver: PlaywrightMcpDriver) -> list[Any]:
        outcomes: list[Any] = []
        for action in actions:
            try:
                outcomes.append(await driver.execute(action))
            except DriverError as exc:
                outcomes.append(exc)
        return outcomes

    return _run(cwd, replies, body)


EXPECTED_ACTIONS = {
    Click: {DriverRef, Locator, PointRef},
    TypeText: {DriverRef, Locator},
    PressKey: set(),
    Hover: {DriverRef, Locator, PointRef},
    Drag: {DriverRef, Locator, PointRef},
    Scroll: set(),
    SelectOption: {DriverRef, Locator},
    FillForm: {DriverRef, Locator},
    Drop: {DriverRef, Locator},
    UploadFile: set(),
    HandleDialog: set(),
    Wait: set(),
    RunScript: {DriverRef, Locator},
    Navigate: set(),
    Back: set(),
    OpenContext: set(),
    SwitchContext: set(),
    CloseContext: set(),
    GetPdf: set(),
    GetConsole: set(),
    GetNetwork: set(),
}


def test_info_actions_are_pinned() -> None:
    actions = PlaywrightMcpDriver().info.actions
    assert {command: set(refs) for command, refs in actions.items()} == EXPECTED_ACTIONS


def test_index_ref_is_never_a_model_facing_ref_kind() -> None:
    for refs in PlaywrightMcpDriver().info.actions.values():
        assert IndexRef not in refs


def test_info_describes_the_browser_surface_and_the_server_version(tmp_path: Path) -> None:
    info = PlaywrightMcpDriver(_config(tmp_path)).info
    assert info.name == "playwright_mcp"
    assert info.surfaces == frozenset({"browser"})
    assert info.observes == frozenset({"elements", "screenshot", "native_text", "surface"})
    assert dict(info.versions) == {"@playwright/mcp": "0.0.78"}
    assert dict(info.tool_hints) == {}


def test_info_without_a_config_has_no_version() -> None:
    assert dict(PlaywrightMcpDriver().info.versions) == {}


def test_tool_hints_are_the_server_descriptions_after_start(tmp_path: Path) -> None:
    described = {tool["name"]: tool["description"] for tool in load_mcp_tools()}

    hints, _calls = _run(tmp_path, {}, lambda driver: _value(dict(driver.info.tool_hints)))

    assert set(hints) == set(EXPECTED_ACTIONS)
    assert hints[Click] == described["browser_click"]
    assert hints[Scroll] == described["browser_mouse_wheel"]
    assert hints[OpenContext] == hints[CloseContext] == described["browser_tabs"]


async def _value(value: Any) -> Any:
    return value


# --- lifecycle -------------------------------------------------------------------------------------------------------


def test_start_connects_and_health_pings_the_session(tmp_path: Path) -> None:
    health, calls = _run(tmp_path, {}, lambda driver: driver.health())

    assert health == Health(ok=True)
    assert calls == []


def test_health_before_start_and_after_stop(tmp_path: Path) -> None:
    driver = PlaywrightMcpDriver(_config(tmp_path))
    assert asyncio.run(driver.health()) == Health(ok=False, detail="not started")

    async def body(started: PlaywrightMcpDriver) -> Health:
        await started.stop()
        return await started.health()

    health, _calls = _run(tmp_path, {}, body)
    assert health == Health(ok=False, detail="not started")


def test_health_reports_a_server_that_stopped_answering(tmp_path: Path) -> None:
    async def body(driver: PlaywrightMcpDriver) -> Health:
        _Session.broken = True
        try:
            return await driver.health()
        finally:
            _Session.broken = False

    health, _calls = _run(tmp_path, {}, body)
    assert health == Health(ok=False, detail="the server does not answer")


def test_start_without_a_config_raises_before_anything_runs() -> None:
    with pytest.raises(DriverError, match="needs a PlaywrightMcpConfig") as caught:
        asyncio.run(PlaywrightMcpDriver().start())
    assert caught.value.dispatched is False


def test_execute_before_start_is_connection_lost_before_dispatch(tmp_path: Path) -> None:
    with pytest.raises(ConnectionLost, match="not started") as caught:
        asyncio.run(PlaywrightMcpDriver(_config(tmp_path)).execute(PressKey("Enter")))
    assert caught.value.dispatched is False


def test_transport_failure_is_connection_lost_dispatched(tmp_path: Path) -> None:
    (outcome,), calls = _execute(tmp_path, {"browser_press_key": RuntimeError("pipe broke")}, PressKey("Enter"))

    assert isinstance(outcome, ConnectionLost)
    assert outcome.dispatched is True
    assert "pipe broke" in str(outcome)
    assert calls == [("browser_press_key", {"key": "Enter"})]


def test_a_call_that_times_out_is_sent_once_and_counts_as_dispatched(tmp_path: Path) -> None:
    async def scenario() -> Any:
        driver = PlaywrightMcpDriver(_config(tmp_path, timeout_s=0.2))
        await driver.start()
        try:
            return await driver.execute(PressKey("Enter"))
        except DriverError as exc:
            return exc
        finally:
            await driver.stop()

    with _mcp({"browser_press_key": [HANG, OK]}) as calls:
        outcome = asyncio.run(scenario())

    assert isinstance(outcome, ActionTimeout)
    assert outcome.dispatched is True
    assert [call for call in calls if call != "tools/list"] == [("browser_press_key", {"key": "Enter"})]


def test_a_closed_transport_is_not_resent(tmp_path: Path) -> None:
    replies = {"browser_press_key": [RuntimeError("ClosedResourceError: connection closed"), OK]}

    (outcome,), calls = _execute(tmp_path, replies, PressKey("Enter"))

    assert isinstance(outcome, ConnectionLost)
    assert outcome.dispatched is True
    assert calls == [("browser_press_key", {"key": "Enter"})]


def test_a_call_without_a_connection_is_connection_lost_before_dispatch(tmp_path: Path) -> None:
    with patch.object(BrowserMoveStdioClient, "connected", new_callable=PropertyMock, return_value=False):
        (outcome,), calls = _execute(tmp_path, {"browser_press_key": OK}, PressKey("Enter"))

    assert isinstance(outcome, ConnectionLost)
    assert outcome.dispatched is False
    assert calls == []


# --- observe ----------------------------------------------------------------------------------------------------------


def test_observe_reads_the_snapshot_header_tabs_elements_and_full_text(tmp_path: Path) -> None:
    raw = _raw("snapshot_two_tabs")

    observation, calls = _run(tmp_path, {"browser_snapshot": raw}, lambda driver: driver.observe(Observe()))

    assert isinstance(observation, Observation)
    assert observation.context == _tab(0, "Fixture popup page")
    assert observation.surface is not None
    assert observation.surface.tabs == (_tab(0, "Fixture popup page"), _tab(1, "Fixture second page"))
    assert observation.surface.url.endswith("/popup.html")
    assert observation.native_text == result_text(raw)
    assert observation.native_text.startswith("### Open tabs\n")
    assert [element.ref.ref for element in observation.elements] == ["f9e1", "f9e2", "f9e3", "f9e4", "f9e5", "f9e6"]
    assert observation.screenshot is None
    assert observation.generation == ""
    assert calls == [("browser_snapshot", {"boxes": True})]


def test_observe_gives_bounds_and_keeps_the_boxes_out_of_the_text(tmp_path: Path) -> None:
    raw = _raw("snapshot_with_boxes")

    observation, _calls = _run(tmp_path, {"browser_snapshot": raw}, lambda driver: driver.observe(Observe()))

    assert observation.native_text == without_boxes(result_text(raw))
    assert "[box=" not in observation.native_text
    assert {element.ref.ref: element.bounds for element in observation.elements}["f7e2"] == (8.0, 21.0, 1264.0, 37.0)


@pytest.mark.parametrize("full_page", [False, True])
def test_observe_with_a_screenshot_takes_it_at_css_scale(tmp_path: Path, full_page: bool) -> None:
    replies = {"browser_snapshot": _raw("snapshot_form_page"), "browser_take_screenshot": _raw("screenshot_element")}

    observation, calls = _run(
        tmp_path, replies, lambda driver: driver.observe(Observe(include_screenshot=True, full_page=full_page))
    )

    assert calls == [
        ("browser_snapshot", {"boxes": True}),
        ("browser_take_screenshot", {"type": "png", "fullPage": full_page}),
    ]
    assert observation.screenshot is not None
    assert (observation.screenshot.width, observation.screenshot.height, observation.screenshot.scale) == (
        1264,
        38,
        1.0,
    )


def test_observe_while_a_dialog_is_open_reads_the_tab_list_and_takes_no_screenshot(tmp_path: Path) -> None:
    refused = _raw("snapshot_during_dialog")
    replies = {"browser_snapshot": refused, "browser_tabs": _raw("tabs_list_during_dialog")}

    observation, calls = _run(tmp_path, replies, lambda driver: driver.observe(Observe(include_screenshot=True)))

    assert calls == [("browser_snapshot", {"boxes": True}), ("browser_tabs", {"action": "list"})]
    assert (observation.elements, observation.screenshot) == ((), None)
    assert observation.context == _tab(0, "")
    assert observation.native_text == result_text(refused)
    assert observation.surface is not None
    assert observation.surface.url.endswith("/index.html")
    assert observation.surface.dialog == "confirm: Delete order?"


# --- handlers ---------------------------------------------------------------------------------------------------------

SENT = [
    (Click(E12), "browser_click", {"element": "click e12", "target": "e12"}),
    (
        Click(CSS, button="right", modifiers=("Shift",), count=2),
        "browser_click",
        {"element": "click #save", "target": "#save", "doubleClick": True, "button": "right", "modifiers": ["Shift"]},
    ),
    (Click(POINT), "browser_mouse_click_xy", {"x": 10, "y": 20}),
    (
        Click(POINT, button="middle", count=3),
        "browser_mouse_click_xy",
        {"x": 10, "y": 20, "button": "middle", "clickCount": 3},
    ),
    (TypeText(E12, "hello"), "browser_type", {"element": "type into e12", "target": "e12", "text": "hello"}),
    (
        TypeText(E12, "hello", submit=True, clear=False, slowly=True),
        "browser_type",
        {"element": "type into e12", "target": "e12", "text": "hello", "submit": True, "slowly": True},
    ),
    (PressKey("Enter"), "browser_press_key", {"key": "Enter"}),
    (Hover(E12), "browser_hover", {"element": "hover e12", "target": "e12"}),
    (Hover(POINT), "browser_mouse_move_xy", {"x": 10, "y": 20}),
    (
        Drag(E12, E5),
        "browser_drag",
        {"startElement": "drag e12", "startTarget": "e12", "endElement": "drop on e5", "endTarget": "e5"},
    ),
    (Drag(POINT, POINT_2), "browser_mouse_drag_xy", {"startX": 10, "startY": 20, "endX": 30, "endY": 40}),
    (Scroll(dx=5, dy=-300), "browser_mouse_wheel", {"deltaX": 5, "deltaY": -300}),
    (
        SelectOption(E12, ("Large", "Medium")),
        "browser_select_option",
        {"element": "select in e12", "target": "e12", "values": ["Large", "Medium"]},
    ),
    (
        FillForm((FormField(E12, FieldKind.TEXTBOX, "Ada"), FormField(CSS, FieldKind.CHECKBOX, "true"))),
        "browser_fill_form",
        {
            "fields": [
                {"name": "textbox e12", "type": "textbox", "target": "e12", "value": "Ada"},
                {"name": "checkbox #save", "type": "checkbox", "target": "#save", "value": "true"},
            ]
        },
    ),
    (
        Drop(E12, paths=("C:/a.csv",), data=(("text/plain", "x"),)),
        "browser_drop",
        {"element": "drop on e12", "target": "e12", "paths": ["C:/a.csv"], "data": {"text/plain": "x"}},
    ),
    (UploadFile(None, ("C:/a.csv",)), "browser_file_upload", {"paths": ["C:/a.csv"]}),
    (UploadFile(None), "browser_file_upload", {}),
    (HandleDialog(accept=True), "browser_handle_dialog", {"accept": True}),
    (HandleDialog(accept=False, prompt_text="x"), "browser_handle_dialog", {"accept": False, "promptText": "x"}),
    (Wait(seconds=1.5), "browser_wait_for", {"time": 1.5}),
    (Wait(until_text="Done", seconds=3), "browser_wait_for", {"text": "Done"}),
    (RunScript("() => 1"), "browser_evaluate", {"function": "() => 1"}),
    (
        RunScript("(e) => e.id", target=E12),
        "browser_evaluate",
        {"element": "evaluate on e12", "target": "e12", "function": "(e) => e.id"},
    ),
    (RunScript("(n) => n + 1", args=41), "browser_evaluate", {"function": "() => ((n) => n + 1)(41)"}),
    (
        RunScript("(el, n) => n", args={"a": 1}, target=CSS),
        "browser_evaluate",
        {
            "element": "evaluate on #save",
            "target": "#save",
            "function": '(element) => ((el, n) => n)(element, {"a": 1})',
        },
    ),
    (Navigate("http://x/"), "browser_navigate", {"url": "http://x/"}),
    (Back(), "browser_navigate_back", {}),
    (OpenContext(), "browser_tabs", {"action": "new"}),
    (OpenContext("http://x/"), "browser_tabs", {"action": "new", "url": "http://x/"}),
    (SwitchContext(TAB_1), "browser_tabs", {"action": "select", "index": 1}),
    (CloseContext(TAB_1), "browser_tabs", {"action": "close", "index": 1}),
    (GetPdf(), "browser_pdf_save", {}),
    (GetPdf("out.pdf"), "browser_pdf_save", {"filename": "out.pdf"}),
    (GetConsole(), "browser_console_messages", {"level": "info"}),
    (GetConsole(level="error", all=True), "browser_console_messages", {"level": "error", "all": True}),
    (GetNetwork(), "browser_network_requests", {"static": False}),
    (GetNetwork(static=True, filter="/api/"), "browser_network_requests", {"static": True, "filter": "/api/"}),
]


@pytest.mark.parametrize(("action", "tool", "arguments"), SENT, ids=[f"{t}:{i}" for i, (_a, t, _x) in enumerate(SENT)])
def test_handler_sends_one_tool_call(tmp_path: Path, action: Any, tool: str, arguments: dict[str, Any]) -> None:
    reply = _raw("tabs_new") if tool == "browser_tabs" else OK

    (result,), calls = _execute(tmp_path, {tool: reply}, action)

    assert isinstance(result, ActionResult)
    assert result.ok is True
    assert calls == [(tool, arguments)]


UNSUPPORTED = [
    Click(E12, count=3),
    Click(POINT, modifiers=("Shift",)),
    Click(Locator(None, "Save", "g3")),
    Click(Locator("#save", "Save", "g3")),
    Click(IndexRef(1, "g3")),
    TypeText(None, "x"),
    TypeText(E12, "x", clear=False),
    Drag(E12, POINT),
    Drag(POINT, CSS),
    PressKey("a", target=E12),
    Scroll(E12, dy=10),
    UploadFile(E12, ("C:/a.csv",)),
    RunScript("() => 1", target=POINT),
    FillForm((FormField(POINT, FieldKind.TEXTBOX, "x"),)),
]


@pytest.mark.parametrize("action", UNSUPPORTED, ids=[f"{type(a).__name__}:{i}" for i, a in enumerate(UNSUPPORTED)])
def test_unsupported_commands_send_nothing(tmp_path: Path, action: Any) -> None:
    (outcome,), calls = _execute(tmp_path, {}, action)

    assert isinstance(outcome, Unsupported)
    assert outcome.dispatched is False
    assert calls == []


# --- results and context tracking -------------------------------------------------------------------------------------


SECRET = "s3cret-value"
ECHOED = {
    "content": [
        {
            "type": "text",
            "text": f"### Ran Playwright code\n```js\nawait page.x('{SECRET}');\n```\n### Page\n- Page URL: http://x/",
        }
    ],
    "isError": False,
}


@pytest.mark.parametrize(
    ("action", "tool"),
    [
        (TypeText(E12, SECRET, sensitive=True), "browser_type"),
        (FillForm((FormField(E12, FieldKind.TEXTBOX, SECRET, sensitive=True),)), "browser_fill_form"),
        (RunScript(f"() => '{SECRET}'", sensitive=True), "browser_evaluate"),
    ],
)
def test_a_sensitive_command_keeps_the_echoed_code_out_of_the_detail(tmp_path: Path, action: Any, tool: str) -> None:
    (result,), _calls = _execute(tmp_path, {tool: ECHOED}, action)

    assert SECRET not in (result.detail or "")


def test_a_plain_command_keeps_the_echoed_code(tmp_path: Path) -> None:
    (result,), _calls = _execute(tmp_path, {"browser_type": ECHOED}, TypeText(E12, SECRET))

    assert SECRET in result.detail


def test_open_context_value_is_the_new_tab(tmp_path: Path) -> None:
    replies = {"browser_navigate": _raw("navigate_second_page"), "browser_tabs": _raw("tabs_new")}

    (_navigated, opened), _calls = _execute(tmp_path, replies, Navigate("http://x/second.html"), OpenContext())

    assert opened.value == _tab(1, "")
    assert opened.opened_contexts == (_tab(1, ""),)
    assert opened.context_changed is True


def test_close_context_names_the_closed_tab(tmp_path: Path) -> None:
    (closed,), _calls = _execute(tmp_path, {"browser_tabs": _raw("tabs_close")}, CloseContext(TAB_1))

    assert closed.source_context == TAB_1
    assert closed.context_changed is True


def test_a_click_without_an_earlier_context_reports_no_change(tmp_path: Path) -> None:
    (clicked,), _calls = _execute(tmp_path, {"browser_click": _raw("click_success")}, Click(E12))

    assert clicked.context_changed is False


def test_the_context_of_every_result_is_carried_to_the_next_decode(tmp_path: Path) -> None:
    replies = {"browser_navigate": _raw("navigate_second_page"), "browser_click": _raw("click_success")}

    (_navigated, clicked), _calls = _execute(tmp_path, replies, Navigate("http://x/second.html"), Click(E12))

    assert clicked.context_changed is True  # second.html -> index.html


def test_the_context_of_an_observation_is_carried_to_the_next_decode(tmp_path: Path) -> None:
    replies = {"browser_snapshot": _raw("snapshot_two_tabs"), "browser_click": _raw("click_navigates")}

    async def body(driver: PlaywrightMcpDriver) -> ActionResult:
        await driver.observe(Observe())
        return await driver.execute(Click(E12))

    clicked, _calls = _run(tmp_path, replies, body)

    assert clicked.context_changed is True  # popup.html -> second.html
    assert clicked.opened_contexts == ()


def test_a_linked_snapshot_is_a_free_observation(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_FILE).parent.mkdir()
    (tmp_path / SNAPSHOT_FILE).write_text(_inline_yaml("snapshot_form_page"), encoding="utf-8")

    (clicked,), calls = _execute(tmp_path, {"browser_click": _raw("click_success")}, Click(E12))

    assert clicked.observation is not None
    assert [element.ref.ref for element in clicked.observation.elements][-3:] == ["f1e1", "f1e2", "f1e3"]
    assert [tool for tool, _args in calls] == ["browser_click"]


def test_decode_runs_off_the_event_loop(tmp_path: Path) -> None:
    threads: list[threading.Thread] = []
    real_decode = driver_module.decode

    def recording_decode(*args: Any, **kwargs: Any) -> ActionResult:
        threads.append(threading.current_thread())
        return real_decode(*args, **kwargs)

    with patch.object(driver_module, "decode", recording_decode):
        _execute(tmp_path, {"browser_click": _raw("click_success")}, Click(E12))

    assert threads and all(thread is not threading.main_thread() for thread in threads)


def test_a_stale_ref_is_raised_before_dispatch(tmp_path: Path) -> None:
    (outcome,), _calls = _execute(tmp_path, {"browser_click": _raw("click_stale_ref")}, Click(E12))

    assert isinstance(outcome, StaleRef)
    assert outcome.dispatched is False


def test_an_action_refused_by_a_dialog_is_not_ok(tmp_path: Path) -> None:
    (outcome,), _calls = _execute(tmp_path, {"browser_click": _raw("click_blocked_by_dialog")}, Click(E12))

    assert isinstance(outcome, ActionResult)
    assert outcome.ok is False


def test_evaluate_returns_the_value(tmp_path: Path) -> None:
    (outcome,), _calls = _execute(tmp_path, {"browser_evaluate": _raw("evaluate_result")}, RunScript("() => 1"))

    assert outcome.value == {"name": "Ada", "size": "l"}


# --- factory ----------------------------------------------------------------------------------------------------------

REMOTE = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9222", extras={})
MANAGED = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9333", extras={"port": 9333})
EXTENSION = ProvisionedDevice(kind="chrome", endpoint="", extras={"extension": True, "extension_token": "tok"})
DEVICES = {
    "remote": ({"BROWSER_PROVISIONING": "remote", "PLAYWRIGHT_MCP_CDP_ENDPOINT": REMOTE.endpoint}, REMOTE),
    "managed": ({"BROWSER_PROVISIONING": "managed"}, MANAGED),
    "extension": ({"BROWSER_PROVISIONING": "extension", "PLAYWRIGHT_MCP_EXTENSION_TOKEN": "tok"}, EXTENSION),
}


@pytest.fixture
def environ(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[dict[str, str]], None]:
    for key in list(os.environ):
        if key.startswith(("BROWSER_", "PLAYWRIGHT_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PLAYWRIGHT_MCP_COMMAND", "npx")
    monkeypatch.setenv("PLAYWRIGHT_RUNTIME_WORKDIR", str(tmp_path))

    def apply(values: dict[str, str]) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    return apply


@pytest.mark.parametrize("provisioning", sorted(DEVICES))
def test_factory_builds_the_server_config_the_runtime_builds_today(environ, provisioning: str) -> None:
    values, device = DEVICES[provisioning]
    environ(values)
    expected = build_playwright_mcp_config(BrowserInstanceConfig(cdp_url=device.endpoint))

    driver = create_playwright_mcp_driver(device, BrowserSettings.from_env(os.environ))

    assert isinstance(driver, PlaywrightMcpDriver)
    assert driver.config is not None
    assert driver.config.server == expected
    assert driver.config.output_dir == Path(expected.params["cwd"])
    caps = [arg for arg in driver.config.server.params["args"] if arg.startswith("--caps=")]
    assert len(caps) == 1 and "vision" in caps[0]


@pytest.mark.parametrize("provisioning", ["remote", "managed"])
def test_factory_points_the_server_at_the_device_endpoint(environ, provisioning: str) -> None:
    values, device = DEVICES[provisioning]
    environ(values)

    server = create_playwright_mcp_driver(device, BrowserSettings.from_env(os.environ)).config.server

    assert server.params["env"]["PLAYWRIGHT_MCP_CDP_ENDPOINT"] == device.endpoint
    assert server.params["env"]["PLAYWRIGHT_MCP_BROWSER"] == "chrome"
    assert "--extension" not in server.params["args"]


def test_factory_builds_the_extension_command_line(environ) -> None:
    values, device = DEVICES["extension"]
    environ(values)

    server = create_playwright_mcp_driver(device, BrowserSettings.from_env(os.environ)).config.server

    assert "--extension" in server.params["args"]
    assert server.params["env"]["PLAYWRIGHT_MCP_EXTENSION"] == "true"
    assert server.params["env"]["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] == "tok"
    assert "PLAYWRIGHT_MCP_CDP_ENDPOINT" not in server.params["env"]
