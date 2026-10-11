# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deprecated alias of ``openjiuwen.harness.tools.browser_move.runtime``; removed in the next release."""

from __future__ import annotations

import pkgutil
import sys
import warnings
from importlib import import_module
from typing import Any

from openjiuwen.harness.tools.browser_move import runtime as _runtime

# An import statement resolves through sys.modules, so every module is aliased up front; a lazy
# proxy would not be the same object as the module under runtime/.
for _info in pkgutil.walk_packages(_runtime.__path__, prefix=f"{_runtime.__name__}."):
    sys.modules[_info.name.replace(_runtime.__name__, __name__, 1)] = import_module(_info.name)

__all__ = _runtime.__all__

# importlib.reload re-runs this body in the same namespace; the flag keeps the warning to one per process.
if not globals().get("_warned"):
    warnings.warn(
        f"{__name__} is deprecated and will be removed in the next release; import {_runtime.__name__}",
        DeprecationWarning,
        stacklevel=2,
    )
    _warned = True


def __getattr__(name: str) -> Any:
    return getattr(_runtime, name)
