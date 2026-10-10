# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Callable, Optional

ToolNameMatcher = Callable[[str], bool]
ToolCallLog = Callable[..., Any]

# Process-wide like the loggers it routes to: agents are built by callers that never see the registrant.
_TOOL_CALL_LOGGERS: dict[ToolNameMatcher, ToolCallLog] = {}


def register_tool_call_logger(matches: ToolNameMatcher, log: ToolCallLog) -> None:
    """Send the tool-call lines of tools whose name ``matches`` to ``log(message, *args)``."""
    _TOOL_CALL_LOGGERS[matches] = log


def tool_call_logger(tool_name: str) -> Optional[ToolCallLog]:
    for matches, log in _TOOL_CALL_LOGGERS.items():
        if matches(tool_name):
            return log
    return None
