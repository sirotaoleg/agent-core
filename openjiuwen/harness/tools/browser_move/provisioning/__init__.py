# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Provisioning: start or attach the device a driver connects to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional


@dataclass(frozen=True)
class ProvisionedDevice:
    """A device made ready by a provisioner (for the browser: a Chrome with a CDP endpoint)."""

    kind: str
    endpoint: str
    extras: Mapping[str, Any]
    cleanup: Optional[Callable]


__all__ = ["ProvisionedDevice"]
