# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deprecated alias of ``surfaces.browser.offload_recall``; removed in the next release."""

from __future__ import annotations

import sys
import warnings

from .surfaces.browser import offload_recall as _moved

warnings.warn(
    f"{__name__} is deprecated and will be removed in the next release; import {_moved.__name__}",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _moved
