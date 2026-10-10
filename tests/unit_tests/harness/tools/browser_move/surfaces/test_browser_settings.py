# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``BrowserSettings.from_env`` parses provisioning, CDP, managed Chrome, Playwright MCP and browser-use variables."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserInstanceConfig
from openjiuwen.harness.tools.browser_move.provisioning import managed_browser
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import ENV_PREFIXES, BrowserSettings


def _browser(**environ: str) -> BrowserSettings:
    return BrowserSettings.from_env(environ)


def test_empty_environment_gives_the_defaults() -> None:
    settings = _browser()

    assert settings == BrowserSettings(mcp_cwd=Path.cwd().resolve())
    assert settings.provisioning == "remote"
    assert (settings.cdp_url, settings.cdp_headers, settings.cdp_timeout) == ("", "", "")
    assert (settings.managed_host, settings.managed_port, settings.managed_kill_existing) == ("127.0.0.1", 9333, False)
    assert (settings.managed_args, settings.user_data_dir, settings.browser_binary) == ((), "", "")
    assert (settings.profile_name, settings.profile_store_path) == ("jiuwenclaw", None)
    assert settings.launch_args == ("-y", "@playwright/mcp@0.0.78")
    assert (settings.mcp_command, settings.mcp_timeout_s, settings.mcp_cwd) == ("npx", 600, Path.cwd().resolve())
    assert (settings.mcp_browser, settings.mcp_device, settings.mcp_extension, settings.mcp_extension_token) == (
        "",
        "",
        False,
        "",
    )
    assert dict(settings.mcp_env) == {}
    assert (settings.upload_root, settings.sidecar_python, settings.selector_cache_path) == (None, None, None)


def test_the_surface_declares_its_env_prefixes() -> None:
    assert ENV_PREFIXES == ("BROWSER_", "PLAYWRIGHT_")


@pytest.mark.parametrize("value", ["remote", " Managed ", "extension"])
def test_browser_provisioning_is_read(value: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _browser(BROWSER_PROVISIONING=value).provisioning == value.strip().lower()


def test_unknown_browser_provisioning_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="BROWSER_PROVISIONING must be one of: remote, managed, extension"):
        _browser(BROWSER_PROVISIONING="cloud")


@pytest.mark.parametrize("legacy", ["managed", "REMOTE", "extension"])
def test_legacy_browser_driver_value_is_read_as_provisioning_with_one_deprecation_warning(legacy: str) -> None:
    with pytest.warns(DeprecationWarning, match="BROWSER_PROVISIONING") as record:
        settings = _browser(BROWSER_DRIVER=legacy)

    assert settings.provisioning == legacy.lower()
    assert len(record) == 1


def test_explicit_browser_provisioning_wins_over_a_legacy_browser_driver() -> None:
    with pytest.warns(DeprecationWarning):
        settings = _browser(BROWSER_DRIVER="managed", BROWSER_PROVISIONING="extension")

    assert settings.provisioning == "extension"


def test_the_extension_flag_selects_extension_provisioning_when_none_is_given() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _browser(PLAYWRIGHT_MCP_EXTENSION="true").provisioning == "extension"
        assert _browser(PLAYWRIGHT_MCP_EXTENSION="false").provisioning == "remote"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"BROWSER_PROVISIONING": "managed"}, "managed"),
        ({"BROWSER_PROVISIONING": "remote"}, "remote"),
        ({"BROWSER_DRIVER": "managed"}, "managed"),
    ],
)
def test_explicit_or_legacy_provisioning_wins_over_the_extension_flag(environ: dict[str, str], expected: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        settings = _browser(PLAYWRIGHT_MCP_EXTENSION="true", **environ)

    assert settings.provisioning == expected


@pytest.mark.parametrize("driver", ["playwright_mcp", "browser_use", "anything-else"])
def test_a_driver_name_in_browser_driver_leaves_provisioning_alone(driver: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        settings = _browser(BROWSER_DRIVER=driver)

    assert settings.provisioning == "remote"


def test_cdp_variables_take_the_first_non_empty_name() -> None:
    settings = _browser(
        PLAYWRIGHT_MCP_CDP_ENDPOINT=" ",
        PLAYWRIGHT_CDP_URL="http://127.0.0.1:9222",
        PLAYWRIGHT_MCP_CDP_HEADERS="Authorization: x",
        PLAYWRIGHT_CDP_HEADERS="ignored",
        PLAYWRIGHT_CDP_TIMEOUT_MS="5000",
    )

    assert (settings.cdp_url, settings.cdp_headers, settings.cdp_timeout) == (
        "http://127.0.0.1:9222",
        "Authorization: x",
        "5000",
    )
    assert _browser(PLAYWRIGHT_MCP_CDP_ENDPOINT="ws://a", PLAYWRIGHT_CDP_URL="ws://b").cdp_url == "ws://a"
    assert _browser(PLAYWRIGHT_MCP_CDP_TIMEOUT="1", PLAYWRIGHT_CDP_TIMEOUT_MS="2").cdp_timeout == "1"


def test_managed_chrome_variables_are_read() -> None:
    settings = _browser(
        BROWSER_MANAGED_HOST=" 0.0.0.0 ",
        BROWSER_MANAGED_PORT=" 9555 ",
        BROWSER_MANAGED_KILL_EXISTING="Yes",
        BROWSER_MANAGED_ARGS='["--headless=new", "--window-size=1280,720"]',
        BROWSER_MANAGED_USER_DATA_DIR=" C:/chrome-data ",
        BROWSER_PROFILE_NAME="work",
        BROWSER_PROFILE_STORE_PATH="~/profiles.json",
        BROWSER_MANAGED_BINARY="C:/chrome.exe",
    )

    assert (settings.managed_host, settings.managed_port, settings.managed_kill_existing) == ("0.0.0.0", 9555, True)
    assert settings.managed_args == ("--headless=new", "--window-size=1280,720")
    assert (settings.user_data_dir, settings.profile_name) == ("C:/chrome-data", "work")
    assert settings.profile_store_path == Path("~/profiles.json").expanduser()
    assert settings.browser_binary == "C:/chrome.exe"


def test_managed_args_also_accept_a_shell_string() -> None:
    assert _browser(BROWSER_MANAGED_ARGS='--headless --user-agent "a b"').managed_args == (
        "--headless",
        "--user-agent",
        "a b",
    )


@pytest.mark.parametrize("raw", ["abc", "0", "-1", " "])
def test_invalid_managed_port_is_a_clear_error(raw: str) -> None:
    with pytest.raises(ValueError, match="Invalid BROWSER_MANAGED_PORT"):
        _browser(BROWSER_MANAGED_PORT=raw)


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("on", True), ("no", False), ("maybe", False)])
def test_kill_existing_needs_a_true_word(raw: str, expected: bool) -> None:
    assert _browser(BROWSER_MANAGED_KILL_EXISTING=raw).managed_kill_existing is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["-y", "@playwright/mcp@0.0.78", "--isolated"]', ("-y", "@playwright/mcp@0.0.78", "--isolated")),
        ("-y @playwright/mcp@0.0.78 --headless", ("-y", "@playwright/mcp@0.0.78", "--headless")),
        ("", ()),
    ],
)
def test_playwright_mcp_args_are_parsed_and_an_empty_value_means_no_args(raw: str, expected: tuple[str, ...]) -> None:
    assert _browser(PLAYWRIGHT_MCP_ARGS=raw).launch_args == expected


def test_playwright_mcp_command_and_timeout_are_read() -> None:
    settings = _browser(PLAYWRIGHT_MCP_COMMAND=" node ", PLAYWRIGHT_MCP_TIMEOUT_S="0", BROWSER_TIMEOUT_S="45")

    assert (settings.mcp_command, settings.mcp_timeout_s) == ("node", 45)
    assert _browser(PLAYWRIGHT_MCP_COMMAND=" ").mcp_command == "npx"
    assert _browser(PLAYWRIGHT_MCP_TIMEOUT_S="30", BROWSER_TIMEOUT_S="45").mcp_timeout_s == 30
    assert _browser(PLAYWRIGHT_MCP_TIMEOUT_S="soon").mcp_timeout_s == 600


@pytest.mark.parametrize(
    "key",
    ["PLAYWRIGHT_RUNTIME_MCP_CWD", "BROWSER_RUNTIME_MCP_CWD", "PLAYWRIGHT_RUNTIME_WORKDIR", "BROWSER_RUNTIME_WORKDIR"],
)
def test_mcp_cwd_is_read_from_each_name(key: str, tmp_path: Path) -> None:
    assert _browser(**{key: str(tmp_path)}).mcp_cwd == tmp_path.resolve()


def test_mcp_cwd_names_have_a_fixed_precedence(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"

    settings = _browser(PLAYWRIGHT_RUNTIME_WORKDIR=str(second), BROWSER_RUNTIME_MCP_CWD=str(first))

    assert settings.mcp_cwd == first.resolve()


def test_playwright_mcp_browser_device_and_extension_are_read() -> None:
    settings = _browser(
        PLAYWRIGHT_MCP_BROWSER="msedge",
        PLAYWRIGHT_MCP_DEVICE="iPhone 15",
        PLAYWRIGHT_MCP_EXTENSION="TRUE",
        PLAYWRIGHT_MCP_EXTENSION_TOKEN=" tok ",
    )

    assert (settings.mcp_browser, settings.mcp_device) == ("msedge", "iPhone 15")
    assert (settings.mcp_extension, settings.mcp_extension_token) == (True, "tok")
    assert _browser(PLAYWRIGHT_MCP_EXTENSION="nope").mcp_extension is False


def test_mcp_env_passes_proxies_through_and_adds_the_json_entries() -> None:
    settings = _browser(
        PLAYWRIGHT_BROWSERS_PATH="C:/pw",
        HTTP_PROXY="http://proxy:1",
        HTTPS_PROXY="",
        NO_PROXY="localhost",
        PLAYWRIGHT_MCP_ENV_JSON='{"NO_PROXY": "*", "DEBUG": 1}',
    )

    assert dict(settings.mcp_env) == {
        "PLAYWRIGHT_BROWSERS_PATH": "C:/pw",
        "HTTP_PROXY": "http://proxy:1",
        "NO_PROXY": "*",
        "DEBUG": "1",
    }


def test_mcp_env_json_that_is_not_an_object_is_ignored() -> None:
    assert dict(_browser(PLAYWRIGHT_MCP_ENV_JSON="[1, 2]").mcp_env) == {}


def test_invalid_mcp_env_json_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="Invalid PLAYWRIGHT_MCP_ENV_JSON"):
        _browser(PLAYWRIGHT_MCP_ENV_JSON="{not json")


def test_mcp_env_cannot_be_changed() -> None:
    with pytest.raises(TypeError):
        _browser(HTTP_PROXY="http://p").mcp_env["HTTP_PROXY"] = "x"  # type: ignore[index]


def test_upload_root_sidecar_python_and_selector_cache_are_read(tmp_path: Path) -> None:
    sidecar = tmp_path / "python.exe"
    settings = _browser(
        BROWSER_UPLOAD_ROOT=f" {tmp_path} ",
        BROWSER_USE_SIDECAR_PYTHON=str(sidecar),
        OPENJIUWEN_BROWSER_SELECTOR_CACHE="~/selectors.json",
    )

    assert settings.upload_root == tmp_path.resolve()
    assert settings.sidecar_python == sidecar
    assert settings.selector_cache_path == Path("~/selectors.json").expanduser()
    assert (_browser(BROWSER_UPLOAD_ROOT=" ").upload_root, _browser(BROWSER_USE_SIDECAR_PYTHON="").sidecar_python) == (
        None,
        None,
    )


def test_from_env_reads_only_the_mapping_it_is_given() -> None:
    conflicting = {
        "BROWSER_DRIVER": "managed",
        "BROWSER_PROVISIONING": "nonsense",
        "BROWSER_MANAGED_PORT": "not-a-port",
        "PLAYWRIGHT_MCP_ARGS": "--from-process",
        "BROWSER_USE_SIDECAR_PYTHON": "C:/process/python.exe",
    }
    with patch.dict(os.environ, conflicting, clear=True), warnings.catch_warnings():
        warnings.simplefilter("error")
        settings = BrowserSettings.from_env({"BROWSER_MANAGED_PORT": "9444"})

    assert settings.provisioning == "remote"
    assert settings.managed_port == 9444
    assert settings.launch_args == ("-y", "@playwright/mcp@0.0.78")
    assert settings.sidecar_python is None


def test_from_process_env_reads_the_process_environment() -> None:
    with patch.dict(os.environ, {"BROWSER_PROVISIONING": "managed", "BROWSER_MANAGED_PORT": "9444"}, clear=True):
        settings = BrowserSettings.from_process_env()

    assert (settings.provisioning, settings.managed_port) == ("managed", 9444)


@pytest.mark.skipif(os.name != "nt", reason="Chrome install roots come from the environment only on Windows")
def test_chrome_install_paths_follow_the_windows_install_roots_in_order() -> None:
    settings = BrowserSettings.from_env({"ProgramW6432": "D:/w64", "LOCALAPPDATA": "C:/la", "ProgramFiles": "C:/pf"})

    tail = ("Google", "Chrome", "Application", "chrome.exe")
    assert settings.browser_binary_candidates == tuple(str(Path(root, *tail)) for root in ("C:/la", "C:/pf", "D:/w64"))


@pytest.mark.skipif(os.name != "nt", reason="Chrome install roots come from the environment only on Windows")
def test_chrome_install_paths_are_found_in_an_upper_case_snapshot() -> None:
    settings = BrowserSettings.from_env({"PROGRAMFILES": "C:/pf"})

    assert settings.browser_binary_candidates == (str(Path("C:/pf", "Google", "Chrome", "Application", "chrome.exe")),)


@pytest.mark.skipif(os.name == "nt", reason="outside Windows no install path comes from the environment")
def test_no_chrome_install_paths_come_from_the_environment_outside_windows() -> None:
    assert BrowserSettings.from_env({"ProgramFiles": "C:/pf"}).browser_binary_candidates == ()


def test_chrome_install_paths_default_to_none() -> None:
    assert BrowserSettings().browser_binary_candidates == ()


def _instance(**fields: Any) -> BrowserInstanceConfig:
    return BrowserInstanceConfig(**fields)


def test_an_empty_instance_leaves_the_settings_alone() -> None:
    settings = _browser(BROWSER_PROVISIONING="managed", BROWSER_MANAGED_PORT="9444")

    assert settings.with_instance(_instance()) == settings


def test_a_keyed_instance_takes_its_own_profile_and_a_free_port() -> None:
    settings = _browser(BROWSER_MANAGED_PORT="9333").with_instance(_instance(key="usd/sgd", driver_mode="managed"))

    assert (settings.provisioning, settings.profile_name, settings.managed_port) == ("managed", "usd-sgd", 0)


def test_distinct_keys_get_distinct_profiles_and_ports() -> None:
    base = _browser()
    with patch.object(managed_browser, "_allocate_free_port", side_effect=[40001, 40002]):
        first = managed_browser._build_profile(base.with_instance(_instance(key="A", provisioning="managed")))
        second = managed_browser._build_profile(base.with_instance(_instance(key="B", provisioning="managed")))

    assert (first.debug_port, second.debug_port) == (40001, 40002)
    assert first.user_data_dir != second.user_data_dir


def test_an_explicit_instance_port_and_profile_name_win() -> None:
    settings = _browser().with_instance(_instance(key="pinned", managed_port=9501, profile_name="custom"))

    assert (settings.managed_port, settings.profile_name) == (9501, "custom")


def test_an_unkeyed_instance_keeps_the_environment_port_and_profile() -> None:
    settings = _browser(BROWSER_MANAGED_PORT="9444").with_instance(_instance(provisioning="managed"))

    assert (settings.managed_port, settings.profile_name) == (9444, "jiuwenclaw")


def test_instance_endpoint_paths_binary_and_launch_args_win() -> None:
    settings = _browser(PLAYWRIGHT_MCP_CDP_ENDPOINT="http://env:9222").with_instance(
        _instance(
            provisioning="remote",
            cdp_url="http://host:9222",
            user_data_dir="C:/profiles/a",
            browser_binary="C:/chrome/chrome.exe",
            launch_args="--headless --isolated",
        )
    )

    assert settings.cdp_url == "http://host:9222"
    assert (settings.user_data_dir, settings.browser_binary) == ("C:/profiles/a", "C:/chrome/chrome.exe")
    assert settings.launch_args == ("--headless", "--isolated")


def test_an_unknown_instance_provisioning_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="provisioning must be one of: remote, managed, extension"):
        _browser().with_instance(_instance(provisioning="cloud"))
