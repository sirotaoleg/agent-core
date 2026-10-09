# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``BrowserUseDriver`` as a ``HandlerDriver``: wire requests, observation mapping, error mapping, factory.

The sidecar transport is mocked; no Chrome and no sidecar process are started.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    AmbiguousElement,
    Click,
    CloseContext,
    ConnectionLost,
    ContextRef,
    Drag,
    DriverError,
    DriverRef,
    ElementNotFound,
    HandleDialog,
    Hover,
    IndexRef,
    Locator,
    NavigationError,
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
from openjiuwen.harness.tools.browser_move.drivers.browser_use import driver as driver_module
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import (
    BrowserUseConfig,
    BrowserUseDriver,
    create_browser_use_driver,
)
from openjiuwen.harness.tools.browser_move.drivers.browser_use.sidecar import wire
from openjiuwen.harness.tools.browser_move.drivers.browser_use.transport import SidecarTransport
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
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
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

E1 = DriverRef("bnid:42", "g3")
E2 = DriverRef("bnid:43", "g3")
WIRE_E1 = {"kind": "driver", "handle": "bnid:42", "driver_generation": 0}
WIRE_E2 = {"kind": "driver", "handle": "bnid:43", "driver_generation": 0}
TAB_2 = ContextRef(id="T2", kind="tab", title="Second")
WIRE_TAB_2 = {"target_id": "T2", "url": "", "title": "Second", "active": False}
ACT_OK = {"ok": True, "detail": "done", "document_changed": False, "driver_generation": 0}
NAV_SAME_URL = {"url": "http://x/", "title": "X", "changed_document": False, "driver_generation": 0}


def _driver(*replies: Any) -> tuple[BrowserUseDriver, AsyncMock]:
    """A driver wired to a mocked transport that answers ``replies`` in order (an exception is raised)."""
    transport = MagicMock()
    transport.request = AsyncMock(side_effect=list(replies))
    driver = BrowserUseDriver()
    driver._transport = transport
    return driver, transport.request


def _calls(request: AsyncMock) -> list[tuple[str, dict[str, Any]]]:
    return [call.args for call in request.call_args_list]


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"


def _sidecar_error(code: str, message: str = "boom", data: dict[str, Any] | None = None) -> Exception:
    """The exception the real transport raises for a sidecar error reply with ``code``."""
    try:
        SidecarTransport._unwrap_reply({"id": 1, "error": {"code": code, "message": message, "data": data or {}}})
    except Exception as exc:  # noqa: BLE001 - we want exactly what the transport raises
        return exc
    raise AssertionError("transport did not raise")


# -- info ------------------------------------------------------------------

EXPECTED_ACTIONS = {
    Click: {DriverRef},
    TypeText: {DriverRef},
    PressKey: set(),
    Scroll: {DriverRef},
    Hover: {DriverRef},
    Drag: {DriverRef},
    Wait: set(),
    SwitchContext: set(),
    CloseContext: set(),
    HandleDialog: set(),
    RunScript: {DriverRef},
    Navigate: set(),
    Back: set(),
    OpenContext: set(),
    SelectOption: {DriverRef},
    UploadFile: {DriverRef},
    FillForm: {DriverRef},
    Drop: {DriverRef},
}


def test_info_actions_are_pinned():
    actions = BrowserUseDriver().info.actions
    assert {command: set(refs) for command, refs in actions.items()} == EXPECTED_ACTIONS


def test_index_ref_is_never_a_model_facing_ref_kind():
    for refs in BrowserUseDriver().info.actions.values():
        assert IndexRef not in refs


def test_info_describes_the_browser_surface():
    info = BrowserUseDriver().info
    assert info.name == "browser_use"
    assert info.surfaces == frozenset({"browser"})
    assert info.observes == frozenset({"elements", "screenshot", "native_text", "surface"})


@pytest.mark.parametrize("ref", [IndexRef(1, "g3"), Locator("#a", None, "g3"), PointRef(1, 2, "g3")])
def test_other_ref_kinds_are_unsupported_before_dispatch(ref):
    driver, request = _driver()
    with pytest.raises(Unsupported) as info:
        asyncio.run(driver.execute(Click(ref)))
    assert info.value.dispatched is False
    request.assert_not_called()


# -- wire requests -----------------------------------------------------------

WIRE_CASES = [
    (
        Click(E1, button="right", modifiers=("Shift",), count=2),
        "click",
        {"ref": WIRE_E1, "button": "right", "click_count": 2, "modifiers": ["Shift"]},
    ),
    (
        TypeText(E1, "hello", submit=True, sensitive=True, clear=False),
        "type_text",
        {"ref": WIRE_E1, "text": "hello", "clear": False, "press_enter": True, "sensitive": True},
    ),
    (PressKey("Control+A"), "press_key", {"keys": "Control+A"}),
    (Scroll(dy=300), "scroll", {"direction": "down", "amount": 300, "ref": None}),
    (Scroll(dy=-200), "scroll", {"direction": "up", "amount": 200, "ref": None}),
    (Scroll(E1, dx=50), "scroll", {"direction": "right", "amount": 50, "ref": WIRE_E1}),
    (Scroll(dx=-50), "scroll", {"direction": "left", "amount": 50, "ref": None}),
    (Hover(E1), "hover", {"ref": WIRE_E1}),
    (Drag(E1, E2), "drag", {"source": WIRE_E1, "target": WIRE_E2, "steps": 10, "delay_ms": 0}),
    (SelectOption(E1, ("Blue",)), "select_option", {"ref": WIRE_E1, "value": "Blue", "label": None}),
    (HandleDialog(accept=False, prompt_text="x"), "handle_dialog", {"accept": False, "prompt_text": "x"}),
    (SwitchContext(TAB_2), "switch_tab", {"tab": WIRE_TAB_2}),
    (CloseContext(TAB_2), "close_tab", {"tab": WIRE_TAB_2}),
]


@pytest.mark.parametrize(("action", "method", "params"), WIRE_CASES, ids=lambda case: getattr(case, "__name__", None))
def test_act_handler_sends_one_wire_request(action, method, params):
    driver, request = _driver(dict(ACT_OK))
    result = asyncio.run(driver.execute(action))
    assert _calls(request) == [(method, params)]
    assert result.ok is True
    assert result.detail == "done"
    assert result.generation == ""


@pytest.mark.parametrize(
    ("action", "method", "params"),
    [
        (
            Navigate("http://x/"),
            "navigate",
            {"url": "http://x/", "wait_until": "load", "timeout_ms": None, "new_tab": False},
        ),
        (Back(), "go_back", {}),
    ],
)
def test_navigation_handler_sends_one_wire_request(action, method, params):
    driver, request = _driver(dict(NAV_SAME_URL))
    result = asyncio.run(driver.execute(action))
    assert _calls(request) == [(method, params)]
    assert result.ok is True
    assert result.surface == BrowserContext(url="http://x/", title="X")


def test_wire_refs_carry_the_last_driver_generation():
    driver, request = _driver({**ACT_OK, "driver_generation": 7}, dict(ACT_OK))
    asyncio.run(driver.execute(Hover(E1)))
    asyncio.run(driver.execute(Hover(E1)))
    assert request.call_args_list[1].args[1]["ref"]["driver_generation"] == 7


def test_run_script_without_target_evaluates_page_js():
    driver, request = _driver({"answer": 42})
    result = asyncio.run(driver.execute(RunScript("(a) => a", args={"x": 1})))
    assert _calls(request) == [
        ("evaluate", {"source": "(a) => a", "args": {"x": 1}, "await_promise": True, "return_by_value": True})
    ]
    assert result.ok is True
    assert result.value == {"answer": 42}
    assert result.context_changed is False


def test_run_script_with_target_stamps_the_element_then_scopes_the_script():
    selector = '[data-openjiuwen-target-id="bnid:42"]'
    driver, request = _driver(selector, "Buy")
    result = asyncio.run(driver.execute(RunScript("(el, a) => el.textContent", args=[1], target=E1)))
    (stamp_method, stamp_params), (eval_method, eval_params) = _calls(request)
    assert (stamp_method, stamp_params) == (
        "stamp",
        {"ref": WIRE_E1, "attribute": "data-openjiuwen-target-id", "value": "bnid:42"},
    )
    assert eval_method == "evaluate"
    assert eval_params["args"] == {"selector": selector, "args": [1]}
    assert "document.querySelector(input.selector)" in eval_params["source"]
    assert "return ((el, a) => el.textContent)(element, input.args);" in eval_params["source"]
    assert result.value == "Buy"


def test_open_context_reports_the_new_tab():
    tab_1 = {"target_id": "T1", "url": "http://x/", "title": "X", "active": True}
    tab_2 = {"target_id": "T2", "url": "http://y/", "title": "Y", "active": False}
    nav = {"url": "http://y/", "title": "Y", "changed_document": True, "driver_generation": 2}
    driver, request = _driver([tab_1], nav, [tab_1, tab_2])
    result = asyncio.run(driver.execute(OpenContext("http://y/")))
    assert _calls(request) == [
        ("list_tabs", {}),
        ("navigate", {"url": "http://y/", "wait_until": "load", "timeout_ms": None, "new_tab": True}),
        ("list_tabs", {}),
    ]
    opened = ContextRef(id="T2", kind="tab", title="Y")
    assert result.opened_contexts == (opened,)
    assert result.value == opened
    assert result.context_changed is True


def test_open_context_without_url_opens_a_blank_tab():
    driver, request = _driver([], dict(NAV_SAME_URL), [])
    asyncio.run(driver.execute(OpenContext()))
    assert request.call_args_list[1].args[1]["url"] == "about:blank"


def test_upload_file_sends_resolved_paths():
    driver, request = _driver(dict(ACT_OK))
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        handle.write(b"x")
        path = handle.name
    try:
        asyncio.run(driver.execute(UploadFile(E1, (path,))))
    finally:
        Path(path).unlink(missing_ok=True)
    assert _calls(request) == [("upload_files", {"ref": WIRE_E1, "paths": [str(Path(path).resolve())]})]


def test_drop_data_only_sends_mime_items():
    driver, request = _driver(dict(ACT_OK))
    asyncio.run(driver.execute(Drop(E1, data=(("text/plain", "hi"), ("text/uri-list", "http://x/")))))
    assert _calls(request) == [
        (
            "drop",
            {
                "ref": WIRE_E1,
                "paths": [],
                "data": [{"mimeType": "text/plain", "data": "hi"}, {"mimeType": "text/uri-list", "data": "http://x/"}],
            },
        )
    ]


def test_fill_form_fills_fields_in_order():
    driver, request = _driver(dict(ACT_OK), dict(ACT_OK), dict(ACT_OK), dict(ACT_OK))
    form = FillForm(
        (
            FormField(E1, FieldKind.TEXTBOX, "Ann"),
            FormField(E1, FieldKind.COMBOBOX, "Blue"),
            FormField(E2, FieldKind.CHECKBOX, "true"),
            FormField(E2, FieldKind.RADIO, "false"),
        )
    )
    result = asyncio.run(driver.execute(form))
    assert _calls(request) == [
        ("type_text", {"ref": WIRE_E1, "text": "Ann", "clear": True, "press_enter": False, "sensitive": False}),
        ("select_option", {"ref": WIRE_E1, "value": "Blue", "label": None}),
        ("set_checked", {"ref": WIRE_E2, "checked": True}),
        ("set_checked", {"ref": WIRE_E2, "checked": False}),
    ]
    assert result.ok is True
    assert result.context_changed is False


def test_fill_form_passes_each_field_sensitivity_to_the_wire():
    driver, request = _driver(dict(ACT_OK), dict(ACT_OK))
    form = FillForm((FormField(E1, FieldKind.TEXTBOX, "Ann"), FormField(E2, FieldKind.TEXTBOX, "pw", sensitive=True)))
    asyncio.run(driver.execute(form))
    assert [params["sensitive"] for _method, params in _calls(request)] == [False, True]


def test_fill_form_stops_at_the_first_failed_field():
    failed = {"ok": False, "detail": "option not found", "document_changed": False, "driver_generation": 0}
    driver, request = _driver(dict(ACT_OK), failed)
    form = FillForm(
        (
            FormField(E1, FieldKind.TEXTBOX, "Ann"),
            FormField(E1, FieldKind.COMBOBOX, "Nope"),
            FormField(E2, FieldKind.CHECKBOX, "true"),
        )
    )
    result = asyncio.run(driver.execute(form))
    assert request.call_count == 2
    assert result.ok is False
    assert result.detail == "fields[1] failed: option not found"


@pytest.mark.parametrize(("failing_field", "dispatched"), [(0, False), (1, True)])
def test_fill_form_error_after_a_filled_field_counts_as_dispatched(failing_field, dispatched):
    replies = [dict(ACT_OK)] * failing_field + [_sidecar_error("ElementNotFound")]
    driver, _request = _driver(*replies)
    form = FillForm((FormField(E1, FieldKind.TEXTBOX, "a"), FormField(E2, FieldKind.TEXTBOX, "b")))
    with pytest.raises(ElementNotFound) as info:
        asyncio.run(driver.execute(form))
    assert info.value.dispatched is dispatched


def test_wait_seconds_sends_nothing():
    driver, request = _driver()
    assert asyncio.run(driver.execute(Wait(seconds=0))).ok is True
    request.assert_not_called()


def test_wait_until_text_polls_page_text(monkeypatch):
    monkeypatch.setattr(driver_module, "_WAIT_POLL_S", 0)
    driver, request = _driver(False, False, True)
    assert asyncio.run(driver.execute(Wait(until_text="Done"))).ok is True
    assert request.call_count == 3
    method, params = request.call_args.args
    assert method == "evaluate"
    assert params["args"] == "Done"
    assert "innerText" in params["source"]


def test_wait_until_text_times_out(monkeypatch):
    monkeypatch.setattr(driver_module, "_WAIT_TIMEOUT_S", 0)
    driver, _request = _driver(False)
    with pytest.raises(ActionTimeout) as info:
        asyncio.run(driver.execute(Wait(until_text="Never")))
    assert info.value.dispatched is True


def test_wait_until_text_uses_seconds_as_its_deadline(monkeypatch):
    monkeypatch.setattr(driver_module, "_WAIT_TIMEOUT_S", 3600.0)
    driver, request = _driver(False)
    with pytest.raises(ActionTimeout, match=r"within 0\.0s"):
        asyncio.run(driver.execute(Wait(until_text="Never", seconds=0.0)))
    assert request.call_count == 1


@pytest.mark.parametrize(
    "action",
    [
        SelectOption(E1, ("a", "b")),
        SelectOption(E1, ()),
        TypeText(None, "x"),
        TypeText(E1, "x", slowly=True),
        Scroll(dx=10, dy=10),
        Scroll(),
        UploadFile(None, ("a.txt",)),
        PressKey("Enter", target=E1),
    ],
)
def test_unsupported_forms_raise_before_dispatch(action):
    driver, request = _driver()
    with pytest.raises(Unsupported) as info:
        asyncio.run(driver.execute(action))
    assert info.value.dispatched is False
    request.assert_not_called()


# -- context_changed -----------------------------------------------------------


@pytest.mark.parametrize("action", [Navigate("http://x/"), Back()])
def test_same_url_reload_sets_context_changed(action):
    driver, _request = _driver(dict(NAV_SAME_URL))
    assert asyncio.run(driver.execute(action)).context_changed is True


@pytest.mark.parametrize(("document_changed", "expected"), [(True, True), (False, False)])
def test_act_result_follows_the_sidecar_document_change(document_changed, expected):
    driver, _request = _driver({**ACT_OK, "document_changed": document_changed})
    assert asyncio.run(driver.execute(Click(E1))).context_changed is expected


def test_a_dialog_opened_by_the_command_changes_the_context_and_is_named():
    dialog = {"type": "confirm", "message": "Proceed?"}
    driver, _request = _driver(
        {**ACT_OK, "detail": "the input waits until the dialog is handled", "dialog_opened": dialog}
    )
    result = asyncio.run(driver.execute(Click(E1)))
    assert result.ok is True
    assert result.context_changed is True
    assert result.detail == "the input waits until the dialog is handled; confirm dialog opened: 'Proceed?'"


def test_no_dialog_leaves_the_detail_alone():
    driver, _request = _driver({**ACT_OK, "dialog_opened": None})
    result = asyncio.run(driver.execute(Click(E1)))
    assert (result.context_changed, result.detail) == (False, "done")


def test_pages_opened_by_the_command_are_opened_contexts():
    popup = {"target_id": "T9", "url": "", "title": "", "active": False}
    driver, _request = _driver({**ACT_OK, "new_targets": [popup]})
    result = asyncio.run(driver.execute(Click(E1)))
    assert result.opened_contexts == (ContextRef(id="T9", kind="tab", title=""),)
    assert result.context_changed is True


def test_switch_context_always_changes_context():
    driver, _request = _driver(dict(ACT_OK))
    assert asyncio.run(driver.execute(SwitchContext(TAB_2))).context_changed is True


def test_close_context_changes_context_and_names_the_closed_one():
    driver, _request = _driver(dict(ACT_OK))
    result = asyncio.run(driver.execute(CloseContext(TAB_2)))
    assert result.context_changed is True
    assert result.source_context == TAB_2


# -- observe -------------------------------------------------------------------


TITLE_CALL = (
    "evaluate",
    {"source": "() => document.title", "args": None, "await_promise": True, "return_by_value": True},
)


def _wire_observation(**overrides: Any) -> dict[str, Any]:
    data = {
        "url": "http://x/",
        "title": "X",
        "tabs": [
            {"target_id": "T1", "url": "http://x/", "title": "X", "active": True},
            {"target_id": "T2", "url": "http://y/", "title": "Y", "active": False},
        ],
        "elements": [
            {
                "index": 3,
                "backend_node_id": 42,
                "driver_ref": {"handle": "bnid:42", "driver_generation": 9},
                "frame_id": None,
                "tag": "button",
                "role": "button",
                "name": "Buy",
                "value": None,
                "attributes": {"id": "buy"},
                "box": {"x": 1.0, "y": 2.0, "width": 30.0, "height": 10.0},
                "visible": True,
                "scrollable": False,
            }
        ],
        "ax_text": "[3]<button>Buy</button>",
        "screenshot_b64": None,
        "pixels_above": 10,
        "pixels_below": 900,
        "errors": [],
        "is_pdf_viewer": False,
        "captured_at": 0.0,
        "driver_generation": 9,
    }
    data.update(overrides)
    return data


def test_observe_maps_the_sidecar_observation():
    png = _png(800, 600)
    driver, request = _driver(_wire_observation(screenshot_b64=base64.b64encode(png).decode("ascii")), "X")
    observation = asyncio.run(driver.observe(Observe(include_screenshot=True)))

    assert _calls(request) == [
        ("observe", {"include_dom": True, "include_screenshot": True, "cached": False}),
        TITLE_CALL,
    ]
    assert observation.generation == ""
    assert observation.context == ContextRef(id="T1", kind="tab", title="X")
    (element,) = observation.elements
    assert element.ref == DriverRef(ref="bnid:42", generation="")
    assert (element.role, element.name, element.value) == ("button", "Buy", None)
    assert element.bounds == (1.0, 2.0, 30.0, 10.0)
    assert element.extra["index"] == 3
    assert element.extra["tag"] == "button"
    assert element.extra["attributes"] == {"id": "buy"}
    assert observation.native_text == "[3]<button>Buy</button>"
    assert observation.screenshot is not None
    assert observation.screenshot.png == png
    assert (observation.screenshot.width, observation.screenshot.height) == (800, 600)
    assert observation.surface == BrowserContext(
        url="http://x/",
        title="X",
        tabs=(ContextRef(id="T1", kind="tab", title="X"), ContextRef(id="T2", kind="tab", title="Y")),
        is_pdf_viewer=False,
        pixels_above=10,
        pixels_below=900,
        last_status=None,
    )


def test_observe_full_page_takes_a_separate_screenshot():
    png = _png(800, 4000)
    driver, request = _driver(_wire_observation(), "X", base64.b64encode(png).decode("ascii"))
    observation = asyncio.run(driver.observe(Observe(include_screenshot=True, full_page=True)))
    assert _calls(request) == [
        ("observe", {"include_dom": True, "include_screenshot": False, "cached": False}),
        TITLE_CALL,
        ("screenshot", {"full_page": True, "clip": None}),
    ]
    assert observation.screenshot.height == 4000


def test_observe_without_screenshot_has_none():
    driver, _request = _driver(_wire_observation(), "X")
    assert asyncio.run(driver.observe(Observe())).screenshot is None


def test_observe_without_an_active_tab_falls_back_to_the_page():
    tabs = [{"target_id": "T1", "url": "http://other/", "title": "O", "active": False}]
    driver, _request = _driver(_wire_observation(tabs=tabs), "X")
    assert asyncio.run(driver.observe(Observe())).context == ContextRef(id="", kind="tab", title="X")


def test_observe_twice_on_an_unchanged_page_gives_the_same_fingerprint():
    driver, _request = _driver(_wire_observation(driver_generation=1), "X", _wire_observation(driver_generation=2), "X")
    first = asyncio.run(driver.observe(Observe()))
    second = asyncio.run(driver.observe(Observe()))
    assert first.fingerprint == second.fingerprint


def test_observe_reports_the_document_title_not_the_sidecar_title():
    """browser-use's summary title lags behind ``document.title`` (seen live: the URL instead of the title)."""
    driver, request = _driver(_wire_observation(title="x/index.html"), "clicked")
    observation = asyncio.run(driver.observe(Observe()))
    assert _calls(request)[1] == TITLE_CALL
    assert observation.surface.title == "clicked"


def test_observe_keeps_the_sidecar_title_when_the_page_runs_no_script():
    driver, _request = _driver(_wire_observation(title="Viewer"), _sidecar_error("EvaluateError"))
    assert asyncio.run(driver.observe(Observe())).surface.title == "Viewer"


# -- errors --------------------------------------------------------------------

ERROR_TABLE = {
    "StaleIndexError": (StaleRef, False),
    "ElementNotFound": (ElementNotFound, False),
    "StaleNodeError": (StaleRef, False),
    "AmbiguousElement": (AmbiguousElement, True),
    "ActionTimeout": (ActionTimeout, True),
    "NavigationError": (NavigationError, True),
    "DriverNotConnected": (ConnectionLost, False),
    "DriverConnectionError": (ConnectionLost, True),
    "DriverConnectionLost": (ConnectionLost, True),
    "DriverUnsupported": (Unsupported, False),
    "ActionIntercepted": (ActionIntercepted, False),
    "DriverError": (DriverError, True),
    "ObserveError": (DriverError, True),
    "EvaluateError": (DriverError, True),
}


def test_error_table_covers_every_wire_error_code():
    assert set(ERROR_TABLE) == set(wire.ERROR_CODES)


@pytest.mark.parametrize(("code", "expected"), sorted(ERROR_TABLE.items()))
def test_sidecar_error_maps_to_typed_error(code, expected):
    error_type, dispatched = expected
    driver, _request = _driver(_sidecar_error(code, "boom"))
    with pytest.raises(DriverError) as info:
        asyncio.run(driver.execute(Hover(E1)))
    assert type(info.value) is error_type
    assert info.value.dispatched is dispatched
    assert "boom" in str(info.value)


@pytest.mark.parametrize("raised", [_sidecar_error("SomethingNew"), RuntimeError("raw failure")])
def test_unclassified_error_is_dispatched_and_logged(monkeypatch, raised):
    logger = MagicMock()
    monkeypatch.setattr(driver_module, "_logger", logger)
    driver, _request = _driver(raised)
    with pytest.raises(DriverError) as info:
        asyncio.run(driver.execute(Hover(E1)))
    assert type(info.value) is DriverError
    assert info.value.dispatched is True
    logger.warning.assert_called_once()
    assert str(raised) in [str(arg) for arg in logger.warning.call_args.args]


def test_intercepted_input_names_the_element_that_would_receive_it():
    driver, _request = _driver(_sidecar_error("ActionIntercepted", "click would reach it", {"by": "span#overlay"}))
    with pytest.raises(ActionIntercepted) as info:
        asyncio.run(driver.execute(Click(E1)))
    assert info.value.by == "span#overlay"
    assert info.value.dispatched is False


def test_classified_error_is_not_logged(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(driver_module, "_logger", logger)
    driver, _request = _driver(_sidecar_error("ElementNotFound"))
    with pytest.raises(ElementNotFound):
        asyncio.run(driver.execute(Hover(E1)))
    logger.warning.assert_not_called()


def test_unclassified_error_log_does_not_contain_the_typed_text(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(driver_module, "_logger", logger)
    driver, _request = _driver(_sidecar_error("DriverError", "sidecar crashed"))
    with pytest.raises(DriverError):
        asyncio.run(driver.execute(TypeText(E1, "s3cret-password", sensitive=True)))
    assert "s3cret-password" not in repr(logger.warning.call_args)


def test_command_before_start_is_connection_lost_and_not_dispatched():
    driver = BrowserUseDriver()
    with pytest.raises(ConnectionLost) as info:
        asyncio.run(driver.execute(Hover(E1)))
    assert info.value.dispatched is False


# -- lifecycle and factory ----------------------------------------------------


def _patched_transport(monkeypatch, *, connect_reply: Any) -> MagicMock:
    transport = MagicMock()
    transport.start = AsyncMock(return_value={"kind": "hello"})
    transport.request = AsyncMock(side_effect=[connect_reply])
    transport.close = AsyncMock()
    monkeypatch.setattr(driver_module, "SidecarTransport", MagicMock(return_value=transport))
    return transport


CONNECT_REPLY = {
    "backend": "browser_use",
    "browser_version": "Chrome/140",
    "protocol_version": "1.3",
    "backend_version": "0.13.10",
    "initial_url": "about:blank",
}


def test_factory_connects_to_the_device_endpoint(monkeypatch):
    transport = _patched_transport(monkeypatch, connect_reply=dict(CONNECT_REPLY))
    device = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9333", extras={}, cleanup=None)
    driver = create_browser_use_driver(device, None)
    asyncio.run(driver.start())
    transport.start.assert_awaited_once_with(timeout_s=30.0)
    assert transport.request.call_args.args == ("connect", {"cdp_url": "http://127.0.0.1:9333", "timeout_s": 30.0})
    assert driver.info.versions == {"browser": "Chrome/140", "protocol": "1.3", "browser_use": "0.13.10"}


def test_start_with_an_explicit_config_overrides_the_factory_config(monkeypatch):
    transport = _patched_transport(monkeypatch, connect_reply=dict(CONNECT_REPLY))
    driver = BrowserUseDriver(BrowserUseConfig(cdp_url="http://a"))
    asyncio.run(driver.start(BrowserUseConfig(cdp_url="http://b", timeout_s=5.0)))
    assert transport.request.call_args.args == ("connect", {"cdp_url": "http://b", "timeout_s": 5.0})


def test_start_without_config_is_refused():
    with pytest.raises(DriverError) as info:
        asyncio.run(BrowserUseDriver().start())
    assert info.value.dispatched is False


def test_failed_connect_closes_the_sidecar_and_raises_connection_lost(monkeypatch):
    transport = _patched_transport(monkeypatch, connect_reply=_sidecar_error("DriverConnectionError"))
    driver = BrowserUseDriver(BrowserUseConfig(cdp_url="http://a"))
    with pytest.raises(ConnectionLost):
        asyncio.run(driver.start())
    transport.close.assert_awaited_once()
    assert asyncio.run(driver.health()).ok is False


def test_stop_closes_the_transport():
    driver, _request = _driver()
    transport = driver._transport
    transport.close = AsyncMock()
    asyncio.run(driver.stop())
    transport.close.assert_awaited_once()
    assert asyncio.run(driver.health()).detail == "not started"


def test_health_maps_the_sidecar_reply():
    driver, request = _driver({"connected": True, "url": "http://x/", "tab_count": 1, "latency_ms": 1.0, "error": None})
    health = asyncio.run(driver.health())
    assert (health.ok, health.detail) == (True, None)
    assert _calls(request) == [("health", {})]


def test_health_never_raises():
    driver, _request = _driver(_sidecar_error("DriverConnectionLost", "gone"))
    health = asyncio.run(driver.health())
    assert (health.ok, health.detail) == (False, "gone")


def test_result_types_are_contract_results():
    driver, _request = _driver(dict(ACT_OK))
    assert isinstance(asyncio.run(driver.execute(Hover(E1))), ActionResult)


def test_importing_browser_use_driver_does_not_load_browser_use_package() -> None:
    snapshot = set(sys.modules)
    importlib.reload(importlib.import_module("openjiuwen.harness.tools.browser_move.drivers.browser_use.driver"))
    added = set(sys.modules) - snapshot
    browser_use_modules = [name for name in added if name == "browser_use" or name.startswith("browser_use.")]
    assert not browser_use_modules, f"unexpected browser_use imports: {browser_use_modules}"
