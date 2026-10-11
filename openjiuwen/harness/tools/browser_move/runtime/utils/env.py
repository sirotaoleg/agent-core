# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure parsing helpers for the settings modules; nothing here reads the process environment."""

from __future__ import annotations

import json
import shlex
from typing import Mapping

SUPPORTED_MODEL_PROVIDERS = frozenset({"openai", "openrouter", "siliconflow", "dashscope"})
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off"})
DEFAULT_MODEL_NAME = "anthropic/claude-sonnet-4.5"
DEFAULT_BROWSER_TIMEOUT_S = 600
DEFAULT_GUARDRAIL_MAX_STEPS = 100
DEFAULT_GUARDRAIL_MAX_FAILURES = 4
DEFAULT_GUARDRAIL_RETRY_ONCE = True
DEFAULT_BROWSER_UPLOAD_ROOT = ""
MISSING_API_KEY_MESSAGE = (
    "Missing API key. Set API_KEY (or OPENROUTER_API_KEY / SILICONFLOW_API_KEY / OPENAI_API_KEY / DASHSCOPE_API_KEY)."
)
_PROVIDER_ALIASES = {
    "alibaba": "dashscope",
    "aliyun": "dashscope",
    "silicon-flow": "siliconflow",
    "silicon_flow": "siliconflow",
}


def parse_command_args(value: str) -> list[str]:
    value = (value or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return shlex.split(value)


def env_value(environ: Mapping[str, str], key: str) -> str | None:
    # A dict copy of os.environ on Windows keeps only upper-case names (SystemRoot -> SYSTEMROOT).
    value = environ.get(key)
    return environ.get(key.upper()) if value is None else value


def first_value(environ: Mapping[str, str], *keys: str, default: str = "") -> str:
    """The first non-blank value among ``keys``, stripped."""
    for key in keys:
        value = (environ.get(key) or "").strip()
        if value:
            return value
    return default


def int_value(environ: Mapping[str, str], *keys: str, default: int, minimum: int) -> int:
    """The first value among ``keys`` that parses as an int ``>= minimum``."""
    for key in keys:
        try:
            value = int((environ.get(key) or "").strip())
        except ValueError:
            continue
        if value >= minimum:
            return value
    return default


def normalize_provider(provider: str) -> str:
    raw = (provider or "").strip()
    lowered = raw.lower()
    if lowered in SUPPORTED_MODEL_PROVIDERS:
        return lowered
    return _PROVIDER_ALIASES.get(lowered, raw)


def is_truthy_env(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered in TRUTHY_ENV_VALUES


def is_falsy_env(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered in FALSY_ENV_VALUES


def infer_provider_from_api_base(api_base: str) -> str:
    base = (api_base or "").strip().lower()
    if not base:
        return ""
    if "openrouter.ai" in base:
        return "openrouter"
    if "siliconflow" in base:
        return "siliconflow"
    if "dashscope" in base:
        return "dashscope"
    return "openai"
