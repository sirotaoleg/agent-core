# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Built-in drivers, registered by name with lazy import strings."""

from ..contract.registry import DRIVERS, register_driver

register_driver("browser_use", f"{__name__}.browser_use.driver:create_browser_use_driver")
register_driver("playwright_mcp", f"{__name__}.playwright_mcp.driver:create_playwright_mcp_driver")

__all__ = ["DRIVERS"]
