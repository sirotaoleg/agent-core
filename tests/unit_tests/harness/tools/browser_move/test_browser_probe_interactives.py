#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
    builtin_site_profiles,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserProbeInteractivesTool,
)


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright-runtime",
        server_name="test-playwright-runtime",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )

    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(
            max_steps=3,
            max_failures=1,
            timeout_s=30,
            retry_once=False,
        ),
    )


def test_build_interactive_probe_js_contains_high_value_selectors() -> None:
    js = build_interactive_probe_js(
        max_items=25,
        viewport_only=True,
        site_profiles=builtin_site_profiles(),
    )

    assert "button" in js
    assert "a[href]" in js
    assert "input" in js
    assert "[aria-label]" in js
    assert "[data-testid]" in js
    assert "max_items" in js
    assert "viewport_only" in js
    assert "validateSelectorHint" in js
    assert "matches.length === 1 && matches[0] === el" in js
    assert "selector_hint_validated" in js
    assert "actionable" in js
    assert "clickable" in js
    assert "match_count" in js
    assert "visible" in js
    assert "enabled" in js
    assert "generation_id" in js
    assert "matchCount === 1" in js
    assert "document.elementFromPoint" in js
    assert "calendar_date" in js
    assert "sort_tab" in js
    assert "rating_filter" in js
    assert "selectionSourceFromUrl" in js
    assert "url-param" in js
    assert "hotel_destination" in js
    assert "hotel_checkin" in js
    assert "hotel_checkout" in js
    assert "hotel_search_submit" in js
    assert "hotel_filter" in js
    assert "hotel_search" in js
    assert "global_search" in js
    assert "region: classifyRegion" in js
    assert "kind: kind || actionLikelihood" in js
    assert "'[role=\"gridcell\"]'" in js
    assert "'[role=\"tab\"]'" in js


def test_build_interactive_probe_js_embeds_generation_id() -> None:
    js = build_interactive_probe_js(generation_id="g7")

    assert '"generation_id": "g7"' in js
    assert "selector_hint: clickable ? selectorHint : ''" in js
    assert "selector_hint_validated: clickable" in js


def test_build_interactive_probe_js_expands_search_and_input_queries() -> None:
    js = build_interactive_probe_js(max_items=25, viewport_only=True, query="search")

    assert "queryAliases" in js
    assert "queryMatches" in js
    assert "action_likelihood" in js
    assert "className" in js
    assert "input_type" in js
    assert "搜索" in js
    assert "关键词" in js
    assert 'role="searchbox"' in js
    assert "[placeholder]" in js


def test_build_interactive_probe_js_clamps_max_items() -> None:
    js = build_interactive_probe_js(max_items=999, viewport_only=True)

    assert '"max_items": 100' in js


def test_browser_probe_interactives_tool_invokes_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.probe_interactives = AsyncMock(
        return_value={
            "ok": True,
            "elements": [
                {
                    "id": "e1",
                    "role": "button",
                    "text": "Add to cart",
                    "selector_hint": "button:nth-of-type(1)",
                }
            ],
            "error": None,
        }
    )

    tool = BrowserProbeInteractivesTool(runtime, language="en")

    result = _run(
        tool.invoke(
            {
                "max_items": 200,
                "viewport_only": "false",
                "query": "cart",
            }
        )
    )

    runtime.probe_interactives.assert_called_once_with(
        max_items=40,
        viewport_only=False,
        query="cart",
    )
    assert result.success is True
    assert result.data["elements"][0]["text"] == "Add to cart"


def test_browser_probe_interactives_tool_reports_runtime_error() -> None:
    runtime = _make_runtime()
    runtime.probe_interactives = AsyncMock(
        return_value={
            "ok": False,
            "error": "browser_code_executor_not_ready",
            "elements": [],
        }
    )

    tool = BrowserProbeInteractivesTool(runtime, language="en")

    result = _run(tool.invoke({}))

    assert result.success is False
    assert result.error == "browser_code_executor_not_ready"
    assert result.data["elements"] == []


def test_probe_query_uses_exact_match_before_bounded_alias_widening() -> None:
    script = build_interactive_probe_js(query="搜索清华大学")

    assert "exactQueryMatches" in script
    assert "widenedCandidates" in script
    assert "query_widened" in script
    assert "raw.includes(term)" in script
