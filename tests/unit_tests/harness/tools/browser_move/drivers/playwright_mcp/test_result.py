# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The Playwright MCP result decoder on recorded @playwright/mcp answers."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    ConnectionLost,
    ContextRef,
    DriverError,
    Screenshot,
    StaleRef,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import decode
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "mcp"
BASE = "http://127.0.0.1:<PORT>"
# The recorded file names carry "<TS>", which Windows does not allow in a file name.
TIMESTAMP = "2026-10-10T00-00-00-000Z"
SNAPSHOT_FILE = Path(".playwright-mcp") / f"page-{TIMESTAMP}.yml"


def _fixture(case: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8"))


def _raw(case: str) -> dict[str, Any]:
    return json.loads(json.dumps(_fixture(case)["raw_result"]).replace("<TS>", TIMESTAMP))


def _decode(case: str, output_dir: Path, before: BrowserContext | None = None) -> ActionResult:
    return decode(_fixture(case)["request"]["tool"], _raw(case), output_dir=output_dir, before=before)


def _tab(index: int, title: str) -> ContextRef:
    return ContextRef(id=str(index), kind="tab", title=title)


def _inline_yaml(case: str) -> str:
    text = _fixture(case)["raw_result"]["content"][0]["text"]
    return text.split("```yaml\n", 1)[1].rsplit("```", 1)[0]


def _one_tab(url: str, title: str) -> BrowserContext:
    return BrowserContext(url=url, title=title, tabs=(_tab(0, title),), tab_urls=(url,), current_tab=0)


FORM = _one_tab(f"{BASE}/index.html", "Fixture form page")
POPUP = _one_tab(f"{BASE}/popup.html", "Fixture popup page")
SECOND = _one_tab(f"{BASE}/second.html", "Fixture second page")
POPUP_AND_SECOND = BrowserContext(
    url=f"{BASE}/popup.html",
    title="Fixture popup page",
    tabs=(_tab(0, "Fixture popup page"), _tab(1, "Fixture second page")),
    tab_urls=(f"{BASE}/popup.html", f"{BASE}/second.html"),
    current_tab=0,
)
SECOND_AND_BLANK = BrowserContext(
    url=f"{BASE}/second.html",
    title="Fixture second page",
    tabs=(_tab(0, "Fixture second page"), _tab(1, "")),
    tab_urls=(f"{BASE}/second.html", "about:blank"),
    current_tab=0,
)


# case -> (before, ok, context_changed)
DECODED = {
    "snapshot_form_page": (FORM, True, False),
    "snapshot_depth_1": (FORM, True, False),
    "snapshot_target": (FORM, True, False),
    "snapshot_with_boxes": (FORM, True, False),
    "snapshot_after_fill_form": (FORM, True, False),
    "snapshot_two_tabs": (POPUP_AND_SECOND, True, False),
    "click_success": (FORM, True, False),
    "events_console_message": (FORM, True, False),
    "events_download": (FORM, True, False),
    "dialog_open_by_click": (FORM, True, True),
    "dialog_handle_accept": (FORM, True, False),
    "click_blocked_by_dialog": (FORM, False, False),
    "snapshot_during_dialog": (FORM, False, False),
    "tabs_list_during_dialog": (FORM, True, True),
    "navigate_same_url_reload": (FORM, True, True),
    "navigate_second_page": (FORM, True, True),
    "navigate_back": (POPUP_AND_SECOND, True, True),
    "click_navigates": (POPUP_AND_SECOND, True, True),
    "click_opens_tab": (POPUP, True, True),
    "tabs_list": (SECOND, True, True),
    "tabs_new": (SECOND, True, True),
    "tabs_select": (SECOND_AND_BLANK, True, True),
    "tabs_close": (SECOND_AND_BLANK, True, True),
    "fill_form": (FORM, True, False),
    "select_option": (FORM, True, False),
    "evaluate_result": (FORM, True, False),
    "screenshot_element": (FORM, True, False),
    "file_chooser_open_by_click": (
        _one_tab(f"{BASE}/upload.html", "Fixture upload page"),
        True,
        False,
    ),
    "file_upload": (
        _one_tab(f"{BASE}/upload.html", ""),
        True,
        False,
    ),
}

# case -> (error type, dispatched)
RAISED = {
    "click_intercepted_by_overlay": (ActionIntercepted, False),
    "click_timeout_not_enabled": (ActionTimeout, False),
    "click_timeout_after_action": (ActionTimeout, True),
    "wait_for_timeout": (ActionTimeout, True),
    "click_stale_ref": (StaleRef, False),
    "evaluate_new_tab_failed": (ConnectionLost, True),
    "evaluate_browser_closed": (ConnectionLost, True),
    "evaluate_error": (DriverError, True),
    "drop_outside_roots": (DriverError, False),
}


def test_every_fixture_is_covered() -> None:
    assert set(DECODED) | set(RAISED) == {path.stem for path in FIXTURES_DIR.glob("*.json")}


@pytest.mark.parametrize("case", sorted(DECODED))
def test_decoded_outcome(case: str, tmp_path: Path) -> None:
    before, ok, changed = DECODED[case]

    result = _decode(case, tmp_path, before)

    assert (result.ok, result.context_changed) == (ok, changed)
    assert result.generation == ""


@pytest.mark.parametrize("case", sorted(RAISED))
def test_error_type_and_dispatched(case: str, tmp_path: Path) -> None:
    error_type, dispatched = RAISED[case]

    with pytest.raises(DriverError) as caught:
        _decode(case, tmp_path, FORM)

    assert type(caught.value) is error_type
    assert caught.value.dispatched is dispatched
    assert "\x1b" not in str(caught.value)


def test_intercepted_click_names_the_blocker(tmp_path: Path) -> None:
    with pytest.raises(ActionIntercepted) as caught:
        _decode("click_intercepted_by_overlay", tmp_path)

    assert caught.value.by == "div#consent-backdrop.consent-dark-filter"


def test_unclassified_error_keeps_the_text(tmp_path: Path) -> None:
    with pytest.raises(DriverError) as caught:
        _decode("evaluate_error", tmp_path)

    assert str(caught.value).startswith("Error: fixture failure\n    at eval")


def test_detail_drops_events_and_the_snapshot_link(tmp_path: Path) -> None:
    result = _decode("events_download", tmp_path)

    assert result.detail == (
        "### Ran Playwright code\n```js\n"
        "await page.getByRole('link', { name: 'Download report' }).click();\n```\n"
        f"### Page\n- Page URL: {BASE}/index.html\n- Page Title: Fixture form page"
    )


def test_a_sensitive_call_leaves_the_code_out_of_the_detail(tmp_path: Path) -> None:
    raw = _raw("click_success")

    sensitive = decode("browser_click", raw, output_dir=tmp_path, sensitive=True)
    plain = decode("browser_click", raw, output_dir=tmp_path)

    assert plain.detail is not None and "### Ran Playwright code" in plain.detail
    assert sensitive.detail == f"### Page\n- Page URL: {BASE}/index.html\n- Page Title: Fixture form page"


@pytest.mark.parametrize("case", ["events_console_message", "click_success", "navigate_second_page"])
def test_detail_has_no_events_or_file_links(case: str, tmp_path: Path) -> None:
    detail = _decode(case, tmp_path).detail

    assert detail is not None
    assert "### Events" not in detail
    assert "### Snapshot" not in detail
    assert ".playwright-mcp" not in detail


def test_same_url_reload_changes_the_context(tmp_path: Path) -> None:
    result = _decode("navigate_same_url_reload", tmp_path, FORM)

    assert result.context_changed is True
    assert result.surface == FORM


def test_navigation_changes_the_context_without_before(tmp_path: Path) -> None:
    assert _decode("navigate_second_page", tmp_path).context_changed is True


def test_click_without_url_or_tab_change_keeps_the_context(tmp_path: Path) -> None:
    result = _decode("click_success", tmp_path, FORM)

    assert result.context_changed is False
    assert result.opened_contexts == ()


def test_click_that_changes_the_url_changes_the_context(tmp_path: Path) -> None:
    result = _decode("click_navigates", tmp_path, POPUP_AND_SECOND)

    assert result.context_changed is True
    assert result.surface == BrowserContext(
        url=f"{BASE}/second.html",
        title="Fixture second page",
        tabs=(_tab(0, "Fixture second page"), _tab(1, "Fixture second page")),
        tab_urls=(f"{BASE}/second.html", f"{BASE}/second.html"),
        current_tab=0,
    )


def test_click_that_opens_a_tab_reports_it(tmp_path: Path) -> None:
    result = _decode("click_opens_tab", tmp_path, POPUP)

    assert result.context_changed is True
    assert result.opened_contexts == (_tab(1, "Fixture second page"),)
    assert result.surface == POPUP_AND_SECOND


def test_new_tab_from_the_tab_list(tmp_path: Path) -> None:
    result = _decode("tabs_new", tmp_path, SECOND)

    assert result.opened_contexts == (_tab(1, ""),)
    assert result.surface == BrowserContext(
        url="about:blank", title="", tabs=SECOND_AND_BLANK.tabs, tab_urls=SECOND_AND_BLANK.tab_urls, current_tab=1
    )
    assert (result.value, result.detail) == (None, None)


def test_closing_a_tab_reports_the_remaining_tabs(tmp_path: Path) -> None:
    result = _decode("tabs_close", tmp_path, SECOND_AND_BLANK)

    assert result.opened_contexts == ()
    assert result.surface == SECOND


def test_dialog_opened_by_click_changes_the_context(tmp_path: Path) -> None:
    result = _decode("dialog_open_by_click", tmp_path)

    assert result.context_changed is True
    assert result.detail is not None
    assert '["confirm" dialog with message "Delete order?"]' in result.detail
    assert result.surface == dataclasses.replace(_one_tab(f"{BASE}/index.html", ""), dialog="confirm: Delete order?")


def test_file_chooser_is_reported_but_keeps_the_context(tmp_path: Path) -> None:
    result = _decode("file_chooser_open_by_click", tmp_path)

    assert result.context_changed is False
    assert result.detail is not None
    assert "- [File chooser]: can be handled by browser_file_upload" in result.detail
    assert result.surface is not None and result.surface.dialog is None


def test_action_refused_by_an_open_dialog_is_not_ok(tmp_path: Path) -> None:
    result = _decode("click_blocked_by_dialog", tmp_path)

    assert result.ok is False
    assert result.detail == (
        '### Error\nError: Tool "browser_click" does not handle the modal state.\n'
        '### Modal state\n- ["confirm" dialog with message "Delete order?"]: can be handled by browser_handle_dialog'
    )
    assert result.surface is None


def test_action_refused_by_an_open_dialog_shows_it_on_the_page_before(tmp_path: Path) -> None:
    result = _decode("click_blocked_by_dialog", tmp_path, FORM)

    assert result.surface == dataclasses.replace(FORM, dialog="confirm: Delete order?")


def test_tab_list_during_a_dialog_shows_the_page_and_the_dialog(tmp_path: Path) -> None:
    result = _decode("tabs_list_during_dialog", tmp_path, FORM)

    assert result.surface == dataclasses.replace(_one_tab(f"{BASE}/index.html", ""), dialog="confirm: Delete order?")


def test_dialog_message_keeps_quotes_and_line_breaks(tmp_path: Path) -> None:
    text = (
        "### Page\n- Page URL: http://x/\n### Modal state\n"
        '- ["prompt" dialog with message "Say "hi"\nand go"]: can be handled by browser_handle_dialog\n'
        "- [File chooser]: can be handled by browser_file_upload"
    )
    raw = {"content": [{"type": "text", "text": text}], "isError": False}

    result = decode("browser_click", raw, output_dir=tmp_path)

    assert result.surface is not None
    assert result.surface.dialog == 'prompt: Say "hi"\nand go'


def test_inline_snapshot_becomes_the_observation(tmp_path: Path) -> None:
    result = _decode("snapshot_form_page", tmp_path)

    observation = result.observation
    assert observation is not None
    assert observation.native_text == _inline_yaml("snapshot_form_page")
    assert len(observation.elements) == 21
    assert observation.context == _tab(0, "Fixture form page")
    assert observation.surface == FORM == result.surface
    assert observation.generation == ""


def test_linked_snapshot_file_becomes_the_observation(tmp_path: Path) -> None:
    yaml = _inline_yaml("snapshot_form_page")
    (tmp_path / SNAPSHOT_FILE).parent.mkdir()
    (tmp_path / SNAPSHOT_FILE).write_text(yaml, encoding="utf-8")

    result = _decode("click_success", tmp_path, FORM)

    assert result.observation is not None
    assert result.observation.native_text == yaml
    assert [element.ref.ref for element in result.observation.elements][-3:] == ["f1e1", "f1e2", "f1e3"]
    assert result.observation.surface == FORM


def test_missing_snapshot_file_gives_no_observation(tmp_path: Path) -> None:
    assert _decode("click_success", tmp_path).observation is None


def test_empty_snapshot_file_gives_no_observation(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_FILE).parent.mkdir()
    (tmp_path / SNAPSHOT_FILE).write_text("", encoding="utf-8")

    assert _decode("dialog_open_by_click", tmp_path).observation is None


def test_snapshot_link_outside_the_output_dir_is_not_read(tmp_path: Path) -> None:
    output_dir = tmp_path / "server"
    output_dir.mkdir()
    (tmp_path / "outside.yml").write_text(_inline_yaml("snapshot_form_page"), encoding="utf-8")
    raw = _raw("click_success")
    raw["content"][0]["text"] = raw["content"][0]["text"].replace(
        f".playwright-mcp\\page-{TIMESTAMP}.yml", "..\\outside.yml"
    )

    assert decode("browser_click", raw, output_dir=output_dir).observation is None


def test_evaluate_result_is_the_value(tmp_path: Path) -> None:
    result = _decode("evaluate_result", tmp_path)

    assert result.value == {"name": "Ada", "size": "l"}
    assert result.detail is not None
    assert result.detail.startswith("### Ran Playwright code\n")
    assert "### Result" not in result.detail
    assert result.surface is None


def test_screenshot_image_is_the_value(tmp_path: Path) -> None:
    result = _decode("screenshot_element", tmp_path)

    assert isinstance(result.value, Screenshot)
    assert result.value.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert (result.value.width, result.value.height, result.value.scale) == (1264, 38, 1.0)
    assert result.detail is not None
    assert "### Result" not in result.detail


def test_result_without_page_has_no_surface(tmp_path: Path) -> None:
    result = _decode("fill_form", tmp_path, FORM)

    assert (result.surface, result.observation, result.value) == (None, None, None)
    assert result.detail is not None
    assert "setChecked(true)" in result.detail
