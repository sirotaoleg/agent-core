# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compact tool errors for actions another element intercepted."""

from __future__ import annotations

import re

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ..contract import ActionIntercepted, DriverRef, Locator, PointRef
from .tool_factory import CommandRecord, command_record

_CONSENT = re.compile(
    r"onetrust|cookie|consent|cmp|didomi|usercentrics|cookiebot|gdpr|truste|qc-cmp|sp_message",
    re.IGNORECASE,
)
_MAX_LOCATOR_CHARS = 60

_CONSENT_HINT = (
    "This is a cookie-consent dialog: close it first (prefer 'Reject all' / 'Close'; "
    "if its settings panel is open, confirm or close that panel)."
)
_GENERIC_HINT = (
    "If it is a dialog, banner or overlay, dismiss or close it first. If it is part of the same control "
    "(an icon or custom styling over an input), click the control's visible label or text instead."
)


def _target(record: CommandRecord) -> str:
    target = getattr(record.action, "target", None)
    if isinstance(target, DriverRef):
        return target.ref
    if isinstance(target, Locator):
        return (target.css or target.text or "the target")[:_MAX_LOCATOR_CHARS]
    if isinstance(target, PointRef):
        return f"({target.x}, {target.y})"
    return "the target"


def explain_blocked_command(record: CommandRecord, retry: str) -> str | None:
    """Compact error for a command another element intercepted, from its record; else None."""
    if record.error != ActionIntercepted.__name__ or not record.blocked_by:
        return None
    hint = _CONSENT_HINT if _CONSENT.search(record.blocked_by) else _GENERIC_HINT
    return (
        "### Error\n"
        f"{record.command.__name__} not delivered: {_target(record)} is covered by {record.blocked_by}.\n"
        f"{hint}\n"
        f"{retry}"
    )


class InterceptedActionHintRail(AgentRail):
    """Rewrite the model-facing message of an intercepted action; ``retry`` is the surface's retry sentence."""

    def __init__(self, retry: str) -> None:
        super().__init__()
        self._retry = retry

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        tool_msg = getattr(inputs, "tool_msg", None)
        record = command_record(getattr(inputs, "tool_result", None))
        readable = isinstance(getattr(tool_msg, "content", None), str)
        explained = explain_blocked_command(record, self._retry) if record is not None and readable else None
        if explained is not None:
            inputs.tool_msg = tool_msg.model_copy(update={"content": explained})
