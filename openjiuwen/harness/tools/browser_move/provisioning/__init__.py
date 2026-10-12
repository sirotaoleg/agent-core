# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Provisioning: start or attach the device a driver connects to."""

from __future__ import annotations

from typing import Any, Protocol

from ..contract.device import ProvisionedDevice
from ..contract.registry import PROVISIONERS, register_provisioner


class Provisioner(Protocol):
    """Makes one device ready from the surface settings and releases it."""

    def provision(self, surface_settings: Any) -> ProvisionedDevice: ...

    def release(self, device: ProvisionedDevice) -> None: ...

    def is_alive(self, device: ProvisionedDevice) -> bool:
        """Whether the device still answers; a dead one is released and provisioned again."""


for _name in ("managed", "remote", "extension"):
    register_provisioner(_name, f"{__name__}.managed_browser:ManagedBrowserProvisioner")


__all__ = ["PROVISIONERS", "ProvisionedDevice", "Provisioner"]
