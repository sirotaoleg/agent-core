# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The core page state moves to a new generation when the surface context's identity changes, for any surface."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    ContextRef,
    Observation,
    PressKey,
    SurfaceContext,
)
from openjiuwen.harness.tools.browser_move.runtime.page_state import PageState
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

SCREEN = ContextRef("screen-1", "screen", "Board")


@dataclass(frozen=True)
class Spot(SurfaceContext):
    activity: str

    @property
    def identity(self) -> str:
        return self.activity


def _moved_to(state: PageState, activity: str) -> str:
    state.record(PressKey("Enter"), ActionResult(ok=True, surface=Spot(activity)))
    return state.generation_id


def test_the_base_context_has_no_identity() -> None:
    assert SurfaceContext().identity == ""


def test_the_browser_context_identity_is_its_address() -> None:
    assert BrowserContext(url="https://a.test/").identity == "https://a.test/"


def test_a_new_identity_starts_a_new_generation_on_a_non_browser_surface() -> None:
    state = PageState()

    assert _moved_to(state, "settings") == "g0"
    assert _moved_to(state, "settings") == "g0"
    assert _moved_to(state, "wifi") == "g1"
    assert state.identity == "wifi"


def test_an_empty_identity_never_moves_the_generation() -> None:
    state = PageState()
    _moved_to(state, "settings")

    assert _moved_to(state, "") == "g0"
    assert state.identity == "settings"


def test_an_observation_carries_its_surface_identity_too() -> None:
    state = PageState()
    state.record(PressKey("Enter"), ActionResult(ok=True, surface=Spot("home")))

    state.record(PressKey("Enter"), ActionResult(ok=True, observation=Observation(context=SCREEN, surface=Spot("app"))))

    assert state.generation_id == "g1"
