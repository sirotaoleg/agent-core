# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The controller's custom actions over commands on a scripted driver: coordinates, drag and drop, file inputs."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    Drag,
    DriverError,
    DriverInfo,
    DriverRef,
    Locator,
    PointRef,
    RunScript,
)
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS, FakeDriver
from openjiuwen.harness.tools.browser_move.runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import ToolContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate, UploadFile
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers import (
    CUSTOM_ACTIONS,
    drag_and_drop,
    get_element_coordinates,
    set_input_files,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

DONE = ActionResult(ok=True)
FILE_INPUT = Locator('input[type="file"]', None, "g0")
ON_ELEMENTS = {**DEFAULT_ACTIONS, Navigate: frozenset(), UploadFile: frozenset({DriverRef, Locator})}
THROUGH_CHOOSER = {**DEFAULT_ACTIONS, Navigate: frozenset(), UploadFile: frozenset()}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _context(*results: ActionResult, actions: dict[type, frozenset] = ON_ELEMENTS) -> ToolContext:
    info = DriverInfo(name="fake", surfaces=frozenset({"browser"}), actions=actions)
    page_state = BrowserPageState()
    driver = FakeDriver(results=results, info=info)
    session = DriverSession(DriverAssembly(stack=driver, device=None, provisioner=None), page_state, MESSAGES)
    return ToolContext(session, page_state, BrowserRenderer(), info, BrowserSettings(upload_root=Path("/tmp")), False)


def _sent(context: ToolContext) -> list[Any]:
    return [call.payload for call in context.driver.assembly.stack.calls if call.kind == "execute"]


def _points(source: Any = None, target: Any = None) -> ActionResult:
    return ActionResult(ok=True, value={"source": source, "target": target})


def test_the_custom_actions_keep_their_controller_names() -> None:
    assert CUSTOM_ACTIONS == {
        "browser_get_element_coordinates": get_element_coordinates,
        "browser_drag_and_drop": drag_and_drop,
        "browser_set_input_files": set_input_files,
    }


def test_coordinates_of_one_element_come_from_one_page_script() -> None:
    context = _context(_points({"x": 12, "y": 34}))

    result = _run(get_element_coordinates(context, session_id="s-json", request_id="r-json", element_source="Example"))

    assert result == {"ok": True, "source": {"x": 12, "y": 34}, "target": None, "error": None}
    [script] = _sent(context)
    assert isinstance(script, RunScript)
    assert (script.args["source"], script.args["target"]) == ("Example", "")


def test_coordinates_take_the_aliases_and_offsets() -> None:
    context = _context(_points({"x": 1, "y": 2}, {"x": 3, "y": 4}))

    _run(get_element_coordinates(context, source="#a", target="#b", element_target_offset={"x": "5", "y": 6}))

    args = _sent(context)[0].args
    assert (args["source"], args["target"], args["target_offset"], args["source_offset"]) == (
        "#a",
        "#b",
        {"x": 5, "y": 6},
        None,
    )


def test_coordinates_given_as_numbers_come_back_without_a_page_script() -> None:
    context = _context()

    result = _run(get_element_coordinates(context, source_x=1, source_y=2, coord_target_x="3", target_y=4))

    assert result == {"ok": True, "source": {"x": 1, "y": 2}, "target": {"x": 3, "y": 4}, "error": None}
    assert _sent(context) == []


def test_coordinates_open_the_url_first() -> None:
    context = _context(DONE, _points({"x": 1, "y": 1}))

    _run(get_element_coordinates(context, url="https://example.test/", element_source="#a"))

    assert _sent(context)[0] == Navigate("https://example.test/")


def test_coordinates_report_an_element_the_page_does_not_have() -> None:
    missing_source = _run(get_element_coordinates(_context(_points()), element_source="More information"))
    missing_target = _run(
        get_element_coordinates(_context(_points({"x": 1, "y": 1})), element_source="#a", element_target="#gone")
    )

    assert missing_source["ok"] is False and "Use the exact visible text" in missing_source["error"]
    assert missing_target == {
        "ok": False,
        "source": {"x": 1, "y": 1},
        "target": None,
        "error": "Failed to determine target coordinates from selector",
    }


def test_coordinates_without_any_location_ask_for_one() -> None:
    result = _run(get_element_coordinates(_context(), source_x=1))

    assert result["ok"] is False and result["error"].startswith("Missing location inputs.")


def test_coordinates_report_a_driver_error_with_the_ids() -> None:
    context = _context()
    context.driver.assembly.stack.fail_next(DriverError("page closed"))

    result = _run(get_element_coordinates(context, session_id="s", request_id="r", element_source="#a"))

    assert result == {
        "ok": False,
        "error": "browser_get_element_coordinates failed: page closed",
        "session_id": "s",
        "request_id": "r",
    }


def test_drag_between_elements_marks_both_and_drags_one_onto_the_other() -> None:
    context = _context(_points({"x": 10, "y": 20}, {"x": 30, "y": 40}), DONE)

    result = _run(drag_and_drop(context, element_source="#card", element_target="Done column", steps=5))

    script, drag = _sent(context)
    marks = (script.args["source_mark"], script.args["target_mark"])
    assert drag == Drag(
        Locator(f'[data-openjiuwen-target-id="{marks[0]}"]', None, "g0"),
        Locator(f'[data-openjiuwen-target-id="{marks[1]}"]', None, "g0"),
    )
    assert result == {
        "ok": True,
        "message": "Dragged element '#card' to 'Done column'",
        "source": {"x": 10, "y": 20},
        "target": {"x": 30, "y": 40},
        "steps": 5,
        "delay_ms": None,
        "error": None,
    }


def test_drag_between_points_drags_points_of_the_last_screenshot() -> None:
    context = _context(DONE)
    context.page_state.screenshot_generation = "g0"

    result = _run(drag_and_drop(context, coord_source_x=1, coord_source_y=2, coord_target_x=3, coord_target_y=4))

    assert _sent(context) == [Drag(PointRef(1, 2, "g0"), PointRef(3, 4, "g0"))]
    assert result["message"] == "Dragged from (1, 2) to (3, 4)"


def test_drag_a_driver_cannot_do_reports_why() -> None:
    no_points = {**ON_ELEMENTS, Drag: frozenset({DriverRef, Locator})}
    context = _context(actions=no_points)

    result = _run(drag_and_drop(context, source_x=1, source_y=2, target_x=3, target_y=4))

    assert result["ok"] is False and "browser_drag_and_drop failed" in result["error"]


def test_drag_reports_an_element_the_page_does_not_have_without_dragging() -> None:
    context = _context(_points({"x": 10, "y": 20}))

    result = _run(drag_and_drop(context, element_source="#card", element_target="#nowhere"))

    assert result["error"] == "Failed to determine source or target coordinates from selectors"
    assert [type(action) for action in _sent(context)] == [RunScript]


def test_drag_without_both_ends_asks_for_them() -> None:
    result = _run(drag_and_drop(_context(), element_source="#card"))

    assert result["ok"] is False and result["error"].startswith("Missing drag inputs.")


def test_set_input_files_returns_error_when_paths_empty() -> None:
    result = _run(set_input_files(_context(), paths=[]))

    assert result["ok"] is False
    assert "paths" in result["error"].lower()


def test_set_input_files_on_a_driver_that_sets_files_on_an_element_sends_one_upload() -> None:
    context = _context(DONE)

    result = _run(set_input_files(context, selector="#upload", paths=["/tmp/x.pdf"]))

    assert _sent(context) == [UploadFile(Locator("#upload", None, "g0"), ("/tmp/x.pdf",))]
    assert result == {"ok": True, "selector": "#upload", "paths": ["/tmp/x.pdf"], "error": None}


def test_set_input_files_defaults_selector_to_file_input() -> None:
    context = _context(DONE)

    _run(set_input_files(context, paths=["/tmp/f.txt"]))

    assert _sent(context)[0].target == FILE_INPUT


def test_set_input_files_outside_the_upload_folders_never_reaches_the_driver() -> None:
    context = _context(DONE)
    outside = str(Path(__file__).resolve())

    result = _run(set_input_files(context, paths=[outside]))

    assert _sent(context) == []
    assert result["ok"] is False and "outside the folders files may be uploaded from" in result["error"]


def test_set_input_files_judges_a_relative_name_under_the_upload_root(tmp_path: Path, monkeypatch) -> None:
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    for folder in (allowed, outside):
        folder.mkdir()
        (folder / "report.txt").write_text(folder.name, encoding="utf-8")
    monkeypatch.chdir(outside)
    context = _context(DONE, DONE)
    context = dataclasses.replace(context, settings=BrowserSettings(upload_root=allowed))

    passed = _run(set_input_files(context, paths=["report.txt"]))
    climbed = _run(set_input_files(context, paths=["../outside/report.txt"]))

    assert passed["ok"] is True and _sent(context) == [UploadFile(FILE_INPUT, ("report.txt",))]
    assert climbed["ok"] is False and "outside the upload folder" in climbed["error"]


def test_set_input_files_on_a_driver_with_a_chooser_clicks_the_input_then_answers_the_chooser() -> None:
    context = _context(DONE, DONE, actions=THROUGH_CHOOSER)

    _run(set_input_files(context, paths=["/tmp/f.txt"]))

    assert _sent(context) == [Click(FILE_INPUT), UploadFile(None, ("/tmp/f.txt",))]


def test_set_input_files_reports_the_page_refusing_the_files() -> None:
    context = _context(ActionResult(ok=False, detail="### Error\nfile not found: /tmp/f.txt"))

    result = _run(set_input_files(context, paths=["/tmp/f.txt"]))

    assert (result["ok"], result["error"]) == (False, "file not found: /tmp/f.txt")


def test_set_input_files_hints_at_a_narrower_selector_when_several_inputs_match() -> None:
    context = _context(actions=THROUGH_CHOOSER)
    context.driver.assembly.stack.fail_next(DriverError("strict mode violation: locator resolved to 2 elements"))

    result = _run(set_input_files(context, paths=["/tmp/f.txt"]))

    assert result["ok"] is False and "more specific selector" in result["hint"]
