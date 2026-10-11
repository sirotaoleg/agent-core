# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The recorded @playwright/mcp fixtures are complete and carry the markers the MCP driver parses."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent
SERVER = "@playwright/mcp@0.0.78"
KEYS = {"server", "case", "request", "raw_result", "model_text"}
CASES = {
    "snapshot_form_page",
    "snapshot_depth_1",
    "snapshot_target",
    "click_success",
    "click_intercepted_by_overlay",
    "navigate_second_page",
    "tabs_list",
    "tabs_new",
    "tabs_select",
    "tabs_close",
    "events_console_message",
    "events_download",
    "dialog_open_by_click",
    "dialog_handle_accept",
    "navigate_same_url_reload",
    "click_timeout_not_enabled",
    "click_stale_ref",
    "snapshot_with_boxes",
    "fill_form",
    "snapshot_after_fill_form",
    "select_option",
    "evaluate_result",
    "evaluate_error",
    "screenshot_element",
    "wait_for_timeout",
    "click_blocked_by_dialog",
    "click_opens_tab",
    "snapshot_two_tabs",
    "click_navigates",
    "navigate_back",
    "file_chooser_open_by_click",
    "file_upload",
    "click_timeout_after_action",
    "snapshot_during_dialog",
    "tabs_list_during_dialog",
    "drop_outside_roots",
    "evaluate_new_tab_failed",
    "evaluate_browser_closed",
}


def _load(case: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8"))


def _raw_text(fixture: dict[str, Any]) -> str:
    return "\n".join(block.get("text", "") for block in fixture["raw_result"]["content"])


def test_every_case_is_recorded() -> None:
    assert {path.stem for path in FIXTURES_DIR.glob("*.json")} == CASES


@pytest.mark.parametrize("case", sorted(CASES))
def test_fixture_is_wellformed(case: str) -> None:
    fixture = _load(case)

    assert set(fixture) == KEYS
    assert fixture["server"] == SERVER
    assert fixture["case"] == case
    assert set(fixture["request"]) == {"tool", "arguments"}
    assert fixture["request"]["tool"].startswith("browser_")
    assert isinstance(fixture["request"]["arguments"], dict)
    assert fixture["raw_result"]["content"], "the server returned no content blocks"
    assert isinstance(fixture["raw_result"]["isError"], bool)
    assert isinstance(fixture["model_text"], str) and fixture["model_text"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_run_specific_values_are_placeholders(case: str) -> None:
    text = (FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8")

    assert not re.search(r"(127\.0\.0\.1|localhost):\d", text), "local port not replaced by <PORT>"
    assert "mcp-fixtures-" not in text, "temp path not replaced by <TMP>"
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z", text), "output-file time not replaced by <TS>"


def test_intercepted_click_carries_the_retry_log() -> None:
    fixture = _load("click_intercepted_by_overlay")

    assert fixture["raw_result"]["isError"] is True
    assert "intercepts pointer events" in _raw_text(fixture)
    assert "intercepts pointer events" in fixture["model_text"]


@pytest.mark.parametrize("case", ["events_console_message", "events_download"])
def test_events_section_is_present(case: str) -> None:
    fixture = _load(case)

    assert "### Events" in _raw_text(fixture)
    assert "### Events" in fixture["model_text"]
