# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime configuration helpers."""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from openjiuwen.core.foundation.tool import McpServerConfig

from ..contract import SurfaceSettings
from ..surfaces import SURFACES
from . import REPO_ROOT
from .utils.env import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_GUARDRAIL_MAX_FAILURES,
    DEFAULT_GUARDRAIL_MAX_STEPS,
    DEFAULT_GUARDRAIL_RETRY_ONCE,
    DEFAULT_MODEL_NAME,
    FALSY_ENV_VALUES,
    MISSING_API_KEY_MESSAGE,  # noqa: F401  (re-exported for main.py / mcp_server)
    SUPPORTED_MODEL_PROVIDERS,
    first_value,
    infer_provider_from_api_base,
    int_value,
    is_falsy_env,
    is_truthy_env,
    normalize_provider,
    parse_command_args,
)


@dataclass
class BrowserRunGuardrails:
    max_steps: int = DEFAULT_GUARDRAIL_MAX_STEPS
    max_failures: int = DEFAULT_GUARDRAIL_MAX_FAILURES
    timeout_s: int = DEFAULT_BROWSER_TIMEOUT_S
    retry_once: bool = True
    resume_on_max_iterations: bool = False


@dataclass(frozen=True)
class BrowserInstanceConfig:
    """Per-instance identity, used to isolate one device per agent.

    All fields default to empty/0, which reproduces the legacy process-global
    (env-driven) behavior. When ``key`` is non-empty the device is isolated:
    the Playwright MCP ``server_id`` is suffixed with the key and the managed
    profile / port / user-data-dir are derived from it instead of shared env.
    Agents sharing the same ``key`` intentionally share one device.
    """

    key: str = ""
    driver_mode: str = ""  # alias of provisioning
    managed_port: int = 0  # 0 -> auto-allocate (keyed) or env/default (legacy)
    user_data_dir: str = ""  # "" -> derived from key under the profiles directory in mcp_cwd
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
TOPOLOGIES = ("direct", "delegated_worker")
DEFAULT_TOOL_TIMEOUT_S = 240.0
DEFAULT_WORKER_TEMPERATURE = 0.2
DEFAULT_WORKER_TOP_P = 0.1
DEFAULT_LOG_FILE = Path("logs") / "browser_agent.log"
_LEGACY_DRIVER_VALUES = frozenset({"managed", "remote", "extension"})
_LOG_FLAG_OFF = FALSY_ENV_VALUES | {""}
_LOG_FILE_OFF = FALSY_ENV_VALUES | {"none", "null", "-"}
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


def _bool(environ: Mapping[str, str], key: str, *, default: bool) -> bool:
    value = environ.get(key) or ""
    if is_truthy_env(value):
        return True
    if is_falsy_env(value):
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


def _topology(environ: Mapping[str, str]) -> str:
    value = (environ.get("BROWSER_TOPOLOGY") or "").strip().lower()
    if value and value not in TOPOLOGIES:
        raise ValueError(f"BROWSER_TOPOLOGY must be one of: {', '.join(TOPOLOGIES)} (got {value!r})")
    return value


def _provider(environ: Mapping[str, str]) -> str:
    raw = first_value(environ, "MODEL_PROVIDER", "MODEL_CLIENT_PROVIDER")
    if raw:
        return normalize_provider(raw)
    api_base = first_value(environ, "API_BASE", "MODEL_API_BASE") or first_value(environ, *_BASE_URL_KEYS)
    inferred = infer_provider_from_api_base(api_base)
    if inferred:
        return inferred
    for provider, key in (
        ("openrouter", "OPENROUTER_API_KEY"),
        ("siliconflow", "SILICONFLOW_API_KEY"),
        ("dashscope", "DASHSCOPE_API_KEY"),
    ):
        if first_value(environ, key):
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
        max_steps=int_value(environ, "BROWSER_GUARDRAIL_MAX_STEPS", default=DEFAULT_GUARDRAIL_MAX_STEPS, minimum=1),
        max_failures=int_value(
            environ, "BROWSER_GUARDRAIL_MAX_FAILURES", default=DEFAULT_GUARDRAIL_MAX_FAILURES, minimum=0
        ),
        timeout_s=int_value(
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


def _timeout_or_zero(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        return 0


def _optional_path(raw: str) -> Path | None:
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class RuntimeServerSettings:
    """The runtime MCP server (``playwright_runtime_mcp_server``): how the agent reaches it, how it listens."""

    enabled: bool = False
    server_id: str = "playwright_runtime_wrapper"
    server_name: str = "playwright-runtime-wrapper"
    client_type: str = "streamable-http"
    server_path: str = ""
    command: str = sys.executable
    args: str = ""
    timeout_s: int = 300
    host: str = "127.0.0.1"
    port: str = "8940"
    path: str = ""
    log_dir: Path | None = None
    auto_http_fallback: bool = True
    transport: str = "stdio"
    log_level: str = "INFO"
    stateless_http: bool | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> RuntimeServerSettings:
        """Client side: ``PLAYWRIGHT_RUNTIME_*`` first, then the ``BROWSER_RUNTIME_*`` aliases."""
        auto_http_fallback = first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_AUTO_HTTP_FALLBACK")
        return cls(
            enabled=is_truthy_env(
                first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_ENABLED", "BROWSER_RUNTIME_MCP_ENABLED")
            ),
            server_id=first_value(
                environ, "PLAYWRIGHT_RUNTIME_MCP_SERVER_ID", "BROWSER_RUNTIME_MCP_SERVER_ID", default=cls.server_id
            ),
            server_name=first_value(
                environ,
                "PLAYWRIGHT_RUNTIME_MCP_SERVER_NAME",
                "BROWSER_RUNTIME_MCP_SERVER_NAME",
                default=cls.server_name,
            ),
            client_type=first_value(
                environ,
                "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE",
                "BROWSER_RUNTIME_MCP_CLIENT_TYPE",
                default=cls.client_type,
            ),
            server_path=first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH", "BROWSER_RUNTIME_MCP_SERVER_PATH"),
            command=first_value(
                environ, "PLAYWRIGHT_RUNTIME_MCP_COMMAND", "BROWSER_RUNTIME_MCP_COMMAND", default=sys.executable
            ),
            args=first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_ARGS", "BROWSER_RUNTIME_MCP_ARGS"),
            timeout_s=_timeout_or_zero(
                first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_TIMEOUT_S", "BROWSER_RUNTIME_MCP_TIMEOUT_S", default="300")
            ),
            host=first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_HOST", "BROWSER_RUNTIME_MCP_HOST", default=cls.host),
            port=first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_PORT", "BROWSER_RUNTIME_MCP_PORT", default=cls.port),
            path=first_value(environ, "PLAYWRIGHT_RUNTIME_MCP_PATH", "BROWSER_RUNTIME_MCP_PATH"),
            log_dir=_optional_path(first_value(environ, "PLAYWRIGHT_RUNTIME_LOG_DIR", "BROWSER_RUNTIME_LOG_DIR")),
            auto_http_fallback=is_truthy_env(auto_http_fallback) if auto_http_fallback else True,
        )

    @classmethod
    def server_from_env(cls, environ: Mapping[str, str]) -> RuntimeServerSettings:
        """Server entry point: ``PLAYWRIGHT_RUNTIME_*`` only (the log dir also takes its alias), its own defaults."""
        stateless_http = environ.get("PLAYWRIGHT_RUNTIME_MCP_STATELESS_HTTP") or ""
        return cls(
            transport=(environ.get("PLAYWRIGHT_RUNTIME_MCP_TRANSPORT") or "stdio").strip().lower(),
            host=(environ.get("PLAYWRIGHT_RUNTIME_MCP_HOST") or "127.0.0.1").strip(),
            port=(environ.get("PLAYWRIGHT_RUNTIME_MCP_PORT") or "8940").strip(),
            path=(environ.get("PLAYWRIGHT_RUNTIME_MCP_PATH") or "").strip(),
            log_level=(environ.get("PLAYWRIGHT_RUNTIME_MCP_LOG_LEVEL") or "INFO").strip(),
            log_dir=_optional_path(first_value(environ, "PLAYWRIGHT_RUNTIME_LOG_DIR", "BROWSER_RUNTIME_LOG_DIR")),
            stateless_http=True if is_truthy_env(stateless_http) else False if is_falsy_env(stateless_http) else None,
        )


@dataclass(frozen=True)
class CoreSettings:
    """Settings of the agent core: driver, surface, model, guardrails and logging; nothing surface-specific."""

    driver: str = DRIVERS[0]
    surface: str = "browser"
    topology: str = ""
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
    runtime_server: RuntimeServerSettings = field(default_factory=RuntimeServerSettings)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> CoreSettings:
        provider = _provider(environ)
        key_names, base_names, default_base = _PROVIDER_ENDPOINTS.get(provider, _PROVIDER_ENDPOINTS["openai"])
        return cls(
            driver=_driver(environ),
            topology=_topology(environ),
            provider=provider,
            api_key=first_value(environ, "API_KEY", "MODEL_API_KEY", *key_names),
            api_base=first_value(environ, "API_BASE", "MODEL_API_BASE", *base_names) or default_base,
            model_name=first_value(environ, "MODEL_NAME") or DEFAULT_MODEL_NAME,
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
            status_log=not is_falsy_env(environ.get("BROWSER_SUBAGENT_STATUS_LOG") or ""),
            runtime_server=RuntimeServerSettings.from_env(environ),
        )

    @classmethod
    def from_process_env(cls) -> CoreSettings:
        return cls.from_env(os.environ)

    @classmethod
    def logging_from_process_env(cls) -> CoreSettings:
        """Default settings with only the log fields read, so a bad unrelated variable cannot break logging."""
        environ = os.environ
        return cls(
            log_file=_log_file(environ),
            log_level=_log_level(environ),
            log_mirror_common=_log_flag(environ, "OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON"),
            log_audit_raw=_log_flag(environ, "OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW"),
        )


def process_environ() -> dict[str, str]:
    """A snapshot of the process environment, for entry points and child-process environments."""
    return dict(os.environ)


def resolve_repo_dotenv_path() -> Path:
    return Path(REPO_ROOT).expanduser().resolve() / ".env"


def load_repo_dotenv(*, override: bool = False) -> bool:
    env_path = resolve_repo_dotenv_path()
    if not env_path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    return bool(load_dotenv(dotenv_path=env_path, override=override))


def read_demo_inputs(environ: Mapping[str, str]) -> tuple[str, str]:
    """``(query, session_id)`` of the demo CLI in ``main.py``."""
    query = (environ.get("AGENT_QUERY") or "").strip()
    session_id = (environ.get("AGENT_SESSION_ID") or "").strip() or "demo-browser-session"
    return query, session_id


def runtime_server_environ(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` with the runtime MCP server's tool timeout defaults filled in."""
    env = dict(environ)
    env.setdefault("BROWSER_TIMEOUT_S", str(DEFAULT_BROWSER_TIMEOUT_S))
    env.setdefault("PLAYWRIGHT_TOOL_TIMEOUT_S", env["BROWSER_TIMEOUT_S"])
    return env


_PROXY_BLOCKLIST = frozenset({"http://127.0.0.1:9", "http://localhost:9"})


def runtime_server_child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Environment of the runtime MCP server child: ``environ`` with API_KEY / API_BASE under provider names."""
    env = dict(environ)
    api_key = (env.get("API_KEY") or "").strip()
    api_base = (env.get("API_BASE") or "").strip()
    model_provider = (env.get("MODEL_PROVIDER") or "").strip().lower()

    if api_key and not env.get("OPENROUTER_API_KEY") and "openrouter.ai" in api_base:
        env["OPENROUTER_API_KEY"] = api_key
    if api_base and not env.get("OPENROUTER_BASE_URL") and "openrouter.ai" in api_base:
        env["OPENROUTER_BASE_URL"] = api_base

    if api_key and not env.get("OPENAI_API_KEY") and "openrouter.ai" not in api_base:
        env["OPENAI_API_KEY"] = api_key
    if api_base and not env.get("OPENAI_BASE_URL") and "openrouter.ai" not in api_base:
        env["OPENAI_BASE_URL"] = api_base

    if model_provider == "openrouter":
        env["MODEL_PROVIDER"] = "openrouter"
    elif model_provider in {"openai", "siliconflow"}:
        env["MODEL_PROVIDER"] = model_provider

    for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        proxy_value = (env.get(proxy_key) or "").strip().lower()
        if proxy_value in _PROXY_BLOCKLIST:
            env.pop(proxy_key, None)
    return env


def browser_surface_settings(environ: Mapping[str, str] | None = None) -> SurfaceSettings:
    return SURFACES.create("browser", os.environ if environ is None else environ).settings


def resolve_playwright_mcp_cwd() -> str:
    """Resolve MCP working directory with relocatable defaults."""
    return str(browser_surface_settings().mcp_cwd)


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
    return playwright_mcp_config(browser_surface_settings(), instance, required_capability)


def playwright_mcp_config(
    settings: Any,
    instance: Optional[BrowserInstanceConfig] = None,
    required_capability: Optional[list[str]] = None,
) -> McpServerConfig:
    """The ``@playwright/mcp`` server config from the surface settings; ``instance.cdp_url`` wins over them."""
    # Imported here so that importing the package loads no surface module.
    from ..surfaces.browser.capabilities import (
        CORE_BROWSER_CAPABILITY_NAME,
        DEFAULT_BROWSER_CAPABILITIES,
        POLICY_ONLY_BROWSER_CAPABILITY_NAMES,
    )

    capabilities_to_enable = (
        *(
            spec.name
            for spec in DEFAULT_BROWSER_CAPABILITIES
            if spec.name != CORE_BROWSER_CAPABILITY_NAME and spec.name not in POLICY_ONLY_BROWSER_CAPABILITY_NAMES
        ),
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

    # Isolate the MCP registration per instance identity. Same key -> same
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
