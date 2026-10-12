# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``CoreSettings.from_env`` parses driver, model, sampling, guardrail and logging variables from a given mapping."""

from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.harness.tools.browser_move.runtime.config import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_GUARDRAIL_MAX_FAILURES,
    DEFAULT_GUARDRAIL_MAX_STEPS,
    DEFAULT_MODEL_NAME,
    CoreSettings,
)


def _core(**environ: str) -> CoreSettings:
    return CoreSettings.from_env(environ)


def test_empty_environment_gives_the_defaults() -> None:
    settings = _core()

    assert settings == CoreSettings()
    assert settings.driver == "playwright_mcp"
    assert settings.surface == "browser"
    assert settings.topology == ""
    assert (settings.provider, settings.api_key, settings.api_base) == ("openai", "", "https://api.openai.com/v1")
    assert settings.model_name == DEFAULT_MODEL_NAME
    assert (settings.worker_temperature, settings.worker_top_p, settings.tool_timeout_s) == (0.2, 0.1, 240.0)
    assert settings.guardrails.max_steps == DEFAULT_GUARDRAIL_MAX_STEPS
    assert settings.guardrails.max_failures == DEFAULT_GUARDRAIL_MAX_FAILURES
    assert settings.guardrails.timeout_s == DEFAULT_BROWSER_TIMEOUT_S
    assert settings.guardrails.retry_once is True
    assert settings.guardrails.resume_on_max_iterations is False
    assert settings.log_file == Path("logs") / "browser_agent.log"
    assert settings.log_level == logging.INFO
    assert (settings.log_mirror_common, settings.log_audit_raw, settings.status_log) == (False, False, True)


@pytest.mark.parametrize(
    ("raw", "driver"),
    [("playwright_mcp", "playwright_mcp"), (" Browser_Use ", "browser_use"), ("", "playwright_mcp")],
)
def test_browser_driver_selects_the_driver(raw: str, driver: str) -> None:
    assert _core(BROWSER_DRIVER=raw).driver == driver


@pytest.mark.parametrize(
    ("raw", "topology"),
    [("direct", "direct"), (" Delegated_Worker ", "delegated_worker"), ("DIRECT", "direct"), ("", ""), ("  ", "")],
)
def test_browser_topology_is_trimmed_and_lower_cased_and_unset_means_the_drivers_default(
    raw: str, topology: str
) -> None:
    assert _core(BROWSER_TOPOLOGY=raw).topology == topology


@pytest.mark.parametrize("raw", ["worker", "delegated-worker", "Direct agent"])
def test_unknown_browser_topology_is_a_clear_error(raw: str) -> None:
    with pytest.raises(ValueError, match="BROWSER_TOPOLOGY must be one of: direct, delegated_worker"):
        _core(BROWSER_TOPOLOGY=raw)


def test_browser_topology_does_not_depend_on_the_driver() -> None:
    assert _core(BROWSER_DRIVER="browser_use").topology == ""
    assert _core(BROWSER_DRIVER="playwright_mcp", BROWSER_TOPOLOGY="direct").topology == "direct"


@pytest.mark.parametrize("legacy", ["managed", "remote", "extension"])
def test_legacy_browser_driver_values_leave_the_default_driver_without_a_warning(legacy: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        settings = _core(BROWSER_DRIVER=legacy)

    assert settings.driver == "playwright_mcp"


@pytest.mark.parametrize("raw", ["cua", "browser-use", "BROWSER_DRIVER_BACKEND"])
def test_any_browser_driver_name_is_kept_for_the_registry_to_check(raw: str) -> None:
    assert _core(BROWSER_DRIVER=raw).driver == raw.lower()


def test_browser_driver_backend_is_not_read() -> None:
    assert _core(BROWSER_DRIVER_BACKEND="browser_use").driver == "playwright_mcp"


@pytest.mark.parametrize(
    ("environ", "provider", "api_key", "api_base"),
    [
        (
            {"MODEL_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "or", "OPENAI_API_KEY": "oa"},
            "openrouter",
            "or",
            "https://openrouter.ai/api/v1",
        ),
        (
            {"MODEL_CLIENT_PROVIDER": "Aliyun", "DASHSCOPE_API_KEY": "ds", "DASHSCOPE_API_BASE": "https://ds/v1"},
            "dashscope",
            "ds",
            "https://ds/v1",
        ),
        (
            {"MODEL_PROVIDER": "silicon-flow", "OPENAI_API_KEY": "oa"},
            "siliconflow",
            "oa",
            "https://api.siliconflow.cn/v1",
        ),
        (
            {"API_KEY": "k", "MODEL_API_KEY": "mk", "API_BASE": "https://openrouter.ai/api/v1"},
            "openrouter",
            "k",
            "https://openrouter.ai/api/v1",
        ),
        (
            {"OPENAI_BASE_URL": "https://dashscope.example/v1"},
            "dashscope",
            "",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        ({"SILICONFLOW_API_KEY": "sf"}, "siliconflow", "sf", "https://api.siliconflow.cn/v1"),
        ({"DASHSCOPE_API_KEY": "ds"}, "dashscope", "ds", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        (
            {"OPENROUTER_API_KEY": "or", "MODEL_API_BASE": "https://proxy/v1"},
            "openai",
            "or",
            "https://proxy/v1",
        ),
        ({"OPENAI_API_KEY": "oa", "OPENAI_API_BASE": "https://oa/v1"}, "openai", "oa", "https://oa/v1"),
    ],
)
def test_model_provider_key_and_base_follow_the_provider_precedence(
    environ: dict[str, str], provider: str, api_key: str, api_base: str
) -> None:
    settings = CoreSettings.from_env(environ)

    assert (settings.provider, settings.api_key, settings.api_base) == (provider, api_key, api_base)


def test_unsupported_model_provider_is_kept_for_the_caller_to_reject() -> None:
    settings = _core(MODEL_PROVIDER="Anthropic", API_KEY="k")

    assert (settings.provider, settings.api_key, settings.api_base) == ("Anthropic", "k", "https://api.openai.com/v1")


def test_model_name_is_read_and_blank_falls_back() -> None:
    assert _core(MODEL_NAME=" anthropic/claude-sonnet-5.5 ").model_name == "anthropic/claude-sonnet-5.5"
    assert _core(MODEL_NAME="  ").model_name == DEFAULT_MODEL_NAME


@pytest.mark.parametrize(
    ("environ", "temperature", "top_p"),
    [
        ({"BROWSER_WORKER_TEMPERATURE": "1", "MODEL_TEMPERATURE": "0.5", "BROWSER_WORKER_TOP_P": "1"}, 1.0, 1.0),
        ({"BROWSER_MODEL_TEMPERATURE": "0.7", "BROWSER_MODEL_TOP_P": "0.9"}, 0.7, 0.9),
        ({"MODEL_TEMPERATURE": "0.3", "MODEL_TOP_P": "0.4"}, 0.3, 0.4),
        ({"BROWSER_WORKER_TEMPERATURE": "hot", "MODEL_TEMPERATURE": "0.3", "BROWSER_WORKER_TOP_P": "x"}, 0.3, 0.1),
        ({"BROWSER_WORKER_TEMPERATURE": "2.5", "BROWSER_WORKER_TOP_P": "1.5", "MODEL_TOP_P": "-1"}, 0.2, 0.1),
    ],
)
def test_worker_sampling_takes_the_first_valid_key_in_range(
    environ: dict[str, str], temperature: float, top_p: float
) -> None:
    settings = CoreSettings.from_env(environ)

    assert (settings.worker_temperature, settings.worker_top_p) == (temperature, top_p)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"PLAYWRIGHT_TOOL_TIMEOUT_S": "30", "PLAYWRIGHT_MCP_TIMEOUT_S": "40", "BROWSER_TIMEOUT_S": "50"}, 30.0),
        ({"PLAYWRIGHT_MCP_TIMEOUT_S": "40", "BROWSER_TIMEOUT_S": "50"}, 40.0),
        ({"BROWSER_TIMEOUT_S": "50.5"}, 50.5),
        ({"PLAYWRIGHT_TOOL_TIMEOUT_S": "soon", "BROWSER_TIMEOUT_S": "50"}, 240.0),
        ({"PLAYWRIGHT_TOOL_TIMEOUT_S": "0", "BROWSER_TIMEOUT_S": "50"}, 240.0),
        ({"PLAYWRIGHT_TOOL_TIMEOUT_S": "", "BROWSER_TIMEOUT_S": "50"}, 50.0),
    ],
)
def test_tool_timeout_takes_the_first_set_key_and_falls_back_when_it_is_invalid(
    environ: dict[str, str], expected: float
) -> None:
    assert CoreSettings.from_env(environ).tool_timeout_s == expected


def test_guardrails_are_read_with_their_minimums_and_boolean_words() -> None:
    guardrails = _core(
        BROWSER_GUARDRAIL_MAX_STEPS="50",
        BROWSER_GUARDRAIL_MAX_FAILURES="0",
        BROWSER_TIMEOUT_S="45",
        PLAYWRIGHT_TOOL_TIMEOUT_S="99",
        BROWSER_GUARDRAIL_RETRY_ONCE="off",
        BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS="yes",
    ).guardrails

    assert (guardrails.max_steps, guardrails.max_failures, guardrails.timeout_s) == (50, 0, 45)
    assert (guardrails.retry_once, guardrails.resume_on_max_iterations) == (False, True)


def test_invalid_guardrails_fall_back_per_key() -> None:
    guardrails = _core(
        BROWSER_GUARDRAIL_MAX_STEPS="0",
        BROWSER_GUARDRAIL_MAX_FAILURES="-1",
        BROWSER_TIMEOUT_S="never",
        PLAYWRIGHT_TOOL_TIMEOUT_S="70",
        BROWSER_GUARDRAIL_RETRY_ONCE="maybe",
        BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS="perhaps",
    ).guardrails

    assert (guardrails.max_steps, guardrails.max_failures, guardrails.timeout_s) == (
        DEFAULT_GUARDRAIL_MAX_STEPS,
        DEFAULT_GUARDRAIL_MAX_FAILURES,
        70,
    )
    assert (guardrails.retry_once, guardrails.resume_on_max_iterations) == (True, False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("~/agent.log", Path("~/agent.log").expanduser()),
        (" off ", None),
        ("None", None),
        ("-", None),
        ("", Path("")),
    ],
)
def test_log_file_can_be_moved_or_disabled(raw: str, expected: Path | None) -> None:
    assert _core(OPENJIUWEN_BROWSER_AGENT_LOG_FILE=raw).log_file == expected


@pytest.mark.parametrize(
    ("raw", "level"), [("debug", logging.DEBUG), (" error ", logging.ERROR), ("chatty", logging.INFO)]
)
def test_log_level_names_map_to_logging_levels(raw: str, level: int) -> None:
    assert _core(OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL=raw).log_level == level


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("anything", True), ("", False), ("no", False)])
def test_log_mirror_and_raw_audit_are_on_for_any_set_value_but_false_words(raw: str, expected: bool) -> None:
    settings = _core(OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON=raw, OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW=raw)

    assert (settings.log_mirror_common, settings.log_audit_raw) == (expected, expected)


@pytest.mark.parametrize(("raw", "expected"), [("off", False), ("0", False), ("on", True), ("whatever", True)])
def test_status_log_is_on_unless_switched_off(raw: str, expected: bool) -> None:
    assert _core(BROWSER_SUBAGENT_STATUS_LOG=raw).status_log is expected


def test_from_env_reads_only_the_mapping_it_is_given() -> None:
    conflicting = {
        "BROWSER_DRIVER": "browser_use",
        "MODEL_NAME": "from-process",
        "OPENAI_API_KEY": "process-key",
        "BROWSER_GUARDRAIL_MAX_STEPS": "7",
        "OPENJIUWEN_BROWSER_AGENT_LOG_FILE": "off",
    }
    with patch.dict(os.environ, conflicting, clear=True):
        settings = CoreSettings.from_env({"MODEL_NAME": "from-mapping"})

    assert settings.driver == "playwright_mcp"
    assert settings.model_name == "from-mapping"
    assert settings.api_key == ""
    assert settings.guardrails.max_steps == DEFAULT_GUARDRAIL_MAX_STEPS
    assert settings.log_file == Path("logs") / "browser_agent.log"


def test_from_process_env_reads_the_process_environment() -> None:
    with patch.dict(os.environ, {"BROWSER_DRIVER": "browser_use", "MODEL_NAME": "m"}, clear=True):
        settings = CoreSettings.from_process_env()

    assert (settings.driver, settings.model_name) == ("browser_use", "m")


def test_core_settings_are_frozen() -> None:
    with pytest.raises(AttributeError):
        CoreSettings().driver = "browser_use"  # type: ignore[misc]
