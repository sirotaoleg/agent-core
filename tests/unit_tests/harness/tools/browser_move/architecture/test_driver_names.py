#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No driver names and no Playwright MCP tool names in the core (plan section 3, invariant 3).

Driver names: ``playwright_mcp``, ``browser_use`` and ``browser-use`` anywhere in the
text (case-insensitive), and ``mcp`` as a whole word (regex ``\\b``, so ``mcp_cfg``
or ``McpClient`` do not count). Scope: core folders and ``surfaces/``, except the
settings parsing modules (``*/config.py``). Code, strings and comments all count.

MCP tool names: the 70 tools of ``@playwright/mcp@0.0.78`` (69 in the capability
catalog plus ``browser_wait_for``) as whole identifiers. Scope: core folders and
``surfaces/`` except ``surfaces/browser/``, where model-facing ``browser_*`` names
are profile data.

Core folders are ``runtime/`` plus today's ``playwright_runtime/``,
``controllers/``, ``utils/`` and ``clients/`` until Phase 8/9 moves them (see
``_scan.py``). Counts are per file: a file whose count grows, or a new file, fails.
"""

from __future__ import annotations

import re
from collections import Counter

from . import _scan

DRIVER_NAME_RE = re.compile(r"playwright_mcp|browser_use|browser-use|\bmcp\b", re.IGNORECASE)

# Copied from .hq/mapping/command-mapping.md section 1 (P0-T6): 69 catalog names + browser_wait_for.
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

# file relative to the package -> number of driver-name matches.
KNOWN_DRIVER_NAMES: dict[str, int] = {
    # all removed in Phase 7
    "clients/logging_utils.py": 2,
    "clients/stdio_client.py": 5,
    "clients/streamable_http_client.py": 5,
    "controllers/action.py": 3,
    "playwright_runtime/__init__.py": 1,
    "playwright_runtime/agents.py": 4,
    "playwright_runtime/browser_capabilities.py": 2,
    "playwright_runtime/browser_tools.py": 23,
    "playwright_runtime/browser_working_context.py": 2,
    "playwright_runtime/browser_working_context_rail.py": 2,
    "playwright_runtime/main.py": 3,
    "playwright_runtime/repeated_call_guard.py": 1,
    "playwright_runtime/runtime.py": 20,
    "playwright_runtime/runtime_tools.py": 3,
    "playwright_runtime/service.py": 16,
    "playwright_runtime/service_registry.py": 2,
    "playwright_runtime/status_logging.py": 1,
    "utils/__init__.py": 4,
    "utils/env.py": 3,
    "utils/parsing.py": 1,
}

# file relative to the package -> number of MCP tool-name matches.
KNOWN_MCP_TOOL_NAMES: dict[str, int] = {
    # all removed in Phase 7
    "controllers/action.py": 14,
    "playwright_runtime/agents.py": 11,
    "playwright_runtime/browser_capabilities.py": 69,
    "playwright_runtime/browser_state_context_processor.py": 46,
    "playwright_runtime/intercepted_action.py": 1,
    "playwright_runtime/probes.py": 2,
    "playwright_runtime/repeated_call_guard.py": 3,
    "playwright_runtime/runtime.py": 57,
    "playwright_runtime/runtime_tools.py": 2,
    "utils/upload_paths.py": 1,
}


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
