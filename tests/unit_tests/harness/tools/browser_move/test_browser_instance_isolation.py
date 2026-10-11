#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for per-agent browser isolation (different browsers for different agents).

Covers the BrowserInstanceConfig identity key flowing through BrowserService
(identity) and the browser surface factory (profile / provisioning / port /
user-data-dir), and the teams manifest element that carries that identity
across the spawn-wire boundary as serializable kwargs.

These tests never launch Chrome: they exercise only config resolution and
profile assembly, with the free-port allocator mocked for determinism.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import (
    ManagedBrowserProvisioner,
    _build_profile,
)
from openjiuwen.harness.tools.browser_move.runtime.config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
    build_playwright_mcp_config,
)
from openjiuwen.harness.tools.browser_move.runtime.profiles import BrowserProfile, BrowserProfileStore
from openjiuwen.harness.tools.browser_move.runtime.service import BrowserService
from openjiuwen.harness.tools.browser_move.surfaces import SURFACES

_ALLOCATE_FREE_PORT = "openjiuwen.harness.tools.browser_move.provisioning.managed_browser._allocate_free_port"


def _store() -> BrowserProfileStore:
    return BrowserProfileStore(Path(os.environ["BROWSER_PROFILE_STORE_PATH"]))


def _seed_live_profile(store: BrowserProfileStore, name: str, *, port: int = 63668) -> None:
    """Persist a managed profile and mark it the global selected one."""
    store.upsert_profile(
        BrowserProfile(
            name=name,
            driver_type="managed",
            cdp_url=f"http://127.0.0.1:{port}",
            user_data_dir=str(Path.cwd()),
            debug_port=port,
        ),
        select=True,
    )


def _profile(instance: BrowserInstanceConfig | None) -> BrowserProfile:
    """The managed profile the provisioner builds from the surface settings with ``instance`` applied."""
    return _build_profile(SURFACES.create("browser", os.environ, instance).settings)


def _make_service(instance: BrowserInstanceConfig | None) -> BrowserService:
    return BrowserService(
        provider="openai",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
        model_name="mock-model",
        mcp_cfg=build_playwright_mcp_config(instance),
        guardrails=BrowserRunGuardrails(),
        instance=instance,
    )


def _isolated_env():
    """Clear browser env + point the profile store at a throwaway path."""
    return patch.dict(
        os.environ,
        {"BROWSER_PROFILE_STORE_PATH": tempfile.mktemp(suffix=".json")},
        clear=True,
    )


def test_distinct_keys_get_distinct_profiles_and_ports() -> None:
    with _isolated_env(), patch(_ALLOCATE_FREE_PORT, side_effect=[40001, 40002]):
        a = _make_service(BrowserInstanceConfig(key="A", driver_mode="managed"))
        b = _make_service(BrowserInstanceConfig(key="B", driver_mode="managed"))

        assert a._profile_name == "A"
        assert b._profile_name == "B"
        assert a._driver_mode == "managed"

        pa = _profile(BrowserInstanceConfig(key="A", driver_mode="managed"))
        pb = _profile(BrowserInstanceConfig(key="B", driver_mode="managed"))

    assert pa.debug_port == 40001
    assert pb.debug_port == 40002
    assert pa.debug_port != pb.debug_port
    assert pa.user_data_dir != pb.user_data_dir


def test_explicit_port_is_honored_without_allocation() -> None:
    with (
        _isolated_env(),
        patch(_ALLOCATE_FREE_PORT, side_effect=AssertionError("must not allocate")),
    ):
        profile = _profile(BrowserInstanceConfig(key="pinned", driver_mode="managed", managed_port=9501))

    assert profile.debug_port == 9501


def test_instance_profile_name_overrides_key() -> None:
    with _isolated_env(), patch(_ALLOCATE_FREE_PORT, return_value=40010):
        svc = _make_service(BrowserInstanceConfig(key="raw", driver_mode="managed", profile_name="custom-profile"))
        assert svc._profile_name == "custom-profile"


def test_instance_driver_mode_overrides_env() -> None:
    with patch.dict(
        os.environ,
        {"BROWSER_DRIVER": "remote", "BROWSER_PROFILE_STORE_PATH": tempfile.mktemp(suffix=".json")},
        clear=True,
    ):
        svc = _make_service(BrowserInstanceConfig(key="x", driver_mode="managed"))
        assert svc._driver_mode == "managed"


def test_legacy_no_instance_preserves_env_defaults() -> None:
    with _isolated_env():
        svc = _make_service(None)
        assert svc._profile_name == "jiuwenclaw"
        profile = _profile(None)

    assert profile.debug_port == 9333


def test_keyed_auto_allocation_ignores_global_env_port() -> None:
    """A keyed instance must not inherit a shared env port (would re-collide)."""
    with (
        patch.dict(
            os.environ,
            {"BROWSER_MANAGED_PORT": "9333", "BROWSER_PROFILE_STORE_PATH": tempfile.mktemp(suffix=".json")},
            clear=True,
        ),
        patch(_ALLOCATE_FREE_PORT, return_value=45000),
    ):
        profile = _profile(BrowserInstanceConfig(key="keyed", driver_mode="managed"))

    assert profile.debug_port == 45000


def test_allocated_port_persists_for_reuse() -> None:
    """The managed profile (with its port) is persisted so a restart reattaches."""
    with _isolated_env(), patch(_ALLOCATE_FREE_PORT, return_value=46000):
        profile = _profile(BrowserInstanceConfig(key="persist", driver_mode="managed"))
        _store().upsert_profile(profile, select=True)

        reloaded = _store().get_profile("persist")

    assert reloaded is not None
    assert reloaded.debug_port == 46000
    assert reloaded.driver_type == "managed"


# ---------------------------------------------------------------------------
# Cross-key reuse guard — the shared store's global selected_profile must not
# let a second keyed instance attach to a first key's live browser.
# ---------------------------------------------------------------------------


def _provisioned(instance: BrowserInstanceConfig) -> BrowserProfile:
    """Provision through the managed provisioner over the store, with Chrome's start stubbed."""
    provisioner = ManagedBrowserProvisioner(profile_store=_store())
    with patch.object(ManagedBrowserProvisioner, "start", return_value="http://127.0.0.1:1"):
        provisioner.provision(SURFACES.create("browser", os.environ, instance).settings)
    return provisioner.profile


def test_keyed_instance_does_not_adopt_another_keys_selected_profile() -> None:
    """Regression: keyed instances must not attach to another key's live Chrome.

    Repro of the observed bug — ``usd-sgd`` starts first and becomes the global
    selected_profile in the shared store; ``sgd-jpy`` must still get its own
    browser instead of reusing usd-sgd's live CDP endpoint.
    """
    with _isolated_env(), patch(_ALLOCATE_FREE_PORT, return_value=40020):
        _seed_live_profile(_store(), "usd-sgd", port=63668)

        profile = _provisioned(BrowserInstanceConfig(key="sgd-jpy", driver_mode="managed"))

    assert (profile.name, profile.debug_port) == ("sgd-jpy", 40020)


def test_keyed_instance_reuses_its_own_live_profile() -> None:
    """Same key == intentional sharing: reuse its own profile when CDP is live."""
    with _isolated_env(), patch(_ALLOCATE_FREE_PORT, side_effect=AssertionError("must not allocate")):
        _seed_live_profile(_store(), "usd-sgd", port=63668)

        profile = _provisioned(BrowserInstanceConfig(key="usd-sgd", driver_mode="managed"))

    assert (profile.name, profile.debug_port) == ("usd-sgd", 63668)


# ---------------------------------------------------------------------------
# Teams manifest plumbing — serializable browser identity across the wire
# ---------------------------------------------------------------------------


def _browser_input(**overrides):
    base = dict(
        browser_key="",
        browser_port=0,
        browser_profile="",
        browser_driver="",
        browser_cdp_url="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_browser_instance_dict_none_when_no_identity() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    assert _browser_instance_dict(_browser_input()) is None


def test_browser_instance_dict_defaults_to_managed() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    out = _browser_instance_dict(_browser_input(browser_key="A"))
    assert out == {"key": "A", "driver_mode": "managed"}


def test_browser_instance_dict_remote_when_cdp_url() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    out = _browser_instance_dict(_browser_input(browser_cdp_url="http://host:9222"))
    assert out["driver_mode"] == "remote"
    assert out["cdp_url"] == "http://host:9222"


def test_distinct_teammate_keys_yield_distinct_dicts() -> None:
    from openjiuwen.agent_teams.rails.subagent_elements import _browser_instance_dict

    a = _browser_instance_dict(_browser_input(browser_key="A", browser_port=9501))
    b = _browser_instance_dict(_browser_input(browser_key="B", browser_port=9502))
    assert a != b
    assert a["managed_port"] == 9501 and b["managed_port"] == 9502
