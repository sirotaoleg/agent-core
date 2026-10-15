# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Surface packages, one per kind of screen; the built-in ones are registered by name with lazy import strings."""

from ..contract.registry import SURFACES, register_surface

DEFAULT_SURFACE = "browser"

register_surface(DEFAULT_SURFACE, f"{__name__}.browser.profile:create_browser_surface")

__all__ = ["DEFAULT_SURFACE", "SURFACES"]
