# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Record real @playwright/mcp answers as JSON fixtures; a script run from the repository root, not a test."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.tool.mcp.base import MCPTool
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.clients import BrowserMoveStdioClient
from openjiuwen.harness.tools.browser_move.runtime.config import build_playwright_mcp_config

SERVER = "@playwright/mcp@0.0.78"
HERE = Path(__file__).resolve().parent
PAGES_DIR = HERE / "pages"

_CLEARED_ENV = (
    "BROWSER_DRIVER",
    "PLAYWRIGHT_MCP_COMMAND",
    "PLAYWRIGHT_MCP_EXTENSION",
    "PLAYWRIGHT_MCP_EXTENSION_TOKEN",
    "PLAYWRIGHT_MCP_CDP_ENDPOINT",
    "PLAYWRIGHT_CDP_URL",
    "PLAYWRIGHT_MCP_CDP_HEADERS",
    "PLAYWRIGHT_CDP_HEADERS",
    "PLAYWRIGHT_MCP_CDP_TIMEOUT",
    "PLAYWRIGHT_CDP_TIMEOUT_MS",
    "PLAYWRIGHT_MCP_BROWSER",
    "PLAYWRIGHT_MCP_DEVICE",
    "PLAYWRIGHT_MCP_ENV_JSON",
)

_BASELINE_MCP_CONFIG = {
    "browser": {
        "contextOptions": {"locale": "en-US", "timezoneId": "America/New_York"},
        "launchOptions": {
            "args": [
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ]
        },
    }
}


_HANG_S = 30


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/hang":
            time.sleep(_HANG_S)
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()


def _start_page_server() -> ThreadingHTTPServer:
    handler = functools.partial(_QuietHandler, directory=str(PAGES_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _configure_env(work_dir: Path) -> None:
    """Launch args of the WebVoyager baseline, headless, with work_dir as the server cwd."""
    for key in _CLEARED_ENV:
        os.environ.pop(key, None)
    config_path = work_dir / "playwright-mcp.config.json"
    config_path.write_text(json.dumps(_BASELINE_MCP_CONFIG), encoding="utf-8")
    os.environ["PLAYWRIGHT_MCP_ARGS"] = json.dumps(
        ["-y", SERVER, "--isolated", "--config", str(config_path), "--headless"]
    )
    os.environ["PLAYWRIGHT_RUNTIME_MCP_CWD"] = str(work_dir)


_OUTPUT_FILE_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z")


class _Placeholders:
    def __init__(self, port: int, tmp_dir: Path, outside_dir: Path) -> None:
        variants = {
            (spelling, token)
            for directory, token in ((tmp_dir, "<TMP>"), (outside_dir, "<OUTSIDE>"))
            for path in (str(directory), str(directory.resolve()))
            for spelling in (path, path.replace("\\", "/"), path.replace("\\", "\\\\"))
        }
        self._paths = sorted(variants, key=lambda variant: len(variant[0]), reverse=True)
        self._port = re.compile(rf"(127\.0\.0\.1|localhost):{port}\b")

    def apply(self, value: Any) -> Any:
        if isinstance(value, str):
            for path, token in self._paths:
                value = value.replace(path, token)
            value = _OUTPUT_FILE_TIMESTAMP.sub("<TS>", value)
            return self._port.sub(r"\1:<PORT>", value)
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        if isinstance(value, dict):
            return {key: self.apply(item) for key, item in value.items()}
        return value


class _Recorder:
    def __init__(self, client: BrowserMoveStdioClient, tools: dict[str, MCPTool], placeholders: _Placeholders):
        self._client = client
        self._tools = tools
        self._placeholders = placeholders
        self._raw: list[Any] = []
        self.written: list[str] = []
        original_call_tool = client._session.call_tool

        async def capturing_call_tool(*args: Any, **kwargs: Any) -> Any:
            result = await original_call_tool(*args, **kwargs)
            self._raw.append(result)
            return result

        client._session.call_tool = capturing_call_tool

    async def call(self, tool: str, arguments: dict[str, Any]) -> tuple[Any, str]:
        self._raw.clear()
        mcp_tool = self._tools[tool]
        output = await mcp_tool.invoke(arguments)
        if len(self._raw) != 1:
            raise RuntimeError(f"expected exactly one MCP call for {tool}, captured {len(self._raw)}")
        return self._raw[0], mcp_tool.render_for_llm(output)

    async def record(self, case: str, tool: str, arguments: dict[str, Any]) -> str:
        raw, model_text = await self.call(tool, arguments)
        fixture = {
            "server": SERVER,
            "case": case,
            "request": {"tool": tool, "arguments": arguments},
            "raw_result": raw.model_dump(mode="json", by_alias=True, exclude_none=True),
            "model_text": model_text,
        }
        path = HERE / f"{case}.json"
        text = json.dumps(self._placeholders.apply(fixture), ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        self.written.append(path.name)
        print(f"recorded {path.name}")
        return model_text


def _ref(snapshot_text: str, role: str, name: str | None) -> str:
    named = "" if name is None else f' "{re.escape(name)}"'
    match = re.search(rf"- {re.escape(role)}{named}[^\n]*\[ref=((?:f\d+)?e\d+)\]", snapshot_text)
    if match is None:
        raise RuntimeError(f"no {role}{named} in snapshot:\n{snapshot_text}")
    return match.group(1)


_CLOSE_BROWSER_MID_CALL = (
    "async (page) => { const pending = page.locator('#never').click({ timeout: 3000 }); "
    "await page.context().browser().close(); await pending; }"
)


async def _run_cases(rec: _Recorder, base_url: str, work_dir: Path, outside_dir: Path) -> None:
    index_url = f"{base_url}/index.html"
    await rec.call("browser_navigate", {"url": index_url})

    snapshot = await rec.record(
        "snapshot_form_page",
        "browser_snapshot",
        {},
    )
    await rec.record("snapshot_depth_1", "browser_snapshot", {"depth": 1})
    await rec.record("snapshot_target", "browser_snapshot", {"target": _ref(snapshot, "navigation", None)})
    await rec.record(
        "click_success",
        "browser_click",
        {"element": "Save button", "target": _ref(snapshot, "button", "Save")},
    )
    await rec.record(
        "events_console_message",
        "browser_click",
        {"element": "Log to console button", "target": _ref(snapshot, "button", "Log to console")},
    )
    await rec.record(
        "events_download",
        "browser_click",
        {"element": "Download report link", "target": _ref(snapshot, "link", "Download report")},
    )
    await rec.record(
        "dialog_open_by_click",
        "browser_click",
        {"element": "Delete order button", "target": _ref(snapshot, "button", "Delete order")},
    )
    await rec.record(
        "dialog_handle_accept",
        "browser_handle_dialog",
        {"accept": True},
    )
    await rec.record(
        "navigate_same_url_reload",
        "browser_navigate",
        {"url": index_url},
    )
    await rec.record(
        "navigate_second_page",
        "browser_navigate",
        {"url": f"{base_url}/second.html"},
    )

    await rec.record("tabs_list", "browser_tabs", {"action": "list"})
    await rec.record("tabs_new", "browser_tabs", {"action": "new"})
    await rec.record("tabs_select", "browser_tabs", {"action": "select", "index": 0})
    await rec.record("tabs_close", "browser_tabs", {"action": "close", "index": 1})

    await rec.call("browser_navigate", {"url": f"{base_url}/blocked.html"})
    _, blocked_snapshot = await rec.call("browser_snapshot", {})
    await rec.record(
        "click_intercepted_by_overlay",
        "browser_click",
        {"element": "Covered button", "target": _ref(blocked_snapshot, "button", "Covered button")},
    )
    await rec.record(
        "click_timeout_not_enabled",
        "browser_click",
        {"element": "Disabled button", "target": _ref(blocked_snapshot, "button", "Disabled button")},
    )

    await rec.call("browser_navigate", {"url": f"{base_url}/second.html"})
    await rec.record(
        "click_stale_ref",
        "browser_click",
        {"element": "Covered button", "target": _ref(blocked_snapshot, "button", "Covered button")},
    )

    await rec.call("browser_navigate", {"url": index_url})
    _, form = await rec.call("browser_snapshot", {})
    await rec.record("snapshot_with_boxes", "browser_snapshot", {"boxes": True})
    await rec.record(
        "fill_form",
        "browser_fill_form",
        {
            "fields": [
                {"target": _ref(form, "textbox", "Full name"), "name": "Full name", "type": "textbox", "value": "Ada"},
                {
                    "target": _ref(form, "checkbox", "Gift wrap"),
                    "name": "Gift wrap",
                    "type": "checkbox",
                    "value": "true",
                },
                {"target": _ref(form, "combobox", "Size"), "name": "Size", "type": "combobox", "value": "Medium"},
            ]
        },
    )
    await rec.record("snapshot_after_fill_form", "browser_snapshot", {})
    await rec.record(
        "select_option",
        "browser_select_option",
        {"element": "Size select", "target": _ref(form, "combobox", "Size"), "values": ["Large"]},
    )
    await rec.record(
        "evaluate_result",
        "browser_evaluate",
        {
            "function": "() => ({ name: document.getElementById('name').value, "
            "size: document.getElementById('size').value })"
        },
    )
    await rec.record(
        "evaluate_error",
        "browser_evaluate",
        {"function": "() => { throw new Error('fixture failure'); }"},
    )
    await rec.record(
        "screenshot_element",
        "browser_take_screenshot",
        {"element": "Page heading", "target": _ref(form, "heading", "Fixture form page"), "type": "png"},
    )
    await rec.record("wait_for_timeout", "browser_wait_for", {"text": "Never shown"})
    await rec.call("browser_click", {"element": "Delete order button", "target": _ref(form, "button", "Delete order")})
    await rec.record(
        "click_blocked_by_dialog",
        "browser_click",
        {"element": "Save button", "target": _ref(form, "button", "Save")},
    )
    await rec.call("browser_handle_dialog", {"accept": False})

    await rec.call("browser_navigate", {"url": f"{base_url}/popup.html"})
    _, popup = await rec.call("browser_snapshot", {})
    await rec.record(
        "click_opens_tab",
        "browser_click",
        {"element": "New tab link", "target": _ref(popup, "link", "Open second page in a new tab")},
    )
    await rec.record("snapshot_two_tabs", "browser_snapshot", {})
    await rec.record(
        "click_navigates",
        "browser_click",
        {"element": "Second page link", "target": _ref(popup, "link", "Go to second page")},
    )
    await rec.record("navigate_back", "browser_navigate_back", {})
    await rec.call("browser_tabs", {"action": "close", "index": 1})

    attachment = work_dir / "attachment.csv"
    attachment.write_text("name,size\nAda,Large\n", encoding="utf-8")
    await rec.call("browser_navigate", {"url": f"{base_url}/upload.html"})
    _, upload = await rec.call("browser_snapshot", {})
    await rec.record(
        "file_chooser_open_by_click",
        "browser_click",
        {"element": "Attachment input", "target": _ref(upload, "button", "Attachment")},
    )
    await rec.record("file_upload", "browser_file_upload", {"paths": [str(attachment)]})

    await rec.call("browser_navigate", {"url": f"{base_url}/slow.html"})
    _, slow = await rec.call("browser_snapshot", {})
    await rec.record(
        "click_timeout_after_action",
        "browser_click",
        {"element": "Slow link", "target": _ref(slow, "link", "Slow link")},
    )

    await rec.call("browser_navigate", {"url": index_url})
    _, form = await rec.call("browser_snapshot", {})
    await rec.call("browser_click", {"element": "Delete order button", "target": _ref(form, "button", "Delete order")})
    await rec.record("snapshot_during_dialog", "browser_snapshot", {"boxes": True})
    await rec.record("tabs_list_during_dialog", "browser_tabs", {"action": "list"})
    await rec.call("browser_handle_dialog", {"accept": False})

    outside = outside_dir / "outside.csv"
    outside.write_text("name\nAda\n", encoding="utf-8")
    await rec.call("browser_navigate", {"url": f"{base_url}/upload.html"})
    _, upload = await rec.call("browser_snapshot", {})
    await rec.record(
        "drop_outside_roots",
        "browser_drop",
        {"element": "Page heading", "target": _ref(upload, "heading", "Fixture upload page"), "paths": [str(outside)]},
    )

    await rec.call("browser_run_code_unsafe", {"code": _CLOSE_BROWSER_MID_CALL})
    await rec.record("evaluate_new_tab_failed", "browser_evaluate", {"function": "() => document.title"})
    await rec.record("evaluate_browser_closed", "browser_evaluate", {"function": "() => document.title"})


async def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="mcp-fixtures-"))
    outside_dir = Path(tempfile.mkdtemp(prefix="mcp-outside-"))
    page_server = _start_page_server()
    port = page_server.server_address[1]
    _configure_env(tmp_dir)
    client = BrowserMoveStdioClient(build_playwright_mcp_config())
    try:
        if not await client.connect():
            print("could not start the MCP server", file=sys.stderr)
            return 1
        tools = {card.name: MCPTool(client, card) for card in await client.list_tools()}
        recorder = _Recorder(client, tools, _Placeholders(port, tmp_dir, outside_dir))
        await _run_cases(recorder, f"http://127.0.0.1:{port}", tmp_dir, outside_dir)
        print(f"wrote {len(recorder.written)} fixtures to {HERE}")
        return 0
    finally:
        await client.disconnect()
        page_server.shutdown()
        page_server.server_close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(outside_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
