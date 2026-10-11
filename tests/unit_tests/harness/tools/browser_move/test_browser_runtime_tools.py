#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserCancelTool,
    BrowserClearCancelTool,
    BrowserCustomActionTool,
    BrowserListActionsTool,
    BrowserRuntimeHealthTool,
)


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test",
        server_name="test",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": "."},
    )
    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )


def test_cancel_tool_calls_cancel_run() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.cancel_run = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": None, "error": None})
    tool = BrowserCancelTool(runtime)
    result = _run(tool.invoke({"session_id": "s1"}))
    runtime.ensure_runtime_ready.assert_called_once()
    runtime.cancel_run.assert_called_once_with(session_id="s1", request_id=None)
    assert result.success is True


def test_clear_cancel_tool_calls_runtime_clear_cancel() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.clear_cancel = AsyncMock(return_value={"ok": True, "session_id": "s1", "request_id": "r1", "error": None})
    tool = BrowserClearCancelTool(runtime)
    result = _run(tool.invoke({"session_id": "s1", "request_id": "r1"}))
    runtime.ensure_runtime_ready.assert_called_once()
    runtime.clear_cancel.assert_called_once_with(session_id="s1", request_id="r1")
    assert result.success is True


def test_list_actions_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.list_actions = AsyncMock(return_value={"ok": True, "actions": ["echo"], "details": {"echo": {}}})
    tool = BrowserListActionsTool(runtime)
    result = _run(tool.invoke({}))
    runtime.list_actions.assert_called_once()
    assert result.success is True
    assert result.data["actions"] == ["echo"]


def test_custom_action_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.run_custom_action = AsyncMock(return_value={"ok": True, "session_id": "s1"})
    tool = BrowserCustomActionTool(runtime)
    result = _run(
        tool.invoke(
            {
                "action": "echo",
                "session_id": "s1",
                "request_id": "r1",
                "params": {"text": "hello"},
            }
        )
    )
    runtime.run_custom_action.assert_called_once_with(
        action="echo",
        session_id="s1",
        request_id="r1",
        params={"text": "hello"},
    )
    assert result.success is True


def test_runtime_health_tool_uses_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.runtime_health = AsyncMock(
        return_value={
            "ok": False,
            "started": False,
            "detail": "not started",
            "provider": "openai",
            "api_base": "https://example.invalid/v1",
            "model_name": "test-model",
        }
    )
    tool = BrowserRuntimeHealthTool(runtime)
    result = _run(tool.invoke({}))
    runtime.runtime_health.assert_called_once()
    assert result.success is True
    assert result.data["started"] is False
