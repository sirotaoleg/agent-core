# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What the model reads for a browser observation, result or error."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    CloseContext,
    ContentBlock,
    DriverError,
    Observation,
    Observe,
    RunScript,
    Screenshot,
    StaleRef,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import OpenContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

_ERROR = "### Error"
_TAB_COMMANDS = (OpenContext, SwitchContext, CloseContext)


class BrowserRenderer:
    """The snapshot text verbatim; results as the server's sections, with script values and tab lists rebuilt."""

    def observation(self, observation: Observation) -> tuple[ContentBlock, ...]:
        return _blocks(observation.native_text, observation.screenshot)

    def result(self, action: Action, result: ActionResult) -> tuple[ContentBlock, ...]:
        image = result.value if isinstance(result.value, Screenshot) else None
        detail = result.detail or ""
        if not result.ok:
            return _blocks(detail if detail.startswith(_ERROR) else f"{_ERROR}\n{detail}", image)
        if isinstance(action, RunScript):
            return _blocks(_join(f"### Result\n{_script_value(result.value)}", detail), image)
        listing = isinstance(action, Observe) and not action.include_screenshot
        if listing or (isinstance(action, _TAB_COMMANDS) and not detail):
            tabs = _tab_list(result.surface or (result.observation.surface if result.observation else None))
            return _blocks(_join(tabs and f"### Result\n{tabs}", detail), image)
        return _blocks(detail, image)

    def error(self, action: Action, error: DriverError, observation: Observation | None) -> tuple[ContentBlock, ...]:
        if observation is None and isinstance(error, StaleRef):
            observation = error.observation
        blocks = (f"{_ERROR}\n{error}",)
        if observation is None:
            return blocks
        seen = self.observation(observation)
        if seen and isinstance(seen[0], str):
            return (f"{blocks[0]}\n\n{seen[0]}",) + seen[1:]
        return blocks + seen


def _blocks(text: str | None, image: Screenshot | None) -> tuple[ContentBlock, ...]:
    return tuple(block for block in (text, image) if block)


def _join(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def _script_value(value: Any) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _tab_list(surface: Any) -> str:
    if not isinstance(surface, BrowserContext):
        return ""
    lines = []
    for index, tab in enumerate(surface.tabs):
        current = index == surface.current_tab
        url = surface.tab_urls[index] if index < len(surface.tab_urls) else surface.url if current else ""
        link = f"[{tab.title or ''}]" + (f"({url})" if url else "")
        lines.append(f"- {index}: " + ("(current) " if current else "") + link)
    return "\n".join(lines)
