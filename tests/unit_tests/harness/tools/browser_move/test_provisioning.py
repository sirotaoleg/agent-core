#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the provisioning package: registrations, ManagedBrowserProvisioner, ProvisionedDevice."""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.harness.tools.browser_move.contract.registry import PROVISIONERS
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice, managed_browser
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import ManagedBrowserProvisioner
from openjiuwen.harness.tools.browser_move.provisioning.profiles import BrowserProfile, BrowserProfileStore
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings

_ENDPOINT = "http://127.0.0.1:9444"


def _managed(tmp_path: Path, **overrides) -> BrowserSettings:
    values = {"provisioning": "managed", "managed_port": 9444, "profile_name": "p", "mcp_cwd": tmp_path}
    return BrowserSettings(**{**values, **overrides})


def _provision(settings: BrowserSettings, store: BrowserProfileStore | None = None):
    provisioner = ManagedBrowserProvisioner(profile_store=store)
    with patch.object(ManagedBrowserProvisioner, "start", return_value=_ENDPOINT) as start:
        device = provisioner.provision(settings)
    return provisioner, device, start


def test_old_class_name_warns_and_is_the_same_class() -> None:
    with pytest.warns(DeprecationWarning, match="ManagedBrowserProvisioner"):
        alias = managed_browser.ManagedBrowserDriver

    assert alias is ManagedBrowserProvisioner


def test_old_class_name_import_warns() -> None:
    with pytest.warns(DeprecationWarning):
        from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import ManagedBrowserDriver

    assert ManagedBrowserDriver is ManagedBrowserProvisioner


def test_new_class_name_does_not_warn(recwarn: pytest.WarningsRecorder) -> None:
    assert managed_browser.ManagedBrowserProvisioner is ManagedBrowserProvisioner
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_unknown_module_attribute_still_raises() -> None:
    with pytest.raises(AttributeError):
        getattr(managed_browser, "NoSuchName")


def test_old_module_path_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("openjiuwen.harness.tools.browser_move.drivers.managed_browser")


def test_provisioned_device_holds_fields_and_is_frozen() -> None:
    cleanup_calls = []
    device = ProvisionedDevice(
        kind="chrome",
        endpoint="http://127.0.0.1:9222",
        extras={"port": 9222},
        cleanup=lambda: cleanup_calls.append(True),
    )

    assert (device.kind, device.endpoint, dict(device.extras)) == ("chrome", "http://127.0.0.1:9222", {"port": 9222})
    device.cleanup()
    assert cleanup_calls == [True]
    with pytest.raises(dataclasses.FrozenInstanceError):
        device.endpoint = "http://127.0.0.1:9333"  # type: ignore[misc]


def test_provisioned_device_cleanup_defaults_to_none() -> None:
    device = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9222", extras={})

    assert device.cleanup is None


@pytest.mark.parametrize("name", ["managed", "remote", "extension"])
def test_builtin_provisioning_names_are_registered(name: str) -> None:
    assert isinstance(PROVISIONERS.create(name), ManagedBrowserProvisioner)


def test_remote_hands_over_the_cdp_url_without_starting_chrome() -> None:
    settings = BrowserSettings(
        provisioning="remote", cdp_url="http://10.0.0.5:9222", cdp_headers='{"a": "b"}', cdp_timeout="5000"
    )
    with patch.object(ManagedBrowserProvisioner, "start", side_effect=AssertionError("must not start")):
        device = ManagedBrowserProvisioner().provision(settings)

    assert (device.kind, device.endpoint, device.cleanup) == ("chrome", "http://10.0.0.5:9222", None)
    assert device.extras["cdp_headers"] == '{"a": "b"}'
    assert device.extras["cdp_timeout"] == "5000"
    assert device.extras["launch_args"] == settings.launch_args


def test_extension_has_no_endpoint_and_carries_the_token() -> None:
    settings = BrowserSettings(provisioning="extension", mcp_extension_token="tok")
    with patch.object(ManagedBrowserProvisioner, "start", side_effect=AssertionError("must not start")):
        device = ManagedBrowserProvisioner().provision(settings)

    assert (device.kind, device.endpoint, device.cleanup) == ("chrome", "", None)
    assert device.extras["extension"] is True
    assert device.extras["extension_token"] == "tok"


def test_managed_builds_a_profile_starts_chrome_and_persists_the_endpoint(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path / "profiles.json")
    settings = _managed(tmp_path, managed_args=("--headless=new",), browser_binary="C:/chrome.exe")

    provisioner, device, start = _provision(settings, store)

    start.assert_called_once_with(30.0, False)
    assert (device.kind, device.endpoint) == ("chrome", _ENDPOINT)
    assert device.extras["port"] == 9444
    assert device.extras["user_data_dir"] == str(tmp_path / ".browser-profiles" / "p")
    assert device.extras["managed_args"] == ("--headless=new",)
    assert device.extras["launch_args"] == settings.launch_args
    assert device.extras["profile"] is provisioner.profile
    assert device.cleanup == provisioner.stop
    stored = BrowserProfileStore(tmp_path / "profiles.json")
    assert stored.selected_profile().name == "p"
    assert (stored.get_profile("p").cdp_url, stored.get_profile("p").browser_binary) == (_ENDPOINT, "C:/chrome.exe")


def test_managed_reuses_a_valid_stored_profile(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path / "profiles.json")
    store.upsert_profile(
        BrowserProfile(
            name="p",
            driver_type="managed",
            browser_binary="C:/old/chrome.exe",
            user_data_dir=str(tmp_path / "kept"),
            debug_port=9555,
        )
    )

    _, device, _ = _provision(_managed(tmp_path), store)

    assert device.extras["port"] == 9555
    assert device.extras["user_data_dir"] == str(tmp_path / "kept")
    assert device.extras["profile"].browser_binary == ""


def test_managed_rebuilds_a_stored_profile_whose_chrome_args_changed(tmp_path: Path) -> None:
    store = BrowserProfileStore(tmp_path / "profiles.json")
    store.upsert_profile(
        BrowserProfile(name="p", driver_type="managed", user_data_dir=str(tmp_path / "old"), debug_port=9555)
    )

    _, device, _ = _provision(_managed(tmp_path, managed_args=("--headless=new",)), store)

    assert device.extras["port"] == 9444
    assert device.extras["managed_args"] == ("--headless=new",)


def test_managed_port_zero_takes_a_free_port(tmp_path: Path) -> None:
    with patch.object(managed_browser, "_allocate_free_port", return_value=45123):
        _, device, _ = _provision(_managed(tmp_path, managed_port=0))

    assert device.extras["port"] == 45123


def test_kill_existing_without_a_user_data_dir_uses_the_default_chrome_profile(tmp_path: Path) -> None:
    with patch.object(managed_browser, "_default_chrome_user_data_dir", return_value=str(tmp_path / "default")):
        _, device, start = _provision(_managed(tmp_path, managed_kill_existing=True))

    start.assert_called_once_with(30.0, True)
    assert device.extras["user_data_dir"] == str(tmp_path / "default")


def test_an_explicit_user_data_dir_wins_over_kill_existing(tmp_path: Path) -> None:
    _, device, _ = _provision(_managed(tmp_path, user_data_dir=str(tmp_path / "mine"), managed_kill_existing=True))

    assert device.extras["user_data_dir"] == str(tmp_path / "mine")


def test_without_a_store_the_profile_store_path_from_the_settings_is_used(tmp_path: Path) -> None:
    _provision(_managed(tmp_path, profile_store_path=tmp_path / "custom.json"))

    assert BrowserProfileStore(tmp_path / "custom.json").get_profile("p") is not None
    assert not (tmp_path / ".browser" / "profiles.json").exists()


def test_without_a_store_or_path_the_store_lives_under_the_mcp_cwd(tmp_path: Path) -> None:
    _provision(_managed(tmp_path))

    assert BrowserProfileStore(tmp_path / ".browser" / "profiles.json").get_profile("p") is not None


def test_release_runs_the_device_cleanup(tmp_path: Path) -> None:
    provisioner, device, _ = _provision(_managed(tmp_path))

    with patch.object(ManagedBrowserProvisioner, "stop") as stop:
        provisioner.release(dataclasses.replace(device, cleanup=stop))

    stop.assert_called_once_with()


def test_release_of_a_device_without_cleanup_does_nothing() -> None:
    provisioner = ManagedBrowserProvisioner()
    device = provisioner.provision(BrowserSettings(provisioning="remote"))

    provisioner.release(device)


class _CdpVersion(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = json.dumps({"Browser": "Chrome/140"}).encode() if self.path == "/json/version" else b""
        self.send_response(200 if body else 404)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def cdp_endpoint() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _CdpVersion)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _closed_endpoint() -> str:
    server = HTTPServer(("127.0.0.1", 0), _CdpVersion)
    server.server_close()
    return f"http://127.0.0.1:{server.server_port}"


def test_a_device_whose_endpoint_answers_cdp_is_alive(cdp_endpoint: str) -> None:
    device = ManagedBrowserProvisioner().provision(BrowserSettings(provisioning="remote", cdp_url=cdp_endpoint))

    assert ManagedBrowserProvisioner().is_alive(device) is True


def test_a_device_whose_endpoint_is_gone_is_dead() -> None:
    device = ManagedBrowserProvisioner().provision(BrowserSettings(provisioning="remote", cdp_url=_closed_endpoint()))

    assert ManagedBrowserProvisioner().is_alive(device) is False


def test_a_managed_device_is_alive_while_its_chrome_answers(tmp_path: Path, cdp_endpoint: str) -> None:
    provisioner = ManagedBrowserProvisioner()
    with patch.object(ManagedBrowserProvisioner, "start", return_value=cdp_endpoint):
        device = provisioner.provision(_managed(tmp_path))

    assert provisioner.is_alive(device) is True
    assert provisioner.is_alive(dataclasses.replace(device, endpoint=_closed_endpoint())) is False


@pytest.mark.parametrize(
    "settings", [BrowserSettings(provisioning="extension"), BrowserSettings(provisioning="remote")]
)
def test_a_device_without_an_endpoint_counts_as_alive(settings: BrowserSettings) -> None:
    provisioner = ManagedBrowserProvisioner()
    device = provisioner.provision(settings)

    with patch.object(managed_browser, "urlopen", side_effect=AssertionError("must not probe")):
        assert provisioner.is_alive(device) is True


@pytest.mark.skipif(os.name != "nt", reason="install paths come from the environment only on Windows")
def test_chrome_lookup_takes_the_existing_install_paths_it_is_given(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"")
    with patch.object(managed_browser.shutil, "which", return_value=None):
        candidates = managed_browser._candidate_chrome_binaries((str(chrome), str(tmp_path / "missing.exe")))

    assert candidates[0] == str(chrome)
    assert str(tmp_path / "missing.exe") not in candidates


def test_provisioned_chrome_lookup_uses_the_settings_candidates(tmp_path: Path) -> None:
    provisioner, _, _ = _provision(_managed(tmp_path, browser_binary_candidates=("C:/from-settings/chrome.exe",)))

    with patch.object(managed_browser, "_candidate_chrome_binaries", return_value=["found"]) as lookup:
        assert provisioner._resolve_binary() == "found"

    lookup.assert_called_once_with(("C:/from-settings/chrome.exe",))
