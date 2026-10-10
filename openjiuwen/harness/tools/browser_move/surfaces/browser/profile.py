# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What the browser surface hands to the core."""

from __future__ import annotations

from collections.abc import Mapping

from ...contract import Effect
from ...contract.surface import SurfaceBundle, SurfaceProfile
from .capabilities import CAPABILITY_CATALOG, CAPABILITY_TOOLS
from .commands import (
    Back,
    Drop,
    FillForm,
    GetConsole,
    GetNetwork,
    GetPdf,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)
from .config import BrowserSettings
from .renderer import BrowserRenderer
from .tools import BROWSER_TOOL_SPECS

_COMMANDS = (Navigate, Back, OpenContext, SelectOption, UploadFile, FillForm, Drop, GetPdf, GetConsole, GetNetwork)
_EFFECT_TIMEOUT_S = 240.0
MESSAGES: Mapping[str, str] = {
    "driver_restarted": "the browser restarted; take a fresh observation",
    "driver_lost": "the browser connection was lost again; the task cannot continue",
    "context_changed": "Call browser_snapshot again and use refs from generation {generation}.",
}


def create_browser_surface(environ: Mapping[str, str]) -> SurfaceBundle:
    """Surface factory (registry signature)."""
    settings = BrowserSettings.from_env(environ)
    profile = SurfaceProfile(
        name="browser",
        commands=_COMMANDS,
        renderer=BrowserRenderer(),
        tool_specs=BROWSER_TOOL_SPECS,
        capability_catalog=CAPABILITY_CATALOG,
        capability_tools=CAPABILITY_TOOLS,
        timeouts={effect: _EFFECT_TIMEOUT_S for effect in Effect},
        messages=MESSAGES,
        settings=settings,
    )
    return SurfaceBundle(settings=settings, profile=profile)
