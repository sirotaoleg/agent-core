# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compact tool errors for Playwright actions blocked by an element on top of the target."""

from __future__ import annotations

import json
import re

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ..contract import ActionIntercepted, DriverRef, Locator, PointRef
from .tool_factory import CommandRecord, command_record

INTERCEPTS_POINTER_EVENTS = "intercepts pointer events"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_LOCATOR = re.compile(r"waiting for (locator\(.*\))\s*$")
_RESOLVED_TARGET = re.compile(r"locator resolved to (<.*)$")
_ACTION = re.compile(r"attempting (\w+) action")
_BLOCKER = re.compile(r"-\s*(<.+?)(?:\s+from\s+(<.+?)\s+subtree)?\s+intercepts pointer events")
_START_TAG = re.compile(r"<([a-zA-Z][\w-]*)([^>]*)>")
_ATTRIBUTE = re.compile(r'([\w:-]+)="([^"]*)"')
_CONSENT = re.compile(
    r"onetrust|cookie|consent|cmp|didomi|usercentrics|cookiebot|gdpr|truste|qc-cmp|sp_message",
    re.IGNORECASE,
)
_DESCRIPTOR_ATTRIBUTES = ("name", "type", "aria-label", "value", "data-testid")
_MAX_DESCRIPTOR_CHARS = 80
_MAX_LOCATOR_CHARS = 60

_CONSENT_HINT = (
    "This is a cookie-consent dialog: close it first (prefer 'Reject all' / 'Close'; "
    "if its settings panel is open, confirm or close that panel)."
)
_GENERIC_HINT = (
    "If it is a dialog, banner or overlay, dismiss or close it first. If it is part of the same control "
    "(an icon or custom styling over an input), click the control's visible label or text instead."
)
_RETRY = "Then retry the action (take a fresh browser_snapshot if the page changed)."


def _describe_element(html: str) -> str:
    """Compact CSS-like descriptor of the first start tag in Playwright's element preview."""
    match = _START_TAG.search(html)
    if match is None:
        return html.strip()[:_MAX_DESCRIPTOR_CHARS]
    tag, raw_attributes = match.groups()
    attributes = dict(_ATTRIBUTE.findall(raw_attributes))
    descriptor = tag.lower()
    if attributes.get("id"):
        descriptor += f"#{attributes['id']}"
    classes = attributes.get("class", "").split()
    if classes:
        descriptor += f".{classes[0]}"
    for name in _DESCRIPTOR_ATTRIBUTES:
        if attributes.get(name):
            descriptor += f'[{name}="{attributes[name]}"]'
    return descriptor[:_MAX_DESCRIPTOR_CHARS]


def _last_match(pattern: re.Pattern[str], lines: list[str]) -> re.Match[str] | None:
    for line in reversed(lines):
        match = pattern.search(line)
        if match is not None:
            return match
    return None


def _explain_call_log(text: str) -> str | None:
    lines = [line.strip() for line in _ANSI_ESCAPE.sub("", text).splitlines()]
    blocker = _last_match(_BLOCKER, lines)
    if blocker is None:
        return None
    blocker_html, container_html = blocker.groups()
    covering = _describe_element(blocker_html)
    if container_html:
        covering += f" (inside {_describe_element(container_html)})"

    resolved = _last_match(_RESOLVED_TARGET, lines)
    locator = _last_match(_LOCATOR, lines)
    if resolved is not None:
        target = _describe_element(resolved.group(1))
    elif locator is not None:
        target = locator.group(1)[:_MAX_LOCATOR_CHARS]
    else:
        target = "the target"
    action = _last_match(_ACTION, lines)
    verb = action.group(1).capitalize() if action is not None else "Action"

    hint = _CONSENT_HINT if _CONSENT.search(f"{blocker_html} {container_html or ''}") else _GENERIC_HINT
    return f"### Error\n{verb} not delivered: {target} is covered by {covering}.\n{hint}\n{_RETRY}"


def explain_intercepted_action(content: str) -> str | None:
    """Compact error for an action blocked by another element (keeps a ``{"result": ...}`` envelope), else None."""
    if INTERCEPTS_POINTER_EVENTS not in content:
        return None
    try:
        envelope = json.loads(content)
    except ValueError:
        envelope = None
    if isinstance(envelope, dict) and list(envelope) == ["result"] and isinstance(envelope["result"], str):
        explained = _explain_call_log(envelope["result"])
        return None if explained is None else json.dumps({"result": explained}, ensure_ascii=False)
    return _explain_call_log(content)


class InterceptedActionHintRail(AgentRail):
    """Rewrite the model-facing message of an intercepted action."""

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        explained = explain_intercepted_action(content) if isinstance(content, str) else None
        if explained is not None:
            inputs.tool_msg = tool_msg.model_copy(update={"content": explained})


def _target(record: CommandRecord) -> str:
    target = getattr(record.action, "target", None)
    if isinstance(target, DriverRef):
        return target.ref
    if isinstance(target, Locator):
        return (target.css or target.text or "the target")[:_MAX_LOCATOR_CHARS]
    if isinstance(target, PointRef):
        return f"({target.x}, {target.y})"
    return "the target"


def explain_blocked_command(record: CommandRecord) -> str | None:
    """Compact error for a command another element intercepted, from its record; else None."""
    if record.error != ActionIntercepted.__name__ or not record.blocked_by:
        return None
    hint = _CONSENT_HINT if _CONSENT.search(record.blocked_by) else _GENERIC_HINT
    return (
        "### Error\n"
        f"{record.command.__name__} not delivered: {_target(record)} is covered by {record.blocked_by}.\n"
        f"{hint}\n"
        f"{_RETRY}"
    )


class CommandInterceptedHintRail(AgentRail):
    """``InterceptedActionHintRail`` on command records: the blocker comes from ``CommandRecord.blocked_by``."""

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        tool_msg = getattr(inputs, "tool_msg", None)
        record = command_record(getattr(inputs, "tool_result", None))
        readable = isinstance(getattr(tool_msg, "content", None), str)
        explained = explain_blocked_command(record) if record is not None and readable else None
        if explained is not None:
            inputs.tool_msg = tool_msg.model_copy(update={"content": explained})
