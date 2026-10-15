# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent Tool wrappers around BrowserAgentRuntime."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime

_ctx_parent_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_session_id",
    default="",
)
_ctx_parent_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_parent_request_id",
    default="",
)

_CUSTOM_ACTION_DESC = (
    "Run a registered custom browser action by name. "
    "Use for deterministic helpers such as drag-and-drop or coordinate resolution "
    "alongside the direct browser tools. "
    "Call browser_list_custom_actions first to discover available actions and parameters. "
    "Aliases source/target and source_x/source_y/target_x/target_y are accepted."
)
_CUSTOM_ACTION_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "description": "Name of the custom action to run"},
        "session_id": {"type": "string", "description": "Session ID (optional)"},
        "request_id": {"type": "string", "description": "Request ID (optional)"},
        "params": {
            "type": "object",
            "description": "Extra key-value parameters forwarded to the action",
            "properties": {},
            "required": [],
        },
    },
    "required": ["action"],
}

_LIST_ACTIONS_DESC = "List available custom browser actions and detailed parameter guidance for browser_custom_action."
_LIST_ACTIONS_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


class BrowserCustomActionTool(Tool):
    """Run a registered custom action."""

    def __init__(self, runtime: "BrowserAgentRuntime") -> None:
        super().__init__(
            ToolCard(
                name="browser_custom_action",
                description=_CUSTOM_ACTION_DESC,
                input_params=_CUSTOM_ACTION_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        action = inputs.get("action", "")
        session_id = (inputs.get("session_id") or "").strip() or _ctx_parent_session_id.get()
        request_id = (inputs.get("request_id") or "").strip() or _ctx_parent_request_id.get()
        params: Dict[str, Any] = inputs.get("params") or {}
        try:
            result = await self._runtime.run_custom_action(
                action=action,
                session_id=session_id,
                request_id=request_id,
                params=params,
            )
            return ToolOutput(
                success=bool(result.get("ok", True)),
                data=result,
                error=result.get("error"),
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None


class BrowserListActionsTool(Tool):
    """List the registered custom actions."""

    def __init__(self, runtime: "BrowserAgentRuntime") -> None:
        super().__init__(
            ToolCard(
                name="browser_list_custom_actions",
                description=_LIST_ACTIONS_DESC,
                input_params=_LIST_ACTIONS_PARAMS,
            )
        )
        self._runtime = runtime

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        del inputs, kwargs
        try:
            data = await self._runtime.list_actions()
            return ToolOutput(success=True, data=data)
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        if False:
            yield None
