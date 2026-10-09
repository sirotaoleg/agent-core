# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime configuration helpers."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from openjiuwen.core.foundation.tool import McpServerConfig

from ..surfaces.browser.config import BrowserSettings
from ..utils.env import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_GUARDRAIL_MAX_FAILURES,
    DEFAULT_GUARDRAIL_MAX_STEPS,
    DEFAULT_GUARDRAIL_RETRY_ONCE,
    DEFAULT_MODEL_NAME,
    MISSING_API_KEY_MESSAGE,  # noqa: F401  (re-exported for main.py / mcp_server)
    SUPPORTED_MODEL_PROVIDERS,
    load_repo_dotenv,  # noqa: F401  (re-exported for main.py / mcp_server)
    parse_command_args,
)
from .browser_capabilities import (
    CORE_BROWSER_CAPABILITY_NAME,
    DEFAULT_BROWSER_CAPABILITIES,
    POLICY_ONLY_BROWSER_CAPABILITY_NAMES,
)

_playwright_mcp_capability_names: list[str] = []
for capability_spec in DEFAULT_BROWSER_CAPABILITIES:
    if capability_spec.name == CORE_BROWSER_CAPABILITY_NAME:
        continue
    if capability_spec.name in POLICY_ONLY_BROWSER_CAPABILITY_NAMES:
        continue
    _playwright_mcp_capability_names.append(capability_spec.name)
PLAYWRIGHT_MCP_CAPABILITY_NAMES = tuple(_playwright_mcp_capability_names)


@dataclass
class BrowserRunGuardrails:
    max_steps: int = DEFAULT_GUARDRAIL_MAX_STEPS
    max_failures: int = DEFAULT_GUARDRAIL_MAX_FAILURES
    timeout_s: int = DEFAULT_BROWSER_TIMEOUT_S
    retry_once: bool = True
    resume_on_max_iterations: bool = False


@dataclass(frozen=True)
class BrowserInstanceConfig:
    """Per-instance browser identity, used to isolate one browser per agent.

    All fields default to empty/0, which reproduces the legacy process-global
    (env-driven) behavior. When ``key`` is non-empty the browser is isolated:
    the Playwright MCP ``server_id`` is suffixed with the key and the managed
    profile / port / user-data-dir are derived from it instead of shared env.
    Agents sharing the same ``key`` intentionally share one browser.
    """

    key: str = ""
    driver_mode: str = ""  # alias of provisioning
    managed_port: int = 0  # 0 -> auto-allocate (keyed) or env/default (legacy)
    user_data_dir: str = ""  # "" -> derived from key under mcp_cwd/.browser-profiles
    profile_name: str = ""  # "" -> key, then env BROWSER_PROFILE_NAME
    cdp_url: str = ""  # remote mode: explicit CDP endpoint
    browser_binary: str = ""  # optional Chrome path override
    launch_args: str = ""  # Playwright MCP launch args in the PLAYWRIGHT_MCP_ARGS format; "" -> env/default
    provisioning: str = ""  # "", "managed", "remote", "extension"; "" -> env/default
    driver: str = ""  # "", "playwright_mcp", "browser_use"; "" -> env/default

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
    guardrails: BrowserRunGuardrails
    instance: Optional[BrowserInstanceConfig] = None


DRIVERS = ("playwright_mcp", "browser_use")
DEFAULT_TOOL_TIMEOUT_S = 240.0
DEFAULT_WORKER_TEMPERATURE = 0.2
DEFAULT_WORKER_TOP_P = 0.1
DEFAULT_LOG_FILE = Path("logs") / "browser_agent.log"
_LEGACY_DRIVER_VALUES = frozenset({"managed", "remote", "extension"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})
_LOG_FLAG_OFF = _FALSY | {""}
_LOG_FILE_OFF = _FALSY | {"none", "null", "-"}
_PROVIDER_ALIASES = {
    "alibaba": "dashscope",
    "aliyun": "dashscope",
    "silicon-flow": "siliconflow",
    "silicon_flow": "siliconflow",
}
_BASE_URL_KEYS = (
    "OPENROUTER_BASE_URL",
    "OPENROUTER_API_BASE",
    "SILICONFLOW_BASE_URL",
    "SILICONFLOW_API_BASE",
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_API_BASE",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
)
# provider -> (api key env names, api base env names, default api base), read after API_KEY / API_BASE.
_PROVIDER_ENDPOINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "openrouter": (
        ("OPENROUTER_API_KEY", "OPENAI_API_KEY"),
        ("OPENROUTER_BASE_URL", "OPENROUTER_API_BASE"),
        "https://openrouter.ai/api/v1",
    ),
    "siliconflow": (
        ("SILICONFLOW_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"),
        ("SILICONFLOW_BASE_URL", "SILICONFLOW_API_BASE"),
        "https://api.siliconflow.cn/v1",
    ),
    "dashscope": (
        ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"),
        ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_BASE"),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "openai": (
        ("OPENAI_API_KEY", "OPENROUTER_API_KEY"),
        ("OPENAI_BASE_URL", "OPENAI_API_BASE"),
        "https://api.openai.com/v1",
    ),
}


def _first(environ: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = (environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _int(environ: Mapping[str, str], *keys: str, default: int, minimum: int) -> int:
    for key in keys:
        try:
            value = int((environ.get(key) or "").strip())
        except ValueError:
            continue
        if value >= minimum:
            return value
    return default


def _bool(environ: Mapping[str, str], key: str, *, default: bool) -> bool:
    value = (environ.get(key) or "").strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


def _float(environ: Mapping[str, str], keys: tuple[str, ...], *, default: float, maximum: float) -> float:
    for key in keys:
        try:
            value = float((environ.get(key) or "").strip())
        except ValueError:
            continue
        if 0.0 <= value <= maximum:
            return value
    return default


def _driver(environ: Mapping[str, str]) -> str:
    value = (environ.get("BROWSER_DRIVER") or "").strip().lower()
    if not value or value in _LEGACY_DRIVER_VALUES:
        return DRIVERS[0]
    if value not in DRIVERS:
        raise ValueError(f"BROWSER_DRIVER must be one of: {', '.join(DRIVERS)} (got {value!r})")
    return value


def _provider_from_api_base(api_base: str) -> str:
    base = api_base.lower()
    if not base:
        return ""
    for provider, marker in (
        ("openrouter", "openrouter.ai"),
        ("siliconflow", "siliconflow"),
        ("dashscope", "dashscope"),
    ):
        if marker in base:
            return provider
    return "openai"


def _provider(environ: Mapping[str, str]) -> str:
    raw = _first(environ, "MODEL_PROVIDER", "MODEL_CLIENT_PROVIDER")
    if raw:
        lowered = raw.lower()
        return lowered if lowered in SUPPORTED_MODEL_PROVIDERS else _PROVIDER_ALIASES.get(lowered, raw)
    api_base = _first(environ, "API_BASE", "MODEL_API_BASE") or _first(environ, *_BASE_URL_KEYS)
    inferred = _provider_from_api_base(api_base)
    if inferred:
        return inferred
    for provider, key in (
        ("openrouter", "OPENROUTER_API_KEY"),
        ("siliconflow", "SILICONFLOW_API_KEY"),
        ("dashscope", "DASHSCOPE_API_KEY"),
    ):
        if _first(environ, key):
            return provider
    return "openai"


def _tool_timeout_s(environ: Mapping[str, str]) -> float:
    raw = (
        environ.get("PLAYWRIGHT_TOOL_TIMEOUT_S")
        or environ.get("PLAYWRIGHT_MCP_TIMEOUT_S")
        or environ.get("BROWSER_TIMEOUT_S")
    )
    try:
        value = float(raw or DEFAULT_TOOL_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TOOL_TIMEOUT_S
    return value if value > 0 else DEFAULT_TOOL_TIMEOUT_S


def _guardrails(environ: Mapping[str, str]) -> BrowserRunGuardrails:
    return BrowserRunGuardrails(
        max_steps=_int(environ, "BROWSER_GUARDRAIL_MAX_STEPS", default=DEFAULT_GUARDRAIL_MAX_STEPS, minimum=1),
        max_failures=_int(environ, "BROWSER_GUARDRAIL_MAX_FAILURES", default=DEFAULT_GUARDRAIL_MAX_FAILURES, minimum=0),
        timeout_s=_int(
            environ, "BROWSER_TIMEOUT_S", "PLAYWRIGHT_TOOL_TIMEOUT_S", default=DEFAULT_BROWSER_TIMEOUT_S, minimum=1
        ),
        retry_once=_bool(environ, "BROWSER_GUARDRAIL_RETRY_ONCE", default=DEFAULT_GUARDRAIL_RETRY_ONCE),
        resume_on_max_iterations=_bool(environ, "BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS", default=False),
    )


def _log_flag(environ: Mapping[str, str], key: str) -> bool:
    value = environ.get(key)
    return value is not None and value.strip().lower() not in _LOG_FLAG_OFF


def _log_file(environ: Mapping[str, str]) -> Path | None:
    configured = environ.get("OPENJIUWEN_BROWSER_AGENT_LOG_FILE")
    if configured is None:
        return DEFAULT_LOG_FILE
    configured = configured.strip()
    if configured.lower() in _LOG_FILE_OFF:
        return None
    return Path(configured).expanduser()


def _log_level(environ: Mapping[str, str]) -> int:
    name = environ.get("OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, name, logging.INFO)
    return level if isinstance(level, int) else logging.INFO


@dataclass(frozen=True)
class CoreSettings:
    """Settings of the agent core: driver, surface, model, guardrails and logging; nothing surface-specific."""

    driver: str = DRIVERS[0]
    surface: str = "browser"
    provider: str = "openai"
    api_key: str = ""
    api_base: str = _PROVIDER_ENDPOINTS["openai"][2]
    model_name: str = DEFAULT_MODEL_NAME
    worker_temperature: float = DEFAULT_WORKER_TEMPERATURE
    worker_top_p: float = DEFAULT_WORKER_TOP_P
    tool_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S
    guardrails: BrowserRunGuardrails = field(default_factory=BrowserRunGuardrails)
    log_file: Path | None = DEFAULT_LOG_FILE
    log_level: int = logging.INFO
    log_mirror_common: bool = False
    log_audit_raw: bool = False
    status_log: bool = True

    @property
    def topology(self) -> str:
        return "delegated_worker" if self.driver == "playwright_mcp" else "direct"

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> CoreSettings:
        provider = _provider(environ)
        key_names, base_names, default_base = _PROVIDER_ENDPOINTS.get(provider, _PROVIDER_ENDPOINTS["openai"])
        return cls(
            driver=_driver(environ),
            provider=provider,
            api_key=_first(environ, "API_KEY", "MODEL_API_KEY", *key_names),
            api_base=_first(environ, "API_BASE", "MODEL_API_BASE", *base_names) or default_base,
            model_name=_first(environ, "MODEL_NAME") or DEFAULT_MODEL_NAME,
            worker_temperature=_float(
                environ,
                ("BROWSER_WORKER_TEMPERATURE", "BROWSER_MODEL_TEMPERATURE", "MODEL_TEMPERATURE"),
                default=DEFAULT_WORKER_TEMPERATURE,
                maximum=2.0,
            ),
            worker_top_p=_float(
                environ,
                ("BROWSER_WORKER_TOP_P", "BROWSER_MODEL_TOP_P", "MODEL_TOP_P"),
                default=DEFAULT_WORKER_TOP_P,
                maximum=1.0,
            ),
            tool_timeout_s=_tool_timeout_s(environ),
            guardrails=_guardrails(environ),
            log_file=_log_file(environ),
            log_level=_log_level(environ),
            log_mirror_common=_log_flag(environ, "OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON"),
            log_audit_raw=_log_flag(environ, "OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW"),
            status_log=(environ.get("BROWSER_SUBAGENT_STATUS_LOG") or "").strip().lower() not in _FALSY,
        )

    @classmethod
    def from_process_env(cls) -> CoreSettings:
        return cls.from_env(os.environ)


def resolve_playwright_mcp_cwd() -> str:
    """Resolve MCP working directory with relocatable defaults."""
    return str(BrowserSettings.from_process_env().mcp_cwd)


def _ensure_capabilities(
    args: list[str],
    required_capabilities: Iterable[str],
) -> list[str]:
    """Return Playwright MCP args with the specified capabilities enabled."""
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


def build_browser_guardrails() -> BrowserRunGuardrails:
    return CoreSettings.from_process_env().guardrails


def build_playwright_mcp_config(
    instance: Optional[BrowserInstanceConfig] = None,
    required_capability: Optional[list[str]] = None,
) -> McpServerConfig:
    return _playwright_mcp_config(BrowserSettings.from_process_env(), instance, required_capability)


def _playwright_mcp_config(
    settings: BrowserSettings,
    instance: Optional[BrowserInstanceConfig] = None,
    required_capability: Optional[list[str]] = None,
) -> McpServerConfig:
    capabilities_to_enable = (
        *PLAYWRIGHT_MCP_CAPABILITY_NAMES,
        *(required_capability or ()),
    )
    launch_args = (
        parse_command_args(instance.launch_args)
        if instance is not None and instance.launch_args.strip()
        else list(settings.launch_args)
    )
    args = _ensure_capabilities(launch_args, capabilities_to_enable)
    extension_mode = settings.provisioning == "extension" or settings.mcp_extension

    env_map: Dict[str, str] = dict(settings.mcp_env)
    if extension_mode:
        env_map["PLAYWRIGHT_MCP_EXTENSION"] = "true"
        if settings.mcp_extension_token:
            env_map["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = settings.mcp_extension_token
        if "--extension" not in args:
            args.append("--extension")
    else:
        # CDP support for official Playwright MCP server. An explicit per-instance
        # cdp_url (remote mode) wins over the shared env endpoint.
        instance_cdp = instance.cdp_url.strip() if instance and instance.cdp_url else ""
        cdp_endpoint = instance_cdp or settings.cdp_url
        if cdp_endpoint:
            if settings.mcp_device:
                raise ValueError("PLAYWRIGHT_MCP_DEVICE is not supported with CDP endpoint mode.")
            env_map["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = cdp_endpoint
            if not settings.mcp_browser:
                # CDP mode is Chromium-only.
                env_map["PLAYWRIGHT_MCP_BROWSER"] = "chrome"
        if settings.cdp_headers:
            env_map["PLAYWRIGHT_MCP_CDP_HEADERS"] = settings.cdp_headers
        if settings.cdp_timeout:
            env_map["PLAYWRIGHT_MCP_CDP_TIMEOUT"] = settings.cdp_timeout

    params: Dict[str, Any] = {
        "command": settings.mcp_command,
        "args": args,
        "cwd": str(settings.mcp_cwd),
    }
    if settings.mcp_timeout_s > 0:
        params["timeout_s"] = settings.mcp_timeout_s
    if env_map:
        params["env"] = env_map

    # Isolate the MCP registration per browser identity. Same key -> same
    # server_id (intentional sharing); different key -> isolated server.
    server_id = "playwright_official_stdio"
    server_name = "playwright-official"
    instance_key = instance.sanitized_key() if instance else ""
    if instance_key:
        server_id = f"{server_id}__{instance_key}"
        server_name = f"{server_name}-{instance_key}"

    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path="stdio://playwright",
        client_type="stdio",
        params=params,
    )


def build_runtime_settings(instance: Optional[BrowserInstanceConfig] = None) -> RuntimeSettings:
    core = CoreSettings.from_process_env()
    if core.provider not in SUPPORTED_MODEL_PROVIDERS:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER '{core.provider}'. Supported: openai, openrouter, siliconflow, dashscope."
        )
    return RuntimeSettings(
        provider=core.provider,
        api_key=core.api_key,
        api_base=core.api_base,
        model_name=core.model_name,
        mcp_cfg=_playwright_mcp_config(BrowserSettings.from_process_env(), instance),
        guardrails=core.guardrails,
        instance=instance,
    )
