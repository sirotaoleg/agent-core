# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser surface settings, parsed from the ``BROWSER_*`` and ``PLAYWRIGHT_*`` environment variables."""

from __future__ import annotations

import json
import os
import shlex
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

ENV_PREFIXES = ("BROWSER_", "PLAYWRIGHT_")
PROVISIONINGS = ("remote", "managed", "extension")
DEFAULT_PLAYWRIGHT_MCP_COMMAND = "npx"
DEFAULT_PLAYWRIGHT_MCP_ARGS = "-y @playwright/mcp@0.0.78"
DEFAULT_MCP_TIMEOUT_S = 600
DEFAULT_PROFILE_NAME = "jiuwenclaw"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_MCP_PASSTHROUGH_ENV = ("PLAYWRIGHT_BROWSERS_PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


def _first(environ: Mapping[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        value = (environ.get(key) or "").strip()
        if value:
            return value
    return default


def _int(environ: Mapping[str, str], *keys: str, default: int, minimum: int) -> int:
    for key in keys:
        try:
            value = int((environ.get(key) or "").strip())
        except ValueError:
            continue
        if value >= minimum:
            return value
    return default


def _split_args(value: str) -> tuple[str, ...]:
    value = value.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
    return tuple(shlex.split(value))


def _provisioning(environ: Mapping[str, str]) -> str:
    explicit = (environ.get("BROWSER_PROVISIONING") or "").strip().lower()
    legacy = (environ.get("BROWSER_DRIVER") or "").strip().lower()
    if legacy in PROVISIONINGS:
        warnings.warn(
            f"BROWSER_DRIVER={legacy} is deprecated: set BROWSER_PROVISIONING={legacy}. "
            "BROWSER_DRIVER now selects the driver (playwright_mcp or browser_use).",
            DeprecationWarning,
            stacklevel=3,
        )
    else:
        legacy = ""
    value = explicit or legacy or "remote"
    if value not in PROVISIONINGS:
        raise ValueError(f"BROWSER_PROVISIONING must be one of: {', '.join(PROVISIONINGS)} (got {explicit!r})")
    return value


def _managed_port(environ: Mapping[str, str]) -> int:
    raw = (environ.get("BROWSER_MANAGED_PORT") or "9333").strip()
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if port <= 0:
        raise ValueError(f"Invalid BROWSER_MANAGED_PORT: {raw}")
    return port


def _mcp_env(environ: Mapping[str, str]) -> Mapping[str, str]:
    env = {key: environ[key] for key in _MCP_PASSTHROUGH_ENV if environ.get(key)}
    extra_json = (environ.get("PLAYWRIGHT_MCP_ENV_JSON") or "").strip()
    if extra_json:
        try:
            extra = json.loads(extra_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid PLAYWRIGHT_MCP_ENV_JSON: {exc}") from exc
        if isinstance(extra, dict):
            env.update((str(k), str(v)) for k, v in extra.items())
    return MappingProxyType(env)


def _optional_path(raw: str) -> Path | None:
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class BrowserSettings:
    """Where the browser comes from and how Playwright MCP and browser-use reach it."""

    provisioning: str = "remote"
    cdp_url: str = ""
    cdp_headers: str = ""
    cdp_timeout: str = ""
    managed_host: str = "127.0.0.1"
    managed_port: int = 9333
    managed_kill_existing: bool = False
    managed_args: tuple[str, ...] = ()
    user_data_dir: str = ""
    profile_name: str = DEFAULT_PROFILE_NAME
    profile_store_path: Path | None = None
    browser_binary: str = ""
    launch_args: tuple[str, ...] = _split_args(DEFAULT_PLAYWRIGHT_MCP_ARGS)
    mcp_command: str = DEFAULT_PLAYWRIGHT_MCP_COMMAND
    mcp_timeout_s: int = DEFAULT_MCP_TIMEOUT_S
    mcp_cwd: Path = field(default_factory=lambda: Path.cwd().resolve())
    mcp_browser: str = ""
    mcp_device: str = ""
    mcp_extension: bool = False
    mcp_extension_token: str = ""
    mcp_env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    upload_root: Path | None = None
    sidecar_python: Path | None = None
    selector_cache_path: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BrowserSettings:
        mcp_cwd = _first(
            environ,
            "PLAYWRIGHT_RUNTIME_MCP_CWD",
            "BROWSER_RUNTIME_MCP_CWD",
            "PLAYWRIGHT_RUNTIME_WORKDIR",
            "BROWSER_RUNTIME_WORKDIR",
        )
        upload_root = _first(environ, "BROWSER_UPLOAD_ROOT")
        sidecar_python = environ.get("BROWSER_USE_SIDECAR_PYTHON")
        return cls(
            provisioning=_provisioning(environ),
            cdp_url=_first(environ, "PLAYWRIGHT_MCP_CDP_ENDPOINT", "PLAYWRIGHT_CDP_URL"),
            cdp_headers=_first(environ, "PLAYWRIGHT_MCP_CDP_HEADERS", "PLAYWRIGHT_CDP_HEADERS"),
            cdp_timeout=_first(environ, "PLAYWRIGHT_MCP_CDP_TIMEOUT", "PLAYWRIGHT_CDP_TIMEOUT_MS"),
            managed_host=_first(environ, "BROWSER_MANAGED_HOST", default="127.0.0.1"),
            managed_port=_managed_port(environ),
            managed_kill_existing=(environ.get("BROWSER_MANAGED_KILL_EXISTING") or "").strip().lower() in _TRUTHY,
            managed_args=_split_args(environ.get("BROWSER_MANAGED_ARGS") or ""),
            user_data_dir=_first(environ, "BROWSER_MANAGED_USER_DATA_DIR"),
            profile_name=_first(environ, "BROWSER_PROFILE_NAME", default=DEFAULT_PROFILE_NAME),
            profile_store_path=_optional_path(_first(environ, "BROWSER_PROFILE_STORE_PATH")),
            browser_binary=_first(environ, "BROWSER_MANAGED_BINARY"),
            launch_args=_split_args(environ.get("PLAYWRIGHT_MCP_ARGS", DEFAULT_PLAYWRIGHT_MCP_ARGS)),
            mcp_command=_first(environ, "PLAYWRIGHT_MCP_COMMAND", default=DEFAULT_PLAYWRIGHT_MCP_COMMAND),
            mcp_timeout_s=_int(
                environ, "PLAYWRIGHT_MCP_TIMEOUT_S", "BROWSER_TIMEOUT_S", default=DEFAULT_MCP_TIMEOUT_S, minimum=1
            ),
            mcp_cwd=Path(mcp_cwd or Path.cwd()).expanduser().resolve(),
            mcp_browser=_first(environ, "PLAYWRIGHT_MCP_BROWSER"),
            mcp_device=_first(environ, "PLAYWRIGHT_MCP_DEVICE"),
            mcp_extension=(environ.get("PLAYWRIGHT_MCP_EXTENSION") or "").strip().lower() in _TRUTHY,
            mcp_extension_token=_first(environ, "PLAYWRIGHT_MCP_EXTENSION_TOKEN"),
            mcp_env=_mcp_env(environ),
            upload_root=Path(upload_root).expanduser().resolve() if upload_root else None,
            sidecar_python=Path(sidecar_python) if sidecar_python else None,
            selector_cache_path=_optional_path((environ.get("OPENJIUWEN_BROWSER_SELECTOR_CACHE") or "").strip()),
        )

    @classmethod
    def from_process_env(cls) -> BrowserSettings:
        return cls.from_env(os.environ)
