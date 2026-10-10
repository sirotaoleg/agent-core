# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser renderer on recorded MCP answers and on FakeDriver results, with each expected difference named."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionIntercepted,
    ActionResult,
    Click,
    CloseContext,
    ContextRef,
    DriverError,
    DriverRef,
    HandleDialog,
    Locator,
    Observation,
    Observe,
    RunScript,
    Screenshot,
    StaleRef,
    SwitchContext,
    TypeText,
    Wait,
)
from openjiuwen.harness.tools.browser_move.contract.testing.fake import FakeDriver
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import decode, result_text
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import parse_snapshot, without_boxes
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
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "mcp"
RENDERER = BrowserRenderer()
E1 = DriverRef("e1", "g1")
TAB = ContextRef("0", "tab", "Fixture second page")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SNAPSHOT_LINK = re.compile(r"\n### Snapshot\n- \[Snapshot\]\([^)]*\)")
_EVENTS = re.compile(r"\n### Events\n(?:- .*(?:\n|$))+")

ACTION_RESULT_CASES = [
    "click_success",
    "click_navigates",
    "click_opens_tab",
    "dialog_open_by_click",
    "dialog_handle_accept",
    "events_console_message",
    "events_download",
    "file_chooser_open_by_click",
    "file_upload",
    "fill_form",
    "navigate_back",
    "navigate_same_url_reload",
    "navigate_second_page",
    "select_option",
]
TAB_CASES = ["tabs_list", "tabs_new", "tabs_select", "tabs_close", "tabs_list_during_dialog"]
REFUSED_CASES = ["click_blocked_by_dialog", "snapshot_during_dialog"]
ERROR_CASES = [
    "click_intercepted_by_overlay",
    "click_stale_ref",
    "click_timeout_after_action",
    "click_timeout_not_enabled",
    "drop_outside_roots",
    "evaluate_browser_closed",
    "evaluate_error",
    "evaluate_new_tab_failed",
    "wait_for_timeout",
]
SNAPSHOT_CASES = ["snapshot_form_page", "snapshot_after_fill_form", "snapshot_two_tabs"]


def _fixture(case: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8"))


def _action(request: dict[str, Any]) -> Action:
    tool, args = request["tool"], request["arguments"]
    if tool == "browser_tabs":
        tabs = {"list": Observe(), "new": OpenContext(), "select": SwitchContext(TAB), "close": CloseContext(TAB)}
        return tabs[args["action"]]
    return {
        "browser_click": Click(E1),
        "browser_evaluate": RunScript(args.get("function", "")),
        "browser_navigate": Navigate(args.get("url", "")),
        "browser_navigate_back": Back(),
        "browser_handle_dialog": HandleDialog(True),
        "browser_file_upload": UploadFile(None, ("attachment.csv",)),
        "browser_fill_form": FillForm((FormField(E1, FieldKind.TEXTBOX, "Ada"),)),
        "browser_select_option": SelectOption(E1, ("Large",)),
        "browser_take_screenshot": Observe(include_screenshot=True),
        "browser_drop": Drop(E1, paths=("outside.csv",)),
        "browser_wait_for": Wait(until_text="Never shown"),
        "browser_snapshot": Observe(),
    }[tool]


def _decode(case: str, tmp_path: Path) -> tuple[Action, ActionResult]:
    fixture = _fixture(case)
    request = fixture["request"]
    return _action(request), decode(request["tool"], fixture["raw_result"], output_dir=tmp_path)


def _error(case: str, tmp_path: Path) -> tuple[Action, DriverError]:
    with pytest.raises(DriverError) as caught:
        _decode(case, tmp_path)
    return _action(_fixture(case)["request"]), caught.value


def _enveloped(case: str) -> str:
    return json.loads(_fixture(case)["model_text"])["result"]


def _observed(case: str) -> Observation:
    """What the driver's ``observe()`` returns for a recorded snapshot."""
    text = result_text(_fixture(case)["raw_result"])
    elements, header = parse_snapshot(text)
    return Observation(context=header.focused, elements=elements, native_text=without_boxes(text))


@pytest.mark.parametrize("case", ACTION_RESULT_CASES)
def test_action_result_renders_the_server_text_without_snapshot_link_and_events(case: str, tmp_path: Path) -> None:
    action, result = _decode(case, tmp_path)
    expected = _EVENTS.sub("", _SNAPSHOT_LINK.sub("", _fixture(case)["model_text"]))
    assert RENDERER.result(action, result) == (expected,)


def test_snapshot_file_link_the_model_cannot_open_is_gone(tmp_path: Path) -> None:
    action, result = _decode("click_success", tmp_path)
    assert "[Snapshot](" not in RENDERER.result(action, result)[0]
    assert "[Snapshot](" in _fixture("click_success")["model_text"]


@pytest.mark.parametrize("case", ["events_console_message", "events_download"])
def test_events_section_is_gone(case: str, tmp_path: Path) -> None:
    action, result = _decode(case, tmp_path)
    assert "### Events" not in RENDERER.result(action, result)[0]


@pytest.mark.parametrize("case", ERROR_CASES)
def test_error_is_plain_text_without_envelope_or_ansi(case: str, tmp_path: Path) -> None:
    action, error = _error(case, tmp_path)
    assert RENDERER.error(action, error, None) == (_ANSI.sub("", _enveloped(case)).rstrip("\n"),)


def test_stale_ref_error_carries_a_fresh_observation(tmp_path: Path) -> None:
    action, error = _error("click_stale_ref", tmp_path)
    fresh = _observed("snapshot_form_page")
    assert RENDERER.error(action, error, fresh) == (f"{_enveloped('click_stale_ref')}\n\n{fresh.native_text}",)


def test_stale_ref_from_the_stack_renders_the_observation_it_carries() -> None:
    fresh = Observation(context=TAB, native_text="- button [ref=e2]", screenshot=Screenshot(b"png", 1, 1))
    error = StaleRef("Ref e1 not found in the current page snapshot.", observation=fresh)
    assert RENDERER.error(Click(E1), error, None) == (
        "### Error\nRef e1 not found in the current page snapshot.\n\n- button [ref=e2]",
        fresh.screenshot,
    )


def test_intercepted_click_reports_the_message_and_leaves_the_hint_to_the_rail(tmp_path: Path) -> None:
    action, error = _error("click_intercepted_by_overlay", tmp_path)
    assert isinstance(error, ActionIntercepted)
    assert error.by == "div#consent-backdrop.consent-dark-filter"
    assert RENDERER.error(action, error, None) == (f"### Error\n{error}",)


def test_run_script_json_result_is_identical(tmp_path: Path) -> None:
    action, result = _decode("evaluate_result", tmp_path)
    assert RENDERER.result(action, result) == (_fixture("evaluate_result")["model_text"],)


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("plain text", "plain text"),
        (None, "undefined"),
        (42, "42"),
        ([1, "a"], '[\n  1,\n  "a"\n]'),
        ({"name": "Zoë"}, '{\n  "name": "Zoë"\n}'),
    ],
)
def test_run_script_values_render_as_the_server_shows_them(value: Any, shown: str) -> None:
    result = ActionResult(ok=True, value=value)
    assert RENDERER.result(RunScript("() => x"), result) == (f"### Result\n{shown}",)


@pytest.mark.parametrize("case", TAB_CASES)
def test_tab_list_is_rebuilt_from_the_surface(case: str, tmp_path: Path) -> None:
    action, result = _decode(case, tmp_path)
    assert RENDERER.result(action, result) == (_fixture(case)["model_text"],)


def test_tab_list_without_tab_urls_shows_titles_only(tmp_path: Path) -> None:
    action, result = _decode("tabs_new", tmp_path)
    unknown = dataclasses.replace(result.surface, tab_urls=(), current_tab=None)
    assert RENDERER.result(action, dataclasses.replace(result, surface=unknown)) == (
        "### Result\n- 0: [Fixture second page]\n- 1: []",
    )


def test_current_tab_without_its_own_url_shows_the_surface_url() -> None:
    surface = BrowserContext(url="https://a.test/", title="A", tabs=(ContextRef("0", "tab", "A"),), current_tab=0)
    result = ActionResult(ok=True, surface=surface)
    assert RENDERER.result(Observe(), result) == ("### Result\n- 0: (current) [A](https://a.test/)",)


def test_tab_list_is_always_listed_for_tab_commands_even_with_one_tab() -> None:
    surface = BrowserContext(url="https://a.test/", tabs=(TAB,), tab_urls=("https://a.test/",), current_tab=0)
    result = ActionResult(ok=True, context_changed=True, surface=surface)
    expected = ("### Result\n- 0: (current) [Fixture second page](https://a.test/)",)
    assert RENDERER.result(SwitchContext(TAB), result) == expected
    assert RENDERER.result(CloseContext(TAB), result) == expected


def test_tab_command_with_detail_renders_the_detail(tmp_path: Path) -> None:
    _, result = _decode("navigate_second_page", tmp_path)
    assert RENDERER.result(OpenContext("http://x.test/"), result) == (result.detail,)


@pytest.mark.parametrize("case", ["click_success", "select_option", "click_navigates", "dialog_open_by_click"])
def test_element_description_never_appears_in_the_rendered_result(case: str, tmp_path: Path) -> None:
    action, result = _decode(case, tmp_path)
    assert _fixture(case)["request"]["arguments"]["element"] not in RENDERER.result(action, result)[0]


@pytest.mark.parametrize("case", SNAPSHOT_CASES)
def test_snapshot_renders_the_full_server_text(case: str) -> None:
    assert RENDERER.observation(_observed(case)) == (_fixture(case)["model_text"],)


def test_snapshot_taken_with_boxes_renders_without_them() -> None:
    rendered = RENDERER.observation(_observed("snapshot_with_boxes"))[0]
    assert "[box=" not in rendered
    assert "[box=" in _fixture("snapshot_with_boxes")["model_text"]
    assert rendered.startswith("### Page\n")


@pytest.mark.parametrize("case", REFUSED_CASES)
def test_modal_refusal_stays_plain_error_text(case: str, tmp_path: Path) -> None:
    action, result = _decode(case, tmp_path)
    assert not result.ok
    assert RENDERER.result(action, result) == (_enveloped(case),)


def test_screenshot_file_link_is_replaced_by_the_image_block(tmp_path: Path) -> None:
    action, result = _decode("screenshot_element", tmp_path)
    text, image = RENDERER.result(action, result)
    assert isinstance(image, Screenshot) and image.png.startswith(b"\x89PNG")
    assert text.startswith("### Ran Playwright code\n")
    assert "[Screenshot of" not in text and "[binary content" not in text


def test_observation_with_a_screenshot_adds_an_image_block() -> None:
    shot = Screenshot(b"png", 2, 2)
    observation = Observation(context=TAB, native_text="tree", screenshot=shot)
    assert RENDERER.observation(observation) == ("tree", shot)
    assert RENDERER.observation(Observation(context=TAB, screenshot=shot)) == (shot,)


@pytest.mark.asyncio
async def test_fake_driver_results_render_with_the_same_rules() -> None:
    driver = FakeDriver(
        results=[
            ActionResult(ok=True, value={"ok": True}),
            ActionResult(ok=True),
            ActionResult(ok=True, detail="typed"),
            ActionResult(ok=False, detail="fields[0] failed: not editable"),
        ]
    )
    script = RunScript("() => ({ok: true})")
    assert RENDERER.result(script, await driver.execute(script)) == ('### Result\n{\n  "ok": true\n}',)
    click = Click(Locator(css="#save", text=None, generation="g1"))
    assert RENDERER.result(click, await driver.execute(click)) == ()
    typing = TypeText(E1, "Ada")
    assert RENDERER.result(typing, await driver.execute(typing)) == ("typed",)
    assert RENDERER.result(click, await driver.execute(click)) == ("### Error\nfields[0] failed: not editable",)
