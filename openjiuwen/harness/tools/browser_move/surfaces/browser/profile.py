# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What the browser surface hands to the core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...contract import Effect
from ...contract.surface import SurfaceBundle, SurfaceProfile
from .capabilities import CAPABILITY_CATALOG, CAPABILITY_TOOLS, DEFAULT_BROWSER_CAPABILITIES
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
from .helpers import EXTRA_TOOLS, activate_page, page_metadata, probe_for_policy, register_custom_actions
from .policy import POLICY_RULES
from .renderer import BrowserRenderer
from .semantic.state import SemanticStateTracker
from .tools import BROWSER_TOOL_SPECS, build_browser_worker_system_prompt

if TYPE_CHECKING:
    from ...runtime.config import BrowserInstanceConfig

_COMMANDS = (Navigate, Back, OpenContext, SelectOption, UploadFile, FillForm, Drop, GetPdf, GetConsole, GetNetwork)
_EFFECT_TIMEOUT_S = 240.0
MESSAGES: Mapping[str, str] = {
    "driver_restarted": "the browser restarted; take a fresh observation",
    "driver_lost": "the browser connection was lost again; the task cannot continue",
    "context_changed": "Call browser_snapshot again and use refs from generation {generation}.",
    "retry": "Then retry the action (take a fresh browser_snapshot if the page changed).",
}


def create_browser_surface(
    environ: Mapping[str, str], instance: BrowserInstanceConfig | None = None, settings: BrowserSettings | None = None
) -> SurfaceBundle:
    """Surface factory (registry signature): ``settings`` replaces parsing ``environ``; ``instance`` is applied last."""
    settings = BrowserSettings.from_env(environ) if settings is None else settings
    if instance is not None:
        settings = settings.with_instance(instance)
    profile = SurfaceProfile(
        name="browser",
        commands=_COMMANDS,
        renderer=BrowserRenderer(),
        tool_specs=BROWSER_TOOL_SPECS,
        capability_catalog=CAPABILITY_CATALOG,
        capability_tools=CAPABILITY_TOOLS,
        extra_tools=EXTRA_TOOLS,
        policy_rules=POLICY_RULES,
        timeouts={effect: _EFFECT_TIMEOUT_S for effect in Effect},
        messages=MESSAGES,
        settings=settings,
        capabilities=DEFAULT_BROWSER_CAPABILITIES,
        custom_actions=register_custom_actions,
        page_metadata=page_metadata,
        policy_probe=probe_for_policy,
        activate=activate_page,
        semantic_tracker=SemanticStateTracker,
        worker_prompt=build_browser_worker_system_prompt,
    )
    return SurfaceBundle(settings=settings, profile=profile)
