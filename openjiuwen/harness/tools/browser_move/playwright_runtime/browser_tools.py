# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser MCP integration helpers for playwright_runtime."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, Mapping

from openjiuwen.core.foundation.tool import McpServerConfig

from .config import (
    RuntimeServerSettings,
    process_environ,
    resolve_playwright_mcp_cwd,
    runtime_server_child_env,
)

_SUPPORTED_CLIENT_TYPES = {"stdio", "sse", "streamable-http", "streamable_http", "http"}


def _parse_args(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass
    try:
        return shlex.split(text, posix=(os.name != "nt"))
    except Exception:
        return text.split()


def _runtime_cwd() -> Path:
    path = Path(resolve_playwright_mcp_cwd()).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _server_script() -> Path:
    return Path(__file__).resolve().parent.parent / "playwright_runtime_mcp_server.py"


def _normalize_client_type(client_type: str) -> str:
    value = (client_type or "").strip().lower()
    if value in {"http", "streamable_http"}:
        return "streamable-http"
    return value


def _runtime_path(settings: RuntimeServerSettings, transport: str) -> str:
    default_path = "/mcp" if _normalize_client_type(transport) == "streamable-http" else "/sse"
    path = settings.path or default_path
    if not path.startswith("/"):
        return f"/{path}"
    return path


def _build_server_url(settings: RuntimeServerSettings, transport: str) -> str:
    return f"http://{settings.host}:{settings.port}{_runtime_path(settings, transport)}"


def build_browser_runtime_mcp_config(
    settings: RuntimeServerSettings | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> McpServerConfig | None:
    """Build browser runtime MCP config for OpenJiuWen agent registration."""
    environ = process_environ() if environ is None else environ
    settings = settings or RuntimeServerSettings.from_env(environ)
    if not settings.enabled:
        return None

    client_type = _normalize_client_type(settings.client_type)
    if client_type not in _SUPPORTED_CLIENT_TYPES:
        raise ValueError("PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE must be stdio, sse, or streamable-http.")

    if client_type in {"sse", "streamable-http"}:
        return McpServerConfig(
            server_id=settings.server_id,
            server_name=settings.server_name,
            server_path=settings.server_path or _build_server_url(settings, client_type),
            client_type=client_type,
        )

    server_script = _server_script()
    if not server_script.exists():
        raise FileNotFoundError(f"playwright runtime MCP server not found: {server_script}")

    args = (
        _parse_args(settings.args)
        if settings.args
        else [
            "-m",
            "openjiuwen.harness.tools.browser_move.playwright_runtime_mcp_server",
            "--transport",
            "stdio",
            "--no-banner",
            "--log-level",
            "ERROR",
        ]
    )

    params: dict[str, Any] = {
        "command": settings.command,
        "args": args,
        "cwd": str(_runtime_cwd()),
    }
    if settings.timeout_s > 0:
        params["timeout_s"] = settings.timeout_s

    child_env = runtime_server_child_env(environ)
    if child_env:
        params["env"] = child_env

    return McpServerConfig(
        server_id=settings.server_id,
        server_name=settings.server_name,
        server_path=settings.server_path or "stdio://playwright-runtime-wrapper",
        client_type="stdio",
        params=params,
    )
