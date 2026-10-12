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

_PKG = __name__.rsplit(".", 1)[0]
# Modules that left runtime/ but have a consumer outside the repo, keyed by their old name under this package.
_MOVED = {
    "browser_capabilities": "surfaces.browser.capabilities",
    "browser_state_context_processor": "surfaces.browser.state_processor",
    "browser_working_context": "surfaces.browser.working_context.store",
    "browser_working_context_processor": "surfaces.browser.working_context.processor",
    "browser_working_context_rail": "surfaces.browser.rails.working_context",
    "probe_semantics": "surfaces.browser.probes.semantics",
    "probes": "surfaces.browser.probes",
    "profiles": "provisioning.profiles",
    "runtime": "runtime.session",
    "semantic_state": "surfaces.browser.semantic.state",
    "site_profiles": "surfaces.browser.site_profiles",
    "status_logging": "surfaces.browser.status_logging",
}
# Names runtime.__all__ used to export lazily, now served from their new modules.
_MOVED_NAMES = {
    "BrowserStateContextProcessor": "surfaces.browser.state_processor",
    "BrowserStateContextProcessorConfig": "surfaces.browser.state_processor",
    "BrowserWorkingContextProcessor": "surfaces.browser.working_context.processor",
    "BrowserWorkingContextProcessorConfig": "surfaces.browser.working_context.processor",
    "BrowserWorkingContextRail": "surfaces.browser.rails.working_context",
}

# An import statement resolves through sys.modules, so every module is aliased up front; a lazy
# proxy would not be the same object as the module under runtime/.
for _info in pkgutil.walk_packages(_runtime.__path__, prefix=f"{_runtime.__name__}."):
    sys.modules[_info.name.replace(_runtime.__name__, __name__, 1)] = import_module(_info.name)
for _old, _new in _MOVED.items():
    sys.modules[f"{__name__}.{_old}"] = import_module(f"{_PKG}.{_new}")

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
    if name in _MOVED_NAMES:
        return getattr(import_module(f"{_PKG}.{_MOVED_NAMES[name]}"), name)
    return getattr(_runtime, name)
