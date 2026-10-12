# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The worker gets the factory tools of the capabilities its allowlist covers, plus the probes and the batch."""

from __future__ import annotations

import asyncio

from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import (
    CORE_BROWSER_TOOL_NAMES,
    PDF_BROWSER_TOOL_NAMES,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import build_browser_worker_system_prompt
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import MCP_PREFIX, make_runtime

WORKER_HELPERS = {"browser_probe_interactives", "browser_probe_cards", "browser_batch_interact"}
SCREENSHOT = MCP_PREFIX + "browser_take_screenshot"


def _worker_tool_names(allowed_tool_names: tuple[str, ...] | None) -> set[str]:
    runtime = make_runtime(
        allowed_tool_names=allowed_tool_names, image_support=None, core_settings=CoreSettings(driver="playwright_mcp")
    )
    asyncio.run(runtime.ensure_started())
    worker = runtime.service.browser_agent
    return {tool.name for tool in asyncio.run(worker.ability_manager.list_tool_info())}


def test_worker_installs_task_allowlist_for_playwright_server() -> None:
    names = _worker_tool_names(CORE_BROWSER_TOOL_NAMES + PDF_BROWSER_TOOL_NAMES)

    expected = {MCP_PREFIX + name for name in CORE_BROWSER_TOOL_NAMES + PDF_BROWSER_TOOL_NAMES} - {SCREENSHOT}
    assert names == expected | WORKER_HELPERS


def test_worker_with_none_allowlist_defaults_to_core() -> None:
    names = _worker_tool_names(None)

    assert names == {MCP_PREFIX + name for name in CORE_BROWSER_TOOL_NAMES} - {SCREENSHOT} | WORKER_HELPERS
    assert "browser_recall_offload" not in names


def test_worker_prompt_only_mentions_conditionally_visible_run_code() -> None:
    prompt = build_browser_worker_system_prompt("screenshots", "artifacts", driver_name="Playwright MCP")

    assert "browser_run_code_unsafe" not in prompt
    assert "only when it is present" in prompt
    assert "do not create screenshots through run-code" in prompt
