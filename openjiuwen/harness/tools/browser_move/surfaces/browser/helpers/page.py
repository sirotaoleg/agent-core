# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Page metadata from the last browser context plus one page script, and fronting a tab before a policy probes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import tool_logger
from openjiuwen.harness.tools.browser_move.contract import ContextRef, DriverError, Observe, RunScript, SwitchContext
from openjiuwen.harness.tools.browser_move.runtime.utils.parsing import extract_json_object
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.probes import build_browser_state_metadata_js

if TYPE_CHECKING:
    from openjiuwen.harness.tools.browser_move.runtime.tool_factory import ToolContext

_PAGE_METADATA = re.compile(r"const pageMetadata = await page\.evaluate\((?P<function>\(\) => \{.*?\n  \})\);\n", re.S)


def _metadata_function() -> str:
    match = _PAGE_METADATA.search(build_browser_state_metadata_js())
    if match is None:
        raise ValueError("the page metadata script has no page function")
    return match.group("function")


_METADATA_FUNCTION = _metadata_function()


async def page_metadata(context: ToolContext) -> tuple[dict[str, Any], str | None]:
    """URL, title, tabs, page position and semantic state, with the capture error if any."""
    page_state = context.page_state
    action = RunScript(code=_METADATA_FUNCTION)
    try:
        result = await context.execute(action)
    except DriverError as error:
        tool_logger.warning("page metadata capture failed: %s", error)
        return {}, f"browser state metadata capture failed: {error}"
    measured = extract_json_object(result.value)
    if not result.ok or not measured:
        return {}, "Could not parse browser state metadata result JSON"
    surface = page_state.surface_context
    metadata = {
        "ok": True,
        "url": page_state.url,
        "title": page_state.title,
        "tabs": _tabs(surface, page_state.current_context) if isinstance(surface, BrowserContext) else [],
        "page_position": measured.get("page_position") or {},
        "semantic_state": measured.get("semantic_state") or {},
    }
    return metadata, None


async def activate_page(context: ToolContext, url: str) -> bool:
    """Focus the tab at ``url``, else the current one, so its timers run and its menus render."""
    page_state = context.page_state
    try:
        if page_state.current_context is None:
            await context.observe(Observe())
        surface = page_state.surface_context
        by_address = dict(zip(surface.tab_urls, surface.tabs)) if isinstance(surface, BrowserContext) else {}
        wanted = by_address.get(str(url or "")) or page_state.current_context
        if wanted is None:
            return False
        action = SwitchContext(wanted)
        result = await context.execute(action)
    except DriverError as error:
        tool_logger.warning("activate_page failed: %s", error)
        return False
    return result.ok


def _tabs(surface: BrowserContext, current: ContextRef | None) -> list[dict[str, Any]]:
    focused = surface.current_tab
    if focused is None and current in surface.tabs:
        focused = surface.tabs.index(current)
    urls = surface.tab_urls
    return [
        {
            "index": index,
            "current": index == focused,
            "url": urls[index] if index < len(urls) else surface.url if index == focused else "",
            "title": tab.title or "",
        }
        for index, tab in enumerate(surface.tabs)
    ]
