# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The driver contract: commands, observations, results, errors, the driver base and the registries.

Imports only the standard library and itself.
"""

from .actions import (
    Action,
    Click,
    CloseContext,
    Drag,
    Effect,
    HandleDialog,
    Hover,
    Observe,
    PressKey,
    RunScript,
    Scroll,
    SwitchContext,
    TypeText,
    Wait,
)
from .driver import Driver, DriverInfo, HandlerDriver, Health, ensure_supported, handles, iter_refs
from .errors import (
    ActionIntercepted,
    ActionTimeout,
    AmbiguousElement,
    ConnectionLost,
    DriverError,
    ElementNotFound,
    NavigationError,
    PolicyDenied,
    StaleRef,
    Unsupported,
)
from .observation import Element, Observation, Screenshot, SurfaceContext, Viewport
from .refs import REF_KINDS, ContextRef, DriverRef, IndexRef, Locator, PointRef, Ref
from .registry import (
    DRIVERS,
    PROVISIONERS,
    SURFACES,
    Registry,
    register_driver,
    register_provisioner,
    register_surface,
)
from .results import ActionResult
from .surface import PolicyRule, Scene, SurfaceProfile, ToolSpec

__all__ = [
    "DRIVERS",
    "PROVISIONERS",
    "REF_KINDS",
    "SURFACES",
    "Action",
    "ActionIntercepted",
    "ActionResult",
    "ActionTimeout",
    "AmbiguousElement",
    "Click",
    "CloseContext",
    "ConnectionLost",
    "ContextRef",
    "Drag",
    "Driver",
    "DriverError",
    "DriverInfo",
    "DriverRef",
    "Effect",
    "Element",
    "ElementNotFound",
    "HandleDialog",
    "HandlerDriver",
    "Health",
    "Hover",
    "IndexRef",
    "Locator",
    "NavigationError",
    "Observation",
    "Observe",
    "PointRef",
    "PolicyDenied",
    "PolicyRule",
    "PressKey",
    "Ref",
    "Registry",
    "RunScript",
    "Scene",
    "Screenshot",
    "Scroll",
    "StaleRef",
    "SurfaceContext",
    "SurfaceProfile",
    "SwitchContext",
    "ToolSpec",
    "TypeText",
    "Unsupported",
    "Viewport",
    "Wait",
    "ensure_supported",
    "handles",
    "iter_refs",
    "register_driver",
    "register_provisioner",
    "register_surface",
]
