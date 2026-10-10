# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What the browser surface hands to the core."""

from __future__ import annotations

from collections.abc import Mapping

from ...contract.surface import SurfaceBundle
from .config import BrowserSettings


def create_browser_surface(environ: Mapping[str, str]) -> SurfaceBundle:
    """Surface factory (registry signature)."""
    return SurfaceBundle(settings=BrowserSettings.from_env(environ))
