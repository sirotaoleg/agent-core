#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No driver names and no Playwright MCP tool names in the core; ``surfaces/browser/`` may name tools."""

from __future__ import annotations

import re
from collections import Counter

from . import _scan

DRIVER_NAME_RE = re.compile(r"playwright_mcp|browser_use(?![a-z])|browser-use|\bmcp\b", re.IGNORECASE)

MCP_TOOL_NAMES = frozenset(
    {
        "browser_annotate",
        "browser_click",
        "browser_close",
        "browser_console_messages",
        "browser_cookie_clear",
        "browser_cookie_delete",
        "browser_cookie_get",
        "browser_cookie_list",
        "browser_cookie_set",
        "browser_drag",
        "browser_drop",
        "browser_evaluate",
        "browser_file_upload",
        "browser_fill_form",
        "browser_find",
        "browser_generate_locator",
        "browser_get_config",
        "browser_handle_dialog",
        "browser_hide_highlight",
        "browser_highlight",
        "browser_hover",
        "browser_localstorage_clear",
        "browser_localstorage_delete",
        "browser_localstorage_get",
        "browser_localstorage_list",
        "browser_localstorage_set",
        "browser_mouse_click_xy",
        "browser_mouse_down",
        "browser_mouse_drag_xy",
        "browser_mouse_move_xy",
        "browser_mouse_up",
        "browser_mouse_wheel",
        "browser_navigate",
        "browser_navigate_back",
        "browser_network_request",
        "browser_network_requests",
        "browser_network_state_set",
        "browser_pdf_save",
        "browser_press_key",
        "browser_resize",
        "browser_resume",
        "browser_route",
        "browser_route_list",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_select_option",
        "browser_sessionstorage_clear",
        "browser_sessionstorage_delete",
        "browser_sessionstorage_get",
        "browser_sessionstorage_list",
        "browser_sessionstorage_set",
        "browser_set_storage_state",
        "browser_snapshot",
        "browser_start_tracing",
        "browser_start_video",
        "browser_stop_tracing",
        "browser_stop_video",
        "browser_storage_state",
        "browser_tabs",
        "browser_take_screenshot",
        "browser_type",
        "browser_unroute",
        "browser_verify_element_visible",
        "browser_verify_list_visible",
        "browser_verify_text_visible",
        "browser_verify_value",
        "browser_video_chapter",
        "browser_video_hide_actions",
        "browser_video_show_actions",
        "browser_wait_for",
    }
)
MCP_TOOL_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(sorted(MCP_TOOL_NAMES, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
)

KNOWN_DRIVER_NAMES: dict[str, int] = {
    "runtime/browser_tools.py": 3,
    "runtime/service.py": 1,
    "surfaces/browser/capabilities.py": 1,
    "surfaces/browser/helpers/batch_steps.py": 1,
}

KNOWN_MCP_TOOL_NAMES: dict[str, int] = {}


def _in_driver_name_scope(relpath: str) -> bool:
    return (_scan.is_core(relpath) or _scan.is_surface(relpath)) and not _scan.is_config_module(relpath)


def _in_mcp_name_scope(relpath: str) -> bool:
    return _scan.is_core(relpath) or (_scan.is_surface(relpath) and not _scan.in_folder(relpath, "surfaces/browser"))


def _count(pattern: re.Pattern, predicate) -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(predicate):
        hits = len(pattern.findall(_scan.read_text(relpath)))
        if hits:
            found[relpath] = hits
    return found


def _collect_driver_names() -> Counter:
    return _count(DRIVER_NAME_RE, _in_driver_name_scope)


def _collect_mcp_tool_names() -> Counter:
    return _count(MCP_TOOL_NAME_RE, _in_mcp_name_scope)


class TestDriverNames:
    def test_mcp_tool_name_list_is_complete(self):
        assert len(MCP_TOOL_NAMES) == 70

    def test_no_driver_names_in_core_or_surfaces(self):
        problems = _scan.compare_with_allowlist(_collect_driver_names(), KNOWN_DRIVER_NAMES, "driver name")
        assert not problems, "\n".join(problems)

    def test_no_mcp_tool_names_in_core(self):
        problems = _scan.compare_with_allowlist(_collect_mcp_tool_names(), KNOWN_MCP_TOOL_NAMES, "MCP tool name")
        assert not problems, "\n".join(problems)
