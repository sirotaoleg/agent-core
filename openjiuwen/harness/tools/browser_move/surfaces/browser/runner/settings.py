# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser runner's settings: the instance identity, the runtime settings and the facades over the process env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.contract import SurfaceBundle, SurfaceSettings
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings, RunGuardrails
from openjiuwen.harness.tools.browser_move.runtime.utils.env import SUPPORTED_MODEL_PROVIDERS, parse_command_args
from openjiuwen.harness.tools.browser_move.surfaces import SURFACES


@dataclass(frozen=True)
class BrowserInstanceConfig:
    """Per-agent device identity; agents with the same ``key`` share one device, an empty key shares the default."""

    key: str = ""
    driver_mode: str = ""
    managed_port: int = 0
    user_data_dir: str = ""
    profile_name: str = ""
    cdp_url: str = ""
    browser_binary: str = ""
    launch_args: str = ""
    provisioning: str = ""
    driver: str = ""

    def __post_init__(self) -> None:
        if self.provisioning and self.driver_mode and self.provisioning != self.driver_mode:
            raise ValueError(
                f"BrowserInstanceConfig: provisioning={self.provisioning!r} conflicts with "
                f"driver_mode={self.driver_mode!r} (driver_mode is an alias of provisioning)"
            )
        provisioning = self.provisioning or self.driver_mode
        object.__setattr__(self, "provisioning", provisioning)
        object.__setattr__(self, "driver_mode", provisioning)

    def sanitized_key(self) -> str:
        """Return the key reduced to id-safe characters (``[A-Za-z0-9_-]``)."""
        return re.sub(r"[^A-Za-z0-9_-]+", "-", (self.key or "").strip()).strip("-")


@dataclass(frozen=True)
class RuntimeSettings:
    provider: str
    api_key: str
    api_base: str
    model_name: str
    mcp_cfg: McpServerConfig
    guardrails: RunGuardrails
    instance: Optional[BrowserInstanceConfig] = None


def read_demo_inputs(environ: Mapping[str, str]) -> tuple[str, str]:
    """``(query, session_id)`` of the demo CLI in ``main.py``."""
    query = (environ.get("AGENT_QUERY") or "").strip()
    session_id = (environ.get("AGENT_SESSION_ID") or "").strip() or "demo-browser-session"
    return query, session_id


def browser_surface(environ: Mapping[str, str] | None = None, settings: Any = None) -> SurfaceBundle:
    return SURFACES.create("browser", os.environ if environ is None else environ, None, settings)


def browser_surface_settings(environ: Mapping[str, str] | None = None) -> SurfaceSettings:
    return browser_surface(environ).settings


def resolve_playwright_mcp_cwd() -> str:
    """Resolve MCP working directory with relocatable defaults."""
    return str(browser_surface_settings().mcp_cwd)


def build_browser_guardrails() -> RunGuardrails:
    return CoreSettings.from_process_env().guardrails


def build_playwright_mcp_config(
    instance: Optional[BrowserInstanceConfig] = None,
    required_capability: Optional[list[str]] = None,
) -> McpServerConfig:
    return playwright_mcp_config(browser_surface_settings(), instance, required_capability)


def playwright_mcp_config(
    settings: Any,
    instance: Optional[BrowserInstanceConfig] = None,
    required_capability: Optional[list[str]] = None,
) -> McpServerConfig:
    """The ``@playwright/mcp`` server config from the surface settings; ``instance.cdp_url`` wins over them."""
    from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.config import (
        playwright_mcp_config as server_config,
    )

    if instance is None:
        return server_config(settings, required_capability=required_capability or ())
    return server_config(
        settings,
        cdp_url=instance.cdp_url.strip(),
        launch_args=parse_command_args(instance.launch_args) if instance.launch_args.strip() else None,
        key=instance.sanitized_key(),
        required_capability=required_capability or (),
    )


def build_runtime_settings(
    instance: Optional[BrowserInstanceConfig] = None,
    *,
    core_settings: CoreSettings | None = None,
    browser_settings: SurfaceSettings | None = None,
) -> RuntimeSettings:
    core = core_settings or CoreSettings.from_process_env()
    if core.provider not in SUPPORTED_MODEL_PROVIDERS:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER '{core.provider}'. Supported: openai, openrouter, siliconflow, dashscope."
        )
    return RuntimeSettings(
        provider=core.provider,
        api_key=core.api_key,
        api_base=core.api_base,
        model_name=core.model_name,
        mcp_cfg=playwright_mcp_config(browser_settings or browser_surface_settings(), instance),
        guardrails=core.guardrails,
        instance=instance,
    )
