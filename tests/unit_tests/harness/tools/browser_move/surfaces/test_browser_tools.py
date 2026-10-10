# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser-dialect tool specs against the browser-use golden, and their argument decoders."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverRef,
    HandleDialog,
    Hover,
    Locator,
    Observe,
    PressKey,
    Ref,
    RunScript,
    SwitchContext,
    TypeText,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FieldKind,
    FillForm,
    FormField,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import BROWSER_TOOL_SPECS

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "snapshots" / "golden" / "browser_use"
SPECS = {spec.name: spec for spec in BROWSER_TOOL_SPECS}
DIALECT = {name: spec for name, spec in SPECS.items() if spec.schema is not None}
TAB_0 = ContextRef("A", "tab", "First")
TAB_1 = ContextRef("B", "tab", "Second")


def _golden_tools() -> list[tuple[str, dict[str, Any]]]:
    tools = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        for agent in json.loads(path.read_text(encoding="utf-8"))["agents"]:
            tools += [(f"{path.stem}:{tool['name']}", tool) for tool in agent["tools"]]
    return tools


def _expected(tool: dict[str, Any]) -> dict[str, Any]:
    expected = copy.deepcopy(tool)
    if tool["name"] == "browser_tabs":
        # Approved difference: "new" opens a tab (OpenContext) on every driver.
        expected["parameters"]["properties"]["action"]["enum"].append("new")
    return expected


class _Resolver:
    def __init__(self, *, contexts: tuple[ContextRef, ...] = (TAB_0, TAB_1), current: ContextRef | None = TAB_1):
        self.contexts = contexts
        self.current_context = current

    def resolve(self, model_id: str, generation: str) -> Ref:
        if model_id == "t_g3_7":
            return DriverRef("e57", generation)
        if model_id == "t_g3_8":
            return Locator(css="#save", text=None, generation=generation)
        raise ValueError(f"unknown target_id {model_id}")

    def generation_of(self, ref: str) -> str:
        return "g3"


def _build(name: str, args: dict[str, Any], resolver: _Resolver | None = None):
    return SPECS[name].build(args, resolver or _Resolver())


E57 = DriverRef("e57", "g3")
SAVE = Locator(css="#save", text=None, generation="g3")


DIALECT_GOLDEN = [(key, tool) for key, tool in _golden_tools() if tool["name"] in DIALECT]


@pytest.mark.parametrize("golden", [tool for _key, tool in DIALECT_GOLDEN], ids=[key for key, _tool in DIALECT_GOLDEN])
def test_dialect_spec_equals_its_golden_entry(golden: dict[str, Any]) -> None:
    spec = DIALECT[golden["name"]]
    expected = _expected(golden)
    assert spec.description == expected["description"]
    assert json.loads(json.dumps(spec.schema)) == expected["parameters"]


def test_golden_tools_without_a_dialect_spec_are_the_approved_ones() -> None:
    golden_names = {tool["name"] for _key, tool in _golden_tools()}
    # cancel/clear-cancel are not carried over; find and recall become extra tools.
    assert golden_names - set(DIALECT) == {
        "browser_cancel_run",
        "browser_clear_cancel",
        "browser_find",
        "browser_recall_offload",
    }
    assert set(DIALECT) <= golden_names


def test_every_dialect_spec_has_commands_and_a_decoder() -> None:
    for spec in DIALECT.values():
        assert spec.commands and spec.build is not None, spec.name


def test_specs_without_a_dialect_rely_on_native_tools() -> None:
    bare = {name for name, spec in SPECS.items() if spec.schema is None}
    assert bare == {
        "browser_mouse_click_xy",
        "browser_mouse_move_xy",
        "browser_mouse_drag_xy",
        "browser_mouse_wheel",
        "browser_pdf_save",
        "browser_console_messages",
        "browser_network_requests",
    }
    assert all(SPECS[name].build is None and not SPECS[name].description for name in bare)


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("browser_navigate", {"url": " https://example.test/ ", "wait_until": "x", "timeout_ms": "y"},
         Navigate("https://example.test/")),
        ("browser_navigate_back", {}, Back()),
        ("browser_click", {"generation_id": "g3", "target_id": "t_g3_7"}, Click(E57)),
        ("browser_click", {"generation_id": "g3", "ref": "e57", "button": "right", "click_count": "2"},
         Click(E57, button="right", count=2)),
        ("browser_click", {"generation_id": "g3", "selector": "#save", "click_count": 0}, Click(SAVE)),
        ("browser_click", {"generation_id": "g3", "target_id": "t_g3_7", "ref": "e1", "selector": "#x"}, Click(E57)),
        ("browser_type", {"generation_id": "g3", "ref": "e57", "text": "Ada"}, TypeText(E57, "Ada")),
        ("browser_type",
         {"generation_id": "g3", "ref": "e57", "text": "pw", "clear": "false", "press_enter": "yes", "sensitive": True},
         TypeText(E57, "pw", submit=True, sensitive=True, clear=False)),
        ("browser_press_key", {"key": "Control+a"}, PressKey("Control+a")),
        ("browser_take_screenshot", {}, Observe(include_screenshot=True)),
        ("browser_take_screenshot", {"full_page": "true"}, Observe(include_screenshot=True, full_page=True)),
        ("browser_tabs", {}, Observe()),
        ("browser_tabs", {"action": "LIST"}, Observe()),
        ("browser_tabs", {"action": "new"}, OpenContext()),
        ("browser_tabs", {"action": "select", "index": 0}, SwitchContext(TAB_0)),
        ("browser_tabs", {"action": "close", "index": "0"}, CloseContext(TAB_0)),
        ("browser_tabs", {"action": "close"}, CloseContext(TAB_1)),
        ("browser_close", {}, CloseContext(TAB_1)),
        ("browser_select_option", {"generation_id": "g3", "ref": "e57", "value": "l"}, SelectOption(E57, ("l",))),
        ("browser_select_option", {"generation_id": "g3", "ref": "e57", "label": "Large"},
         SelectOption(E57, ("Large",))),
        ("browser_evaluate", {"function": "() => 1", "args": {"a": 1}}, RunScript("() => 1", {"a": 1})),
        ("browser_evaluate", {"source": "document.title"}, RunScript("document.title")),
        ("browser_drag",
         {"generation_id": "g3", "source_ref": "e57", "target_selector": "#save", "steps": 4},
         Drag(E57, SAVE)),
        ("browser_drag", {"generation_id": "g3", "source_target_id": "t_g3_8", "target_target_id": "t_g3_7"},
         Drag(SAVE, E57)),
        ("browser_file_upload", {"generation_id": "g3", "ref": "e57", "paths": ["a.txt", 3]},
         UploadFile(E57, ("a.txt", "3"))),
        ("browser_hover", {"generation_id": "g3", "selector": "#save"}, Hover(SAVE)),
        ("browser_handle_dialog", {"accept": "yes", "promptText": "Ada"}, HandleDialog(True, "Ada")),
        ("browser_handle_dialog", {"accept": False, "prompt_text": "x"}, HandleDialog(False, "x")),
        ("browser_handle_dialog", {"accept": True}, HandleDialog(True)),
        ("browser_drop", {"generation_id": "g3", "ref": "e57", "paths": ["a.csv"]}, Drop(E57, paths=("a.csv",))),
        ("browser_drop",
         {"generation_id": "g3", "ref": "e57",
          "data": [{"mimeType": "text/plain", "data": "hi"}, {"mime_type": "text/html", "data": "<b>"}]},
         Drop(E57, data=(("text/plain", "hi"), ("text/html", "<b>")))),
        ("browser_snapshot", {}, Observe()),
        ("browser_snapshot", {"include_screenshot": "1"}, Observe(include_screenshot=True)),
    ],
)  # fmt: skip
def test_decoder_builds_the_exact_command(name: str, args: dict[str, Any], expected) -> None:
    assert _build(name, args) == expected


def test_fill_form_decodes_each_field_kind() -> None:
    fields = [
        {"type": "textbox", "ref": "e57", "value": "Ada", "sensitive": True},
        {"ref": "e57", "value": 5},
        {"type": "slider", "selector": "#save", "value": "7"},
        {"type": "Select", "target_id": "t_g3_7", "value": "", "label": "Medium"},
        {"type": "checkbox", "ref": "e57", "value": "on"},
        {"type": "switch", "ref": "e57", "value": False},
        {"type": "radio", "ref": "e57", "value": "checked"},
    ]
    assert _build("browser_fill_form", {"generation_id": "g3", "fields": fields}) == FillForm(
        (
            FormField(E57, FieldKind.TEXTBOX, "Ada", sensitive=True),
            FormField(E57, FieldKind.TEXTBOX, "5"),
            FormField(SAVE, FieldKind.SLIDER, "7"),
            FormField(E57, FieldKind.COMBOBOX, "Medium"),
            FormField(E57, FieldKind.CHECKBOX, "true"),
            FormField(E57, FieldKind.CHECKBOX, "false"),
            FormField(E57, FieldKind.RADIO, "true"),
        )
    )


def test_element_refs_carry_the_generation_the_model_sent() -> None:
    assert _build("browser_hover", {"generation_id": "g1", "ref": "e57"}) == Hover(DriverRef("e57", "g1"))


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("browser_navigate", {}, "'url' is required"),
        ("browser_click", {"ref": "e57"}, "'generation_id' is required"),
        ("browser_click", {"generation_id": "g3"}, "exactly one of target_id, ref, or selector is required"),
        ("browser_click", {"generation_id": "g3", "ref": "e57", "click_count": "two"},
         "'click_count' must be an integer"),
        ("browser_click", {"generation_id": "g3", "target_id": "t_unknown"}, "unknown target_id t_unknown"),
        ("browser_press_key", {}, "'key' is required"),
        ("browser_tabs", {"action": "open"}, "action must be one of: list, new, select, close"),
        ("browser_tabs", {"action": "select"}, "action=select requires integer index"),
        ("browser_tabs", {"action": "select", "index": "x"}, "'index' must be an integer"),
        ("browser_tabs", {"action": "select", "index": 2}, r"tab index 2 is out of range: 2 tab\(s\) open"),
        ("browser_tabs", {"action": "close", "index": -1}, "tab index -1 is out of range"),
        ("browser_select_option", {"generation_id": "g3", "ref": "e57"}, "exactly one of value or label is required"),
        ("browser_evaluate", {"args": 1}, "'function' is required"),
        ("browser_drag", {"generation_id": "g3", "source_ref": "e57"},
         "exactly one of target_target_id, target_ref, or target_selector is required"),
        ("browser_drag", {"generation_id": "g3", "source_ref": "e57", "target_ref": "e1", "steps": "x"},
         "'steps' must be an integer"),
        ("browser_file_upload", {"generation_id": "g3", "ref": "e57"}, "'paths' must be a list of strings"),
        ("browser_file_upload", {"generation_id": "g3", "ref": "e57", "paths": "a.txt"},
         "'paths' must be a list of strings"),
        ("browser_handle_dialog", {"promptText": "x"}, "'accept' is required"),
        ("browser_drop", {"generation_id": "g3", "ref": "e57"}, "Drop needs paths or data"),
        ("browser_drop", {"generation_id": "g3", "ref": "e57", "data": {"a": 1}}, "'data' must be a list of objects"),
        ("browser_drop", {"generation_id": "g3", "ref": "e57", "data": ["x"]}, "'data' items must be objects"),
        ("browser_fill_form", {"generation_id": "g3", "fields": []}, "'fields' must be a non-empty list"),
        ("browser_fill_form", {"generation_id": "g3", "fields": ["x"]}, r"fields\[0\] must be an object"),
        ("browser_fill_form", {"generation_id": "g3", "fields": [{"type": "date", "ref": "e1"}]},
         r"fields\[0\] unsupported type='date'"),
        ("browser_fill_form", {"fields": [{"ref": "e1"}]}, "'generation_id' is required"),
    ],
)  # fmt: skip
def test_invalid_arguments_raise_value_error(name: str, args: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _build(name, args)


@pytest.mark.parametrize("name", ["browser_close", "browser_tabs"])
def test_closing_without_an_open_tab_is_a_value_error(name: str) -> None:
    args = {"action": "close"} if name == "browser_tabs" else {}
    with pytest.raises(ValueError, match="no open tab"):
        _build(name, args, _Resolver(contexts=(), current=None))
