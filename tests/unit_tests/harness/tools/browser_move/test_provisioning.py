#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the provisioning package: the provisioner rename and ProvisionedDevice."""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice, managed_browser
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import ManagedBrowserProvisioner


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


def test_provisioned_device_cleanup_may_be_none() -> None:
    device = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9222", extras={}, cleanup=None)

    assert device.cleanup is None
