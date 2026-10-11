# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser surface settings, parsed from the ``BROWSER_*`` and ``PLAYWRIGHT_*`` environment variables."""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from openjiuwen.core.foundation.tool import McpServerConfig

from ...utils.env import (
    env_value,
    first_value,
    int_value,
    is_truthy_env,
    parse_command_args,
)

if TYPE_CHECKING:
    from ...playwright_runtime.config import BrowserInstanceConfig

ENV_PREFIXES = ("BROWSER_", "PLAYWRIGHT_")
PROVISIONINGS = ("remote", "managed", "extension")
DEFAULT_MCP_TIMEOUT_S = 600
DEFAULT_PLAYWRIGHT_MCP_COMMAND = "npx"
DEFAULT_PLAYWRIGHT_MCP_ARGS = "-y @playwright/mcp@0.0.78"
DEFAULT_PROFILE_NAME = "jiuwenclaw"
_MCP_PASSTHROUGH_ENV = ("PLAYWRIGHT_BROWSERS_PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


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
    # Without an explicit choice the extension flag selects the provisioning, so device and MCP config agree.
    implied = "extension" if is_truthy_env(environ.get("PLAYWRIGHT_MCP_EXTENSION", "")) else "remote"
    value = explicit or legacy or implied
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


def _chrome_install_paths(environ: Mapping[str, str]) -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    keys = ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
    roots = tuple(env_value(environ, key) for key in keys)
    return tuple(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe") for root in roots if root)


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
    browser_binary_candidates: tuple[str, ...] = ()
    local_app_data: str = ""
    launch_args: tuple[str, ...] = tuple(parse_command_args(DEFAULT_PLAYWRIGHT_MCP_ARGS))
    mcp_command: str = DEFAULT_PLAYWRIGHT_MCP_COMMAND
    mcp_timeout_s: int = DEFAULT_MCP_TIMEOUT_S
    mcp_cwd: Path = field(default_factory=lambda: Path.cwd().resolve())
    mcp_browser: str = ""
    mcp_device: str = ""
    mcp_extension: bool = False
    mcp_extension_token: str = ""
    mcp_env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    # A server config given by the caller (the WebVoyager runner's): its command line wins over the fields above.
    mcp_server: McpServerConfig | None = None
    upload_root: Path | None = None
    sidecar_python: Path | None = None
    selector_cache_path: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BrowserSettings:
        mcp_cwd = first_value(
            environ,
            "PLAYWRIGHT_RUNTIME_MCP_CWD",
            "BROWSER_RUNTIME_MCP_CWD",
            "PLAYWRIGHT_RUNTIME_WORKDIR",
            "BROWSER_RUNTIME_WORKDIR",
        )
        upload_root = first_value(environ, "BROWSER_UPLOAD_ROOT")
        sidecar_python = environ.get("BROWSER_USE_SIDECAR_PYTHON")
        return cls(
            provisioning=_provisioning(environ),
            cdp_url=first_value(environ, "PLAYWRIGHT_MCP_CDP_ENDPOINT", "PLAYWRIGHT_CDP_URL"),
            cdp_headers=first_value(environ, "PLAYWRIGHT_MCP_CDP_HEADERS", "PLAYWRIGHT_CDP_HEADERS"),
            cdp_timeout=first_value(environ, "PLAYWRIGHT_MCP_CDP_TIMEOUT", "PLAYWRIGHT_CDP_TIMEOUT_MS"),
            managed_host=first_value(environ, "BROWSER_MANAGED_HOST", default="127.0.0.1"),
            managed_port=_managed_port(environ),
            managed_kill_existing=is_truthy_env(environ.get("BROWSER_MANAGED_KILL_EXISTING", "")),
            managed_args=tuple(parse_command_args(environ.get("BROWSER_MANAGED_ARGS") or "")),
            user_data_dir=first_value(environ, "BROWSER_MANAGED_USER_DATA_DIR"),
            profile_name=first_value(environ, "BROWSER_PROFILE_NAME", default=DEFAULT_PROFILE_NAME),
            profile_store_path=_optional_path(first_value(environ, "BROWSER_PROFILE_STORE_PATH")),
            browser_binary=first_value(environ, "BROWSER_MANAGED_BINARY"),
            browser_binary_candidates=_chrome_install_paths(environ),
            local_app_data=environ.get("LOCALAPPDATA") or "",
            launch_args=tuple(parse_command_args(environ.get("PLAYWRIGHT_MCP_ARGS", DEFAULT_PLAYWRIGHT_MCP_ARGS))),
            mcp_command=first_value(environ, "PLAYWRIGHT_MCP_COMMAND", default=DEFAULT_PLAYWRIGHT_MCP_COMMAND),
            mcp_timeout_s=int_value(
                environ, "PLAYWRIGHT_MCP_TIMEOUT_S", "BROWSER_TIMEOUT_S", default=DEFAULT_MCP_TIMEOUT_S, minimum=1
            ),
            mcp_cwd=Path(mcp_cwd or Path.cwd()).expanduser().resolve(),
            mcp_browser=first_value(environ, "PLAYWRIGHT_MCP_BROWSER"),
            mcp_device=first_value(environ, "PLAYWRIGHT_MCP_DEVICE"),
            mcp_extension=is_truthy_env(environ.get("PLAYWRIGHT_MCP_EXTENSION", "")),
            mcp_extension_token=first_value(environ, "PLAYWRIGHT_MCP_EXTENSION_TOKEN"),
            mcp_env=_mcp_env(environ),
            upload_root=Path(upload_root).expanduser().resolve() if upload_root else None,
            sidecar_python=Path(sidecar_python) if sidecar_python else None,
            selector_cache_path=_optional_path((environ.get("OPENJIUWEN_BROWSER_SELECTOR_CACHE") or "").strip()),
        )

    @classmethod
    def from_process_env(cls) -> BrowserSettings:
        return cls.from_env(os.environ)

    def with_instance(self, instance: BrowserInstanceConfig) -> BrowserSettings:
        """These settings for one isolated browser: the instance's non-empty fields win."""
        provisioning = instance.provisioning.strip().lower() or self.provisioning
        if provisioning not in PROVISIONINGS:
            raise ValueError(
                f"BrowserInstanceConfig.provisioning must be one of: {', '.join(PROVISIONINGS)} (got {provisioning!r})"
            )
        if instance.managed_port > 0:
            managed_port = instance.managed_port
        elif instance.key:
            # 0 = a free port: one shared port cannot serve several keys without them landing on one browser.
            managed_port = 0
        else:
            managed_port = self.managed_port
        return replace(
            self,
            provisioning=provisioning,
            cdp_url=instance.cdp_url.strip() or self.cdp_url,
            managed_port=managed_port,
            user_data_dir=instance.user_data_dir.strip() or self.user_data_dir,
            profile_name=(instance.profile_name or instance.sanitized_key()).strip() or self.profile_name,
            browser_binary=instance.browser_binary.strip() or self.browser_binary,
            launch_args=tuple(parse_command_args(instance.launch_args))
            if instance.launch_args.strip()
            else self.launch_args,
        )
