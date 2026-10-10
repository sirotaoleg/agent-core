# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Decode a @playwright/mcp tool result into an ``ActionResult`` or a typed driver error."""

from __future__ import annotations

import base64
import json
import re
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    ConnectionLost,
    ContextRef,
    DriverError,
    Observation,
    Screenshot,
    StaleRef,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import (
    PageHeader,
    parse_elements,
    parse_header,
    parse_tabs,
    snapshot_yaml,
    split_sections,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

_NAVIGATING_TOOLS = frozenset({"browser_navigate", "browser_navigate_back", "browser_tabs"})
# Results go to value or surface, snapshots to observation; events are not reported.
_DROPPED_SECTIONS = frozenset({"Result", "Snapshot", "Events"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FILE_LINK = re.compile(r"^- \[[^\]]*\]\((?P<path>[^)]+)\)$", re.MULTILINE)
_DIALOG = re.compile(r"dialog with message")
_MODAL_REFUSAL = re.compile(r'Tool "[\w-]+" does not handle the modal state')
_STALE_REF = re.compile(r"Ref \S+ not found in the current page snapshot")
# After the browser dies the server first fails to open a new tab, then reports the closed target.
_CLOSED = re.compile(r"Target page, context or browser has been closed|Failed to open a new tab")
_TIMEOUT = re.compile(r"TimeoutError:|Timeout \d+ms exceeded")
# Playwright folds repeated call-log lines into "N × line".
_LOG_PREFIX = re.compile(r"^\s*(?:\d+ × )?(?:- )?")
_PERFORMED = re.compile(r"^performing \w+ action$|^\w+ action done$")
_INTERCEPTED = re.compile(r"^(?P<by><.+?)(?: from (?P<within><.+?) subtree)? intercepts pointer events$")
_ACTIONABILITY_WAIT = re.compile(
    r"waiting for (?:locator|getBy\w+)\(.*"
    r"|locator resolved to .*"
    r"|attempting \w+ action"
    r"|retrying \w+ action"
    r"|waiting \d+ms"
    r"|waiting for element to be .*"
    r"|element is .*"
    r"|element was detached from the DOM, retrying"
    r"|scrolling into view if needed"
    r"|done scrolling"
)
_START_TAG = re.compile(r"<([a-zA-Z][\w-]*)([^>]*)>([^<]*)")
_HTML_ATTRIBUTE = re.compile(r'([\w:-]+)="([^"]*)"')


def decode(
    tool_name: str,
    raw_result: Mapping[str, Any],
    *,
    output_dir: Path,
    before: BrowserContext | None = None,
) -> ActionResult:
    """``raw_result`` is the ``CallToolResult`` as JSON; ``before`` is the context before the call."""
    blocks = raw_result.get("content") or ()
    text = result_text(raw_result)
    parts = split_sections(text)
    if raw_result.get("isError") or "Error" in parts:
        if _MODAL_REFUSAL.search(parts.get("Error", "")):
            return ActionResult(ok=False, detail=_detail(parts))
        raise _error(_ANSI.sub("", parts.get("Error") or text))

    tab_list = parse_tabs(parts.get("Result", "")) if tool_name == "browser_tabs" else None
    header = tab_list or parse_header(parts)
    surface = None if header is None else BrowserContext(url=header.url, title=header.title, tabs=header.tabs)
    image = next((block for block in blocks if block.get("type") == "image"), None)
    if image is not None:
        value: Any = _screenshot(str(image["data"]))
    elif tab_list is None and "Result" in parts:
        value = _result_value(parts["Result"])
    else:
        value = None

    opened: tuple[ContextRef, ...] = ()
    changed = tool_name in _NAVIGATING_TOOLS or bool(_DIALOG.search(parts.get("Modal state", "")))
    if before is not None and header is not None:
        opened = header.tabs[len(before.tabs) :]
        changed = changed or header.url != before.url or len(header.tabs) != len(before.tabs)
    return ActionResult(
        ok=True,
        value=value,
        detail=_detail(parts),
        context_changed=changed,
        observation=_observation(parts.get("Snapshot"), header, surface, output_dir),
        opened_contexts=opened,
        surface=surface,
    )


def result_text(raw_result: Mapping[str, Any]) -> str:
    """The text blocks of a result, joined: what the model reads today."""
    blocks = raw_result.get("content") or ()
    return "\n".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")


def _detail(parts: dict[str, str]) -> str | None:
    kept = [f"### {name}\n{body}" if name else body for name, body in parts.items() if name not in _DROPPED_SECTIONS]
    return "\n".join(kept) or None


def _result_value(body: str) -> Any:
    if body == "undefined":
        return None
    try:
        return json.loads(body)
    except ValueError:
        return body


def _screenshot(data: str) -> Screenshot:
    png = base64.b64decode(data)
    width, height = struct.unpack(">II", png[16:24]) if png[:8] == _PNG_SIGNATURE and len(png) >= 24 else (0, 0)
    return Screenshot(png=png, width=width, height=height)


def _observation(
    section: str | None, header: PageHeader | None, surface: BrowserContext | None, output_dir: Path
) -> Observation | None:
    if section is None or header is None:
        return None
    yaml = snapshot_yaml(section)
    if yaml is None:
        yaml = _read_linked_snapshot(section, output_dir)
    if not yaml:
        return None
    return Observation(context=header.focused, elements=parse_elements(yaml), native_text=yaml, surface=surface)


def _read_linked_snapshot(section: str, output_dir: Path) -> str | None:
    # The server writes an empty file while a modal state blocks the snapshot; that reads as None too.
    link = _FILE_LINK.search(section)
    if link is None:
        return None
    root = output_dir.resolve()
    path = (root / link.group("path").replace("\\", "/")).resolve()
    if not path.is_relative_to(root):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _error(message: str) -> DriverError:
    if _STALE_REF.search(message):
        return StaleRef(message, dispatched=False)
    if _CLOSED.search(message):
        return ConnectionLost(message, dispatched=True)
    head, _, call_log = message.partition("Call log:")
    lines = [_LOG_PREFIX.sub("", line).strip() for line in call_log.splitlines() if line.strip()]
    timeout = bool(_TIMEOUT.search(head))
    if any(_PERFORMED.match(line) for line in lines):
        return ActionTimeout(message, dispatched=True) if timeout else DriverError(message, dispatched=True)
    blockers = [match for match in map(_INTERCEPTED.match, lines) if match is not None]
    if blockers:
        return ActionIntercepted(message, by=_describe_blocker(blockers[-1]), dispatched=False)
    if timeout:
        only_waits = bool(lines) and all(_ACTIONABILITY_WAIT.fullmatch(line) for line in lines)
        return ActionTimeout(message, dispatched=not only_waits)
    return DriverError(message, dispatched=True)


def _describe_blocker(match: re.Match[str]) -> str:
    by = _describe_element(match.group("by"))
    within = match.group("within")
    return by if within is None else f"{by} in {_describe_element(within)}"


def _describe_element(html: str) -> str:
    """``tag#id.class "text"``, the form the browser-use driver reports in ``ActionIntercepted.by``."""
    match = _START_TAG.match(html)
    if match is None:
        return html
    tag, raw_attributes, text = match.groups()
    attributes = dict(_HTML_ATTRIBUTE.findall(raw_attributes))
    classes = attributes.get("class", "").split()[:2]
    described = tag.lower() + (f"#{attributes['id']}" if attributes.get("id") else "")
    described += "".join(f".{name}" for name in classes)
    text = " ".join(text.split())[:40]
    return f'{described} "{text}"' if text else described
