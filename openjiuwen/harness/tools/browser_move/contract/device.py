# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProvisionedDevice:
    """A device made ready by a provisioner, with the endpoint its driver connects to."""

    kind: str
    endpoint: str
    extras: Mapping[str, Any]
    cleanup: Callable[[], None] | None = None
