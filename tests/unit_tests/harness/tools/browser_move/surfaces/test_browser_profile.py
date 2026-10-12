# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser surface profile: fields, timeouts, messages and the capability catalog."""

from __future__ import annotations

import re

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    Click,
    CloseContext,
    DriverRef,
    Effect,
    Locator,
    Observe,
    PointRef,
    RunScript,
    Scroll,
    SurfaceProfile,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.surfaces import SURFACES
from openjiuwen.harness.tools.browser_move.surfaces.browser import commands
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import (
    CORE_BROWSER_TOOL_NAMES,
    DEFAULT_BROWSER_CAPABILITIES,
    POLICY_ONLY_BROWSER_CAPABILITY_NAMES,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Drop, GetPdf, OpenContext, UploadFile
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers import (
    EXTRA_TOOLS,
    activate_page,
    page_metadata,
    probe_for_policy,
    register_custom_actions,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.policy import POLICY_RULES
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer
from openjiuwen.harness.tools.browser_move.surfaces.browser.semantic.state import SemanticStateTracker
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import (
    BROWSER_TOOL_SPECS,
    build_browser_worker_system_prompt,
)

DRIVER_NAME_RE = re.compile(r"playwright_mcp|browser_use|browser-use|\bmcp\b", re.IGNORECASE)


@pytest.fixture(name="profile")
def _profile() -> SurfaceProfile:
    return SURFACES.create("browser", {}).profile


def test_registered_factory_returns_settings_and_profile() -> None:
    bundle = SURFACES.create("browser", {})
    assert isinstance(bundle.settings, BrowserSettings)
    assert bundle.profile.settings is bundle.settings


def test_profile_fields_are_filled(profile: SurfaceProfile) -> None:
    assert profile.name == "browser"
    assert isinstance(profile.renderer, BrowserRenderer)
    assert profile.tool_specs == BROWSER_TOOL_SPECS
    assert set(profile.capability_catalog) == set(profile.capability_tools)
    assert profile.policy_rules[: len(POLICY_RULES)] == POLICY_RULES and profile.rails == ()
    assert [rule.command_type for rule in profile.policy_rules[len(POLICY_RULES) :]] == [UploadFile, Drop]
    assert profile.extra_tools == EXTRA_TOOLS
    assert not hasattr(profile, "native_tools")


def test_profile_lists_every_browser_owned_command(profile: SurfaceProfile) -> None:
    owned = {
        value
        for value in vars(commands).values()
        if isinstance(value, type) and issubclass(value, Action) and value.__module__ == commands.__name__
    }
    assert set(profile.commands) == owned


def test_every_effect_gets_the_same_timeout(profile: SurfaceProfile) -> None:
    assert dict(profile.timeouts) == {effect: 240.0 for effect in Effect}


def test_messages_the_core_needs_are_present(profile: SurfaceProfile) -> None:
    assert profile.messages["driver_restarted"] == "the browser restarted; take a fresh observation"
    assert profile.messages["driver_lost"]
    assert profile.messages["context_changed"].format(generation="g4") == (
        "Call browser_snapshot again and use refs from generation g4."
    )


def test_profile_names_no_driver(profile: SurfaceProfile) -> None:
    texts = [*profile.messages.values(), *profile.capability_tools]
    texts += [spec.name + spec.description + str(spec.schema) for spec in profile.tool_specs]
    texts += [name for names in profile.capability_tools.values() for name in names]
    assert not [text for text in texts if DRIVER_NAME_RE.search(text)]


def test_catalog_entries_are_the_public_capabilities(profile: SurfaceProfile) -> None:
    assert list(profile.capability_tools) == [capability.name for capability in DEFAULT_BROWSER_CAPABILITIES]


def test_core_entry_round_trips_the_public_core_tool_names(profile: SurfaceProfile) -> None:
    assert profile.capability_tools["core"] == CORE_BROWSER_TOOL_NAMES


def test_catalog_keeps_only_tools_a_command_or_extra_tool_serves(profile: SurfaceProfile) -> None:
    assert profile.capability_tools["vision"] == (
        "browser_mouse_click_xy",
        "browser_mouse_drag_xy",
        "browser_mouse_move_xy",
        "browser_mouse_wheel",
    )
    assert profile.capability_tools["extended_interaction"] == ("browser_close", "browser_drag", "browser_drop")
    assert profile.capability_tools["pdf"] == ("browser_pdf_save",)
    assert profile.capability_tools["devtools"] == ("browser_console_messages",)
    assert profile.capability_tools["network"] == ("browser_network_requests",)
    for entry in ("advanced_code", "unsafe_dev", "config", "storage", "testing"):
        assert profile.capability_tools[entry] == ()
        assert profile.capability_catalog[entry] == frozenset()


def test_every_catalog_name_but_find_has_a_tool_spec_and_every_spec_an_entry(profile: SurfaceProfile) -> None:
    spec_names = {spec.name for spec in profile.tool_specs}
    names = {name for names in profile.capability_tools.values() for name in names}
    assert names - spec_names == {"browser_find"}
    assert spec_names <= names


def test_core_pairs_take_element_refs_and_no_points(profile: SurfaceProfile) -> None:
    core = profile.capability_catalog["core"]
    assert {(Click, DriverRef), (Click, Locator), (RunScript, None), (RunScript, DriverRef)} <= core
    assert {(UploadFile, None), (UploadFile, Locator), (Observe, None), (OpenContext, None)} <= core
    assert {(SwitchContext, None), (CloseContext, None)} <= core
    assert not [pair for pair in core if pair[1] is PointRef]


def test_vision_pairs_take_points(profile: SurfaceProfile) -> None:
    vision = profile.capability_catalog["vision"]
    assert (Click, PointRef) in vision and (Scroll, None) in vision and (Observe, None) not in vision
    assert (Click, DriverRef) not in vision


def test_pdf_entry_needs_the_pdf_command(profile: SurfaceProfile) -> None:
    assert profile.capability_catalog["pdf"] == frozenset({(GetPdf, None)})


def test_profile_carries_the_catalog_the_hooks_and_the_worker_prompt(profile: SurfaceProfile) -> None:
    assert profile.capabilities == DEFAULT_BROWSER_CAPABILITIES
    assert profile.capabilities[0].name == "core" and profile.capabilities[0].tool_names == CORE_BROWSER_TOOL_NAMES
    policy_only = tuple(entry.name for entry in profile.capabilities if entry.policy_only)
    assert policy_only == POLICY_ONLY_BROWSER_CAPABILITY_NAMES
    assert policy_only == ("advanced_code", "extended_interaction", "unsafe_dev")
    assert profile.custom_actions is register_custom_actions
    assert profile.page_metadata is page_metadata
    assert profile.policy_probe is probe_for_policy
    assert profile.activate is activate_page
    assert isinstance(profile.semantic_tracker(), SemanticStateTracker)
    assert profile.semantic_tracker() is not profile.semantic_tracker()
    assert profile.worker_prompt is build_browser_worker_system_prompt
