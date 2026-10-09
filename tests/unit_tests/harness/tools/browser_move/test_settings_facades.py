# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The public ``RuntimeSettings`` and ``BrowserInstanceConfig`` facades over ``CoreSettings`` + ``BrowserSettings``."""

from __future__ import annotations

import dataclasses
import os
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
    CoreSettings,
    build_browser_guardrails,
    build_playwright_mcp_config,
    build_runtime_settings,
    resolve_playwright_mcp_cwd,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings


def test_browser_instance_config_keeps_its_positional_fields_and_adds_provisioning_and_driver() -> None:
    names = [f.name for f in dataclasses.fields(BrowserInstanceConfig)]

    assert names == [
        "key",
        "driver_mode",
        "managed_port",
        "user_data_dir",
        "profile_name",
        "cdp_url",
        "browser_binary",
        "launch_args",
        "provisioning",
        "driver",
    ]
    assert BrowserInstanceConfig("k", "managed").provisioning == "managed"


@pytest.mark.parametrize(
    "instance",
    [BrowserInstanceConfig(driver_mode="managed"), BrowserInstanceConfig(provisioning="managed")],
)
def test_driver_mode_is_an_alias_of_provisioning(instance: BrowserInstanceConfig) -> None:
    assert (instance.provisioning, instance.driver_mode) == ("managed", "managed")
    assert instance == BrowserInstanceConfig(provisioning="managed", driver_mode="managed")


def test_conflicting_driver_mode_and_provisioning_are_refused() -> None:
    with pytest.raises(ValueError, match="driver_mode is an alias of provisioning"):
        BrowserInstanceConfig(driver_mode="remote", provisioning="managed")


def test_browser_instance_config_carries_a_driver() -> None:
    instance = BrowserInstanceConfig(key="a", driver="browser_use")

    assert (instance.driver, instance.provisioning) == ("browser_use", "")


def test_build_runtime_settings_composes_core_and_browser_settings(tmp_path: Path) -> None:
    environ = {
        "MODEL_PROVIDER": "openrouter",
        "OPENROUTER_API_KEY": "or-key",
        "MODEL_NAME": "anthropic/claude-sonnet-5.5",
        "BROWSER_GUARDRAIL_MAX_STEPS": "50",
        "BROWSER_TIMEOUT_S": "45",
        "PLAYWRIGHT_MCP_COMMAND": "node",
        "PLAYWRIGHT_RUNTIME_MCP_CWD": str(tmp_path),
    }
    with patch.dict(os.environ, environ, clear=True):
        settings = build_runtime_settings()
    core = CoreSettings.from_env(environ)
    browser = BrowserSettings.from_env(environ)

    assert (settings.provider, settings.api_key, settings.api_base, settings.model_name) == (
        core.provider,
        core.api_key,
        core.api_base,
        core.model_name,
    )
    assert settings.guardrails == core.guardrails
    assert settings.mcp_cfg.params["command"] == browser.mcp_command
    assert settings.mcp_cfg.params["cwd"] == str(browser.mcp_cwd)
    assert settings.mcp_cfg.params["timeout_s"] == browser.mcp_timeout_s == 45


def test_build_runtime_settings_still_rejects_an_unsupported_model_provider() -> None:
    with patch.dict(os.environ, {"MODEL_PROVIDER": "anthropic"}, clear=True):
        with pytest.raises(ValueError, match="Unsupported MODEL_PROVIDER 'anthropic'"):
            build_runtime_settings()


def test_guardrails_and_mcp_config_do_not_reject_an_unsupported_model_provider() -> None:
    with patch.dict(os.environ, {"MODEL_PROVIDER": "anthropic", "BROWSER_GUARDRAIL_MAX_STEPS": "9"}, clear=True):
        assert build_browser_guardrails().max_steps == 9
        assert build_playwright_mcp_config().server_id == "playwright_official_stdio"


@pytest.mark.parametrize(
    ("environ", "warns"),
    [
        ({"BROWSER_DRIVER": "extension"}, True),
        ({"BROWSER_PROVISIONING": "extension"}, False),
        ({"BROWSER_DRIVER": "browser_use", "BROWSER_PROVISIONING": "extension"}, False),
    ],
)
def test_extension_provisioning_reaches_the_playwright_mcp_config(environ: dict[str, str], warns: bool) -> None:
    with patch.dict(os.environ, environ, clear=True), warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        cfg = build_playwright_mcp_config()

    assert "--extension" in cfg.params["args"]
    assert cfg.params["env"]["PLAYWRIGHT_MCP_EXTENSION"] == "true"
    assert [w.category for w in record] == ([DeprecationWarning] if warns else [])


def test_unknown_browser_driver_fails_when_the_runtime_settings_are_built() -> None:
    with patch.dict(os.environ, {"BROWSER_DRIVER": "cua"}, clear=True):
        with pytest.raises(ValueError, match="BROWSER_DRIVER must be one of"):
            build_runtime_settings()


def test_resolve_playwright_mcp_cwd_comes_from_the_browser_settings(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"BROWSER_RUNTIME_WORKDIR": str(tmp_path)}, clear=True):
        assert resolve_playwright_mcp_cwd() == str(tmp_path.resolve())
