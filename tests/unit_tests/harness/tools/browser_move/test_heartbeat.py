#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access
"""The browser-move stdio client patch (kept until the client moves into the driver)."""

from __future__ import annotations

from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_tools import ensure_browser_runtime_client_patch
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.clients.stdio_client import BrowserMoveStdioClient
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr



def test_browser_runtime_stdio_patch_creates_pingable_client() -> None:
    ensure_browser_runtime_client_patch()
    config = McpServerConfig(
        server_id="test-stdio-patch",
        server_name="test-stdio-patch",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": "."},
    )

    client = ToolMgr._create_client(config)

    assert isinstance(client, BrowserMoveStdioClient)
    assert hasattr(client, "ping")
