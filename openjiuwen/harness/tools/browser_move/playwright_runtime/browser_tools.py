# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser MCP integration helpers for playwright_runtime."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner

from .config import RuntimeServerSettings, process_environ, resolve_playwright_mcp_cwd, runtime_server_child_env

_SUPPORTED_CLIENT_TYPES = {"stdio", "sse", "streamable-http", "streamable_http", "http"}
_LOCAL_SERVER_PROCESS: subprocess.Popen[str] | None = None
_LOCAL_SERVER_URL: str | None = None
_OPENJIUWEN_CLIENTS_PATCHED = False
_client_registry: dict[str, Any] = {}
_browser_runtime_stdout_handle = None
_browser_runtime_stderr_handle = None
_BROWSER_RUNTIME_STDOUT_LOG = "browser_runtime_stdout.log"
_BROWSER_RUNTIME_STDERR_LOG = "browser_runtime_stderr.log"


def get_registered_client(server_id: str) -> Any:
    """Return the MCP client created for *server_id*, or None if not found."""
    return _client_registry.get(server_id)


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


def _runtime_logs_dir(settings: RuntimeServerSettings) -> Path:
    path = settings.log_dir or Path(resolve_playwright_mcp_cwd()).expanduser() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runtime_cwd() -> Path:
    path = Path(resolve_playwright_mcp_cwd()).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _browser_runtime_log_paths(settings: RuntimeServerSettings) -> tuple[Path, Path]:
    logs_dir = _runtime_logs_dir(settings)
    return (
        logs_dir / _BROWSER_RUNTIME_STDOUT_LOG,
        logs_dir / _BROWSER_RUNTIME_STDERR_LOG,
    )


def _close_browser_runtime_log_handles() -> None:
    global _browser_runtime_stdout_handle
    global _browser_runtime_stderr_handle

    for handle_name in ("_browser_runtime_stdout_handle", "_browser_runtime_stderr_handle"):
        handle = globals().get(handle_name)
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:
            pass
        globals()[handle_name] = None


def _server_script() -> Path:
    return Path(__file__).resolve().parent.parent / "playwright_runtime_mcp_server.py"


def _normalize_client_type(client_type: str) -> str:
    value = (client_type or "").strip().lower()
    if value in {"http", "streamable_http"}:
        return "streamable-http"
    return value


def _ensure_openjiuwen_client_patch() -> None:
    global _OPENJIUWEN_CLIENTS_PATCHED
    if _OPENJIUWEN_CLIENTS_PATCHED:
        return

    import openjiuwen.core.runner.resources_manager.tool_manager as tool_mgr

    from ..drivers.playwright_mcp.clients.stdio_client import BrowserMoveStdioClient
    from ..drivers.playwright_mcp.clients.streamable_http_client import BrowserMoveStreamableHttpClient

    tool_mgr.StdioClient = BrowserMoveStdioClient
    original_create_client = tool_mgr.ToolMgr._create_client

    def _patched_create_client(config: McpServerConfig):
        normalized = _normalize_client_type(getattr(config, "client_type", ""))
        if normalized == "streamable-http":
            client = BrowserMoveStreamableHttpClient(config)
        elif normalized == "stdio":
            client = BrowserMoveStdioClient(config)
        else:
            client = original_create_client(config)
        _client_registry[config.server_id] = client
        return client

    tool_mgr.StreamableHttpClient = BrowserMoveStreamableHttpClient
    tool_mgr.ToolMgr._create_client = staticmethod(_patched_create_client)
    _OPENJIUWEN_CLIENTS_PATCHED = True


def ensure_browser_runtime_client_patch() -> None:
    """Ensure MCP client creation is patched for browser runtime transports."""
    _ensure_openjiuwen_client_patch()


def _runtime_path(settings: RuntimeServerSettings, transport: str) -> str:
    default_path = "/mcp" if _normalize_client_type(transport) == "streamable-http" else "/sse"
    path = settings.path or default_path
    if not path.startswith("/"):
        return f"/{path}"
    return path


def _build_server_url(settings: RuntimeServerSettings, transport: str) -> str:
    return f"http://{settings.host}:{settings.port}{_runtime_path(settings, transport)}"


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_available_port(host: str, preferred_port: int, max_attempts: int = 25) -> int:
    if preferred_port > 0 and _is_port_available(host, preferred_port):
        return preferred_port
    for port in range(preferred_port + 1, preferred_port + max_attempts + 1):
        if _is_port_available(host, port):
            return port
    raise RuntimeError("No available port for local browser runtime MCP server.")


def _wait_port_open(host: str, port: int, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.8)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Browser runtime MCP server did not start in time: {host}:{port}")


def _start_local_server(
    transport: str,
    host: str,
    port: int,
    path: str,
    *,
    settings: RuntimeServerSettings,
    environ: Mapping[str, str],
) -> str:
    global _LOCAL_SERVER_PROCESS
    global _LOCAL_SERVER_URL
    global _browser_runtime_stdout_handle
    global _browser_runtime_stderr_handle

    normalized = _normalize_client_type(transport)
    if normalized not in {"sse", "streamable-http"}:
        raise ValueError(f"Unsupported local server transport: {transport}")

    server_script = _server_script()
    if not server_script.exists():
        raise FileNotFoundError(f"playwright runtime MCP server not found: {server_script}")

    cmd = [
        settings.command,
        str(server_script),
        "--transport",
        normalized,
        "--host",
        host,
        "--port",
        str(port),
        "--path",
        path,
        "--no-banner",
    ]
    stdout_log_path, stderr_log_path = _browser_runtime_log_paths(settings)
    _close_browser_runtime_log_handles()
    stdout_handle = stdout_log_path.open("a", encoding="utf-8")
    stderr_handle = stderr_log_path.open("a", encoding="utf-8")
    try:
        _LOCAL_SERVER_PROCESS = subprocess.Popen(
            cmd,
            cwd=str(_runtime_cwd()),
            env=runtime_server_child_env(environ),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    _browser_runtime_stdout_handle = stdout_handle
    _browser_runtime_stderr_handle = stderr_handle
    _wait_port_open(host, port)
    _LOCAL_SERVER_URL = f"http://{host}:{port}{path}"
    return _LOCAL_SERVER_URL


def _parse_local_server_url(server_url: str) -> tuple[str, int, str]:
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port is None:
        raise ValueError(f"Invalid browser runtime MCP URL: {server_url}")
    return parsed.hostname, int(parsed.port), parsed.path or "/mcp"


def stop_local_browser_runtime_server() -> None:
    global _LOCAL_SERVER_PROCESS
    global _LOCAL_SERVER_URL

    proc = _LOCAL_SERVER_PROCESS
    _LOCAL_SERVER_PROCESS = None
    _LOCAL_SERVER_URL = None
    if proc is None or proc.poll() is not None:
        _close_browser_runtime_log_handles()
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    finally:
        _close_browser_runtime_log_handles()


def restart_local_browser_runtime_server(
    settings: RuntimeServerSettings | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    environ = process_environ() if environ is None else environ
    settings = settings or RuntimeServerSettings.from_env(environ)
    transport = _normalize_client_type(settings.client_type)
    current_url = _LOCAL_SERVER_URL
    current_proc = _LOCAL_SERVER_PROCESS

    if transport not in {"sse", "streamable-http"}:
        stop_local_browser_runtime_server()
        return None

    host = settings.host
    path = _runtime_path(settings, transport)
    preferred_port = int(settings.port)
    if current_url:
        host, preferred_port, path = _parse_local_server_url(current_url)

    had_local_server = current_url is not None or (current_proc is not None and current_proc.poll() is None)
    stop_local_browser_runtime_server()
    if not had_local_server:
        return None

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _is_port_available(host, preferred_port):
            return _start_local_server(transport, host, preferred_port, path, settings=settings, environ=environ)
        time.sleep(0.3)
    raise RuntimeError(f"Browser runtime port is still occupied after shutdown: {host}:{preferred_port}")


def _ensure_local_server_started(
    transport: str, settings: RuntimeServerSettings, environ: Mapping[str, str]
) -> str:
    global _LOCAL_SERVER_PROCESS
    global _LOCAL_SERVER_URL

    normalized = _normalize_client_type(transport)
    if normalized not in {"sse", "streamable-http"}:
        raise ValueError(f"Unsupported local server transport: {transport}")
    if _LOCAL_SERVER_PROCESS is not None and _LOCAL_SERVER_PROCESS.poll() is None and _LOCAL_SERVER_URL:
        return _LOCAL_SERVER_URL

    host = settings.host
    preferred_port = int(settings.port)
    path = _runtime_path(settings, normalized)
    port = _pick_available_port(host, preferred_port)
    return _start_local_server(normalized, host, port, path, settings=settings, environ=environ)


def _build_http_fallback_config(
    base_cfg: McpServerConfig, settings: RuntimeServerSettings, server_url: str | None = None
) -> McpServerConfig:
    return McpServerConfig(
        server_id=f"{base_cfg.server_id}_http",
        server_name=base_cfg.server_name,
        server_path=server_url or _build_server_url(settings, "streamable-http"),
        client_type="streamable-http",
    )


def _build_http_retry_config(base_cfg: McpServerConfig, server_url: str) -> McpServerConfig:
    return McpServerConfig(
        server_id=base_cfg.server_id,
        server_name=base_cfg.server_name,
        server_path=server_url,
        client_type="streamable-http",
    )


def _result_is_ok(result: Any) -> bool:
    if result is None:
        return True
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok):
        try:
            return bool(is_ok())
        except Exception:
            return False
    return False


def _result_error_text(result: Any) -> str:
    if result is None:
        return ""
    for attr in ("error", "msg"):
        method = getattr(result, attr, None)
        if callable(method):
            try:
                value = method()
                if value is not None:
                    return str(value)
            except Exception:
                pass
    raw_error = getattr(result, "_error", None)
    if raw_error is not None:
        return str(raw_error)
    return str(result)


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


async def register_browser_runtime_mcp_server(
    agent: Any,
    *,
    tag: str = "agent.main",
    settings: RuntimeServerSettings | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Register browser runtime MCP server on an OpenJiuWen agent with fallbacks."""
    _ensure_openjiuwen_client_patch()
    environ = process_environ() if environ is None else environ
    settings = settings or RuntimeServerSettings.from_env(environ)
    cfg = build_browser_runtime_mcp_config(settings, environ=environ)
    if cfg is None:
        return False

    async def _register_once(target_cfg: McpServerConfig) -> tuple[bool, str]:
        result = await Runner.resource_mgr.add_mcp_server(target_cfg, tag=tag)
        if _result_is_ok(result):
            agent.ability_manager.add(target_cfg)
            return True, ""

        error_text = _result_error_text(result)
        if "already exist" in error_text.lower():
            agent.ability_manager.add(target_cfg)
            return True, error_text
        return False, error_text

    http_err = ""
    if cfg.client_type == "stdio" and settings.auto_http_fallback:
        http_cfg = _build_http_fallback_config(cfg, settings)
        ok, http_err = await _register_once(http_cfg)
        if ok:
            return True
        try:
            auto_url = _ensure_local_server_started("streamable-http", settings, environ)
            http_cfg = _build_http_fallback_config(cfg, settings, server_url=auto_url)
            ok, auto_http_err = await _register_once(http_cfg)
            if ok:
                return True
            http_err = f"{http_err} | {auto_http_err}".strip(" |")
        except Exception as exc:
            http_err = f"{http_err} | {exc}".strip(" |")

    ok, error_text = await _register_once(cfg)
    if ok:
        return True

    normalized_type = _normalize_client_type(cfg.client_type)
    if normalized_type in {"streamable-http", "sse"}:
        try:
            auto_url = _ensure_local_server_started(normalized_type, settings, environ)
            retry_cfg = (
                _build_http_retry_config(cfg, auto_url)
                if normalized_type == "streamable-http"
                else McpServerConfig(
                    server_id=cfg.server_id,
                    server_name=cfg.server_name,
                    server_path=auto_url,
                    client_type="sse",
                )
            )
            ok, retry_err = await _register_once(retry_cfg)
            if ok:
                return True
            error_text = f"{error_text} | {retry_err}".strip(" |")
        except Exception as exc:
            error_text = f"{error_text} | {exc}".strip(" |")

    if http_err:
        error_text = f"{error_text} | http={http_err}".strip(" |")
    raise RuntimeError(f"Failed to register browser runtime MCP server: {error_text}")
