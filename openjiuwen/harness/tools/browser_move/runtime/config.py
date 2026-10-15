# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The core settings: driver, surface, model, guardrails, logging and the runtime server, read from one mapping."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from . import REPO_ROOT
from .utils.env import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_GUARDRAIL_MAX_FAILURES,
    DEFAULT_GUARDRAIL_MAX_STEPS,
    DEFAULT_GUARDRAIL_RETRY_ONCE,
    DEFAULT_MODEL_NAME,
    FALSY_ENV_VALUES,
    MISSING_API_KEY_MESSAGE,  # noqa: F401
    first_value,
    infer_provider_from_api_base,
    int_value,
    is_falsy_env,
    is_truthy_env,
    normalize_provider,
)


@dataclass
class RunGuardrails:
    max_steps: int = DEFAULT_GUARDRAIL_MAX_STEPS
    max_failures: int = DEFAULT_GUARDRAIL_MAX_FAILURES
    timeout_s: int = DEFAULT_BROWSER_TIMEOUT_S
    retry_once: bool = True
    resume_on_max_iterations: bool = False


DEFAULT_DRIVER = "playwright_mcp"
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
    return DEFAULT_DRIVER if not value or value in _LEGACY_DRIVER_VALUES else value


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


def _guardrails(environ: Mapping[str, str]) -> RunGuardrails:
    return RunGuardrails(
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

    driver: str = DEFAULT_DRIVER
    surface: str = "browser"
    topology: str = ""
    provider: str = "openai"
    api_key: str = ""
    api_base: str = _PROVIDER_ENDPOINTS["openai"][2]
    model_name: str = DEFAULT_MODEL_NAME
    worker_temperature: float = DEFAULT_WORKER_TEMPERATURE
    worker_top_p: float = DEFAULT_WORKER_TOP_P
    tool_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S
    guardrails: RunGuardrails = field(default_factory=RunGuardrails)
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
