# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from openjiuwen.core.foundation.tool import McpServerConfig

SERVER_CAPABILITIES = ("pdf", "vision", "devtools", "config", "network", "storage", "testing")


def _ensure_capabilities(args: list[str], required_capabilities: Iterable[str]) -> list[str]:
    normalized_args: list[str] = []
    capabilities: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--caps":
            if index + 1 < len(args) and not args[index + 1].startswith("--"):
                capabilities.extend(args[index + 1].split(","))
                index += 2
                continue
        elif arg.startswith("--caps="):
            capabilities.extend(arg.removeprefix("--caps=").split(","))
            index += 1
            continue
        normalized_args.append(arg)
        index += 1

    capabilities = list(dict.fromkeys(capability.strip() for capability in capabilities if capability.strip()))
    for capability in required_capabilities:
        if capability not in capabilities:
            capabilities.append(capability)
    normalized_args.append(f"--caps={','.join(capabilities)}")
    return normalized_args


def playwright_mcp_config(
    settings: Any,
    *,
    cdp_url: str = "",
    launch_args: Sequence[str] | None = None,
    key: str = "",
    required_capability: Iterable[str] = (),
) -> McpServerConfig:
    """The ``@playwright/mcp`` server config from the browser settings; ``cdp_url`` wins over theirs."""
    args = _ensure_capabilities(
        list(settings.launch_args if launch_args is None else launch_args),
        (*SERVER_CAPABILITIES, *required_capability),
    )
    extension_mode = settings.provisioning == "extension" or settings.mcp_extension

    env_map: dict[str, str] = dict(settings.mcp_env)
    if extension_mode:
        env_map["PLAYWRIGHT_MCP_EXTENSION"] = "true"
        if settings.mcp_extension_token:
            env_map["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = settings.mcp_extension_token
        if "--extension" not in args:
            args.append("--extension")
    else:
        cdp_endpoint = cdp_url or settings.cdp_url
        if cdp_endpoint:
            if settings.mcp_device:
                raise ValueError("PLAYWRIGHT_MCP_DEVICE is not supported with CDP endpoint mode.")
            env_map["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = cdp_endpoint
            if not settings.mcp_browser:
                env_map["PLAYWRIGHT_MCP_BROWSER"] = "chrome"
        if settings.cdp_headers:
            env_map["PLAYWRIGHT_MCP_CDP_HEADERS"] = settings.cdp_headers
        if settings.cdp_timeout:
            env_map["PLAYWRIGHT_MCP_CDP_TIMEOUT"] = settings.cdp_timeout

    params: dict[str, Any] = {
        "command": settings.mcp_command,
        "args": args,
        "cwd": str(settings.mcp_cwd),
    }
    if settings.mcp_timeout_s > 0:
        params["timeout_s"] = settings.mcp_timeout_s
    if env_map:
        params["env"] = env_map

    server_id = "playwright_official_stdio"
    server_name = "playwright-official"
    if key:
        server_id = f"{server_id}__{key}"
        server_name = f"{server_name}-{key}"

    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path="stdio://playwright",
        client_type="stdio",
        params=params,
    )


__all__ = ["SERVER_CAPABILITIES", "playwright_mcp_config"]
