# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Decoders of the @playwright/mcp tool arguments: the inverse of the driver's handlers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverRef,
    Hover,
    Locator,
    Observe,
    PointRef,
    Ref,
    RunScript,
    Scroll,
    SwitchContext,
    TypeText,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Drop,
    FieldKind,
    FillForm,
    FormField,
    GetConsole,
    GetNetwork,
    OpenContext,
    UploadFile,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import BROWSER_TOOL_SPECS
from tests.unit_tests.harness.tools.browser_move.drivers.playwright_mcp.test_driver import OK, SENT, _execute, _raw
from tests.unit_tests.harness.tools.browser_move.snapshots.builder import load_mcp_tools

TAB_0 = ContextRef("0", "tab", "First")
TAB_1 = ContextRef("1", "tab", "Second")


@dataclass
class _Resolver:
    contexts: tuple[ContextRef, ...] = (TAB_0, TAB_1)
    current_context: ContextRef | None = TAB_0
    generation: str = "g3"
    screenshot_generation: str = ""

    def resolve(self, model_id: str, generation: str) -> Ref:
        raise ValueError(f"unknown target_id {model_id}")

    def generation_of(self, ref: str) -> str:
        return self.generation


RESOLVER = _Resolver()
ROUND_TRIPS = [(tool, arguments) for _action, tool, arguments in SENT if tool in DECODERS]


def _decode(tool: str, arguments: dict[str, Any], resolver: _Resolver = RESOLVER) -> Any:
    return DECODERS[tool](arguments, resolver)


@pytest.mark.parametrize(("tool", "arguments"), ROUND_TRIPS, ids=[f"{t}:{i}" for i, (t, _a) in enumerate(ROUND_TRIPS)])
def test_decoding_then_sending_gives_back_the_arguments(tmp_path: Path, tool: str, arguments: dict[str, Any]) -> None:
    reply = _raw("tabs_new") if tool == "browser_tabs" else OK

    _outcomes, calls = _execute(tmp_path, {tool: reply}, _decode(tool, arguments))

    assert calls == [(tool, arguments)]


def test_every_tool_spec_the_server_offers_has_a_decoder_and_nothing_else_does() -> None:
    offered = {tool["name"] for tool in load_mcp_tools()}
    specs = {spec.name for spec in BROWSER_TOOL_SPECS}
    assert set(DECODERS) == specs & offered


@pytest.mark.parametrize(
    ("target", "ref"),
    [
        ("e12", DriverRef("e12", "g3")),
        ("f1e3", DriverRef("f1e3", "g3")),
        ("#save", Locator("#save", None, "g3")),
        ("e12 > span", Locator("e12 > span", None, "g3")),
    ],
)
def test_target_is_a_driver_ref_for_an_aria_ref_and_a_css_locator_otherwise(target: str, ref: Ref) -> None:
    assert _decode("browser_hover", {"target": target}) == Hover(ref)


def test_a_driver_ref_carries_the_generation_it_was_last_seen_in() -> None:
    assert _decode("browser_hover", {"target": "e1"}, _Resolver(generation="g1")) == Hover(DriverRef("e1", "g1"))


def test_double_click_is_a_click_counted_twice() -> None:
    assert _decode("browser_click", {"target": "e1", "doubleClick": True}).count == 2
    assert _decode("browser_click", {"target": "e1", "doubleClick": False}).count == 1


def test_typing_slowly_keeps_the_content() -> None:
    assert _decode("browser_type", {"target": "e1", "text": "a"}) == TypeText(DriverRef("e1", "g3"), "a")
    slow = _decode("browser_type", {"target": "e1", "text": "a", "slowly": True})
    assert (slow.slowly, slow.clear) == (True, False)


@pytest.mark.parametrize(
    ("tool", "arguments", "action"),
    [
        ("browser_click", {"target": "e1", "element": "Save button"}, Click(DriverRef("e1", "g3"))),
        ("browser_snapshot", {"target": "e1", "depth": 2, "boxes": True, "filename": "a.md"}, Observe()),
        (
            "browser_take_screenshot",
            {"target": "e1", "element": "logo", "type": "jpeg", "scale": "device", "filename": "a.png"},
            Observe(include_screenshot=True),
        ),
        ("browser_evaluate", {"function": "() => 1", "filename": "out.json"}, RunScript("() => 1")),
        ("browser_mouse_click_xy", {"x": 1, "y": 2, "delay": 50}, Click(PointRef(1, 2, ""))),
        ("browser_console_messages", {"level": "error", "filename": "c.txt"}, GetConsole(level="error")),
        ("browser_network_requests", {"static": False, "filename": "n.txt"}, GetNetwork()),
    ],
)
def test_cosmetic_and_dropped_arguments_do_not_change_the_command(tool: str, arguments: dict, action: Any) -> None:
    assert _decode(tool, arguments) == action


def test_points_take_rounded_screenshot_pixels_and_no_generation_before_a_screenshot() -> None:
    assert _decode("browser_mouse_move_xy", {"x": 10.6, "y": 20.2}) == Hover(PointRef(11, 20, ""))
    dragged = _decode("browser_mouse_drag_xy", {"startX": 1, "startY": 2, "endX": 3, "endY": 4})
    assert dragged == Drag(PointRef(1, 2, ""), PointRef(3, 4, ""))


def test_points_take_the_generation_of_the_last_screenshot() -> None:
    shot = _Resolver(screenshot_generation="g2")
    assert _decode("browser_mouse_click_xy", {"x": 1, "y": 2}, shot) == Click(PointRef(1, 2, "g2"))
    dragged = _decode("browser_mouse_drag_xy", {"startX": 1, "startY": 2, "endX": 3, "endY": 4}, shot)
    assert dragged == Drag(PointRef(1, 2, "g2"), PointRef(3, 4, "g2"))


def test_wheel_scrolls_the_whole_view() -> None:
    assert _decode("browser_mouse_wheel", {"deltaX": 0, "deltaY": 300}) == Scroll(None, dx=0, dy=300)


def test_fill_form_and_drop_take_the_server_shapes() -> None:
    form = _decode("browser_fill_form", {"fields": [{"target": "#a", "name": "A", "type": "radio", "value": "true"}]})
    assert form == FillForm((FormField(Locator("#a", None, "g3"), FieldKind.RADIO, "true"),))
    drop = _decode("browser_drop", {"target": "e1", "data": {"text/plain": "x", "text/uri-list": "https://a.test"}})
    assert drop == Drop(DriverRef("e1", "g3"), data=(("text/plain", "x"), ("text/uri-list", "https://a.test")))


def test_file_upload_answers_the_open_chooser() -> None:
    assert _decode("browser_file_upload", {"paths": ["C:/a.csv"]}) == UploadFile(None, ("C:/a.csv",))
    assert _decode("browser_file_upload", {}) == UploadFile(None)


@pytest.mark.parametrize(
    ("arguments", "action"),
    [
        ({"action": "list"}, Observe()),
        ({"action": "new"}, OpenContext()),
        ({"action": "new", "url": "https://a.test/"}, OpenContext("https://a.test/")),
        ({"action": "select", "index": 1}, SwitchContext(TAB_1)),
        ({"action": "close", "index": 1}, CloseContext(TAB_1)),
        ({"action": "close"}, CloseContext(TAB_0)),
    ],
)
def test_tabs_action_picks_the_command(arguments: dict, action: Any) -> None:
    assert _decode("browser_tabs", arguments) == action


def test_close_takes_the_current_tab() -> None:
    assert _decode("browser_close", {}) == CloseContext(TAB_0)


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("browser_click", {}, "'target' is required"),
        ("browser_type", {"target": "e1"}, "'text' is required"),
        ("browser_navigate", {}, "'url' is required"),
        ("browser_select_option", {"target": "e1", "values": []}, "at least one option"),
        ("browser_select_option", {"target": "e1", "values": "Large"}, "must be a list"),
        ("browser_fill_form", {"fields": []}, "non-empty list"),
        ("browser_fill_form", {"fields": [{"target": "e1", "type": "date", "value": "x"}]}, "unsupported type"),
        ("browser_drop", {"target": "e1"}, "Drop needs paths or data"),
        ("browser_drop", {"target": "e1", "data": ["x"]}, "MIME types"),
        ("browser_mouse_click_xy", {"y": 1}, "'x' is required"),
        ("browser_mouse_click_xy", {"x": "left", "y": 1}, "'x' must be a number"),
        ("browser_handle_dialog", {}, "'accept' is required"),
        ("browser_tabs", {}, "'action' is required"),
        ("browser_tabs", {"action": "move"}, "action must be one of"),
        ("browser_tabs", {"action": "select"}, "requires index"),
        ("browser_tabs", {"action": "select", "index": 2}, "out of range: 2 tab(s) open"),
    ],
)
def test_invalid_arguments_are_a_value_error(tool: str, arguments: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        _decode(tool, arguments)


def test_closing_with_no_open_tab_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="no open tab"):
        _decode("browser_tabs", {"action": "close"}, _Resolver(contexts=(), current_context=None))
