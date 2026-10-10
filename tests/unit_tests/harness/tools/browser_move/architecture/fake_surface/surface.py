# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A screen surface written from outside the package: its command, renderer, policy, driver and provisioner."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    Click,
    ContentBlock,
    ContextRef,
    DriverError,
    DriverInfo,
    Effect,
    HandlerDriver,
    Health,
    Observation,
    Observe,
    PointRef,
    PolicyRule,
    RefResolver,
    Registry,
    Screenshot,
    SurfaceBundle,
    SurfaceContext,
    SurfaceProfile,
    ToolSpec,
    handles,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import Registries
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice

BOARD = 100
SCREEN = ContextRef("screen-1", "screen", "Board")
DEVICE = ProvisionedDevice(kind="fake-screen", endpoint="screen://1", extras={})
MESSAGES = {"driver_restarted": "the screen restarted; look again", "driver_lost": "the screen is gone"}


@dataclass(frozen=True)
class Poke(Action):
    """Poke the screen at a point."""

    effect: ClassVar[Effect] = Effect.ACT

    target: PointRef


@dataclass(frozen=True)
class ScreenSettings:
    provisioning: str = "screen"


class ScreenRenderer:
    def observation(self, observation: Observation) -> tuple[ContentBlock, ...]:
        return (f"screen {observation.context.id}", observation.screenshot)

    def result(self, action: Action, result: ActionResult) -> tuple[ContentBlock, ...]:
        if isinstance(action, Observe):
            return (result.value,)
        return (f"{type(action).__name__} done",)

    def error(self, action: Action, error: DriverError, observation: Observation | None) -> tuple[ContentBlock, ...]:
        text = f"### Error\n{type(error).__name__}: {error}"
        if observation is not None:
            text += f"\nfresh look at {observation.context.id} in {observation.generation}"
        return (text,)


def _on_the_board(action: Action, context: SurfaceContext | None) -> str | None:
    target = action.target
    return None if 0 <= target.x < BOARD and 0 <= target.y < BOARD else f"({target.x}, {target.y}) is off the board"


def _look(args: Mapping[str, Any], refs: RefResolver) -> Action:
    return Observe(include_screenshot=True)


PROFILE = SurfaceProfile(
    name="fake",
    commands=(Poke,),
    renderer=ScreenRenderer(),
    tool_specs=(
        ToolSpec("look", (Observe,), "Look at the screen.", schema={"type": "object", "properties": {}}, build=_look),
        ToolSpec("click", (Click,), "Click a point of the screen."),
        ToolSpec("poke", (Poke,), "Poke a point of the screen."),
    ),
    capability_catalog={"core": frozenset({(Observe, None), (Click, PointRef), (Poke, PointRef)})},
    capability_tools={"core": ("look", "click", "poke")},
    policy_rules=(PolicyRule(Poke, _on_the_board),),
    timeouts=dict.fromkeys(Effect, 5.0),
    messages=MESSAGES,
    settings=ScreenSettings(),
)


class ScreenDriver(HandlerDriver):
    """Screenshot-only observations; each command answers with the next scripted outcome."""

    def __init__(self, outcomes: Iterable[ActionResult | DriverError]) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[str | Action] = []
        self._shots = 0

    def describe(self) -> DriverInfo:
        return DriverInfo(name="screen_driver", surfaces=frozenset({"fake"}))

    async def start(self, config: Any = None) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")

    async def health(self) -> Health:
        return Health(ok=True)

    async def observe(self, command: Observe) -> Observation:
        self.calls.append(command)
        self._shots += 1
        return Observation(context=SCREEN, screenshot=Screenshot(b"\x89PNG-%d" % self._shots, BOARD, BOARD))

    @handles(Click, refs={PointRef})
    async def _click(self, action: Click) -> ActionResult:
        return self._next(action)

    @handles(Poke, refs={PointRef})
    async def _poke(self, action: Poke) -> ActionResult:
        return self._next(action)

    def _next(self, action: Action) -> ActionResult:
        self.calls.append(action)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, DriverError):
            raise outcome
        return outcome


class ScreenProvisioner:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    def provision(self, surface_settings: Any) -> ProvisionedDevice:
        self.log.append(("provision", surface_settings))
        return DEVICE

    def release(self, device: ProvisionedDevice) -> None:
        self.log.append(("release", device))

    def is_alive(self, device: ProvisionedDevice) -> bool:
        return True


def screen_registries(driver: ScreenDriver, provisioner: ScreenProvisioner) -> Registries:
    """Test-scoped registries: the screen surface, its driver and its provisioner by name."""
    surfaces: Registry[SurfaceBundle] = Registry("surface")
    surfaces.register("fake", lambda environ: SurfaceBundle(PROFILE.settings, PROFILE))
    drivers: Registry[Any] = Registry("driver")
    drivers.register("screen_driver", lambda device, settings: driver)
    provisioners: Registry[Any] = Registry("provisioner")
    provisioners.register("screen", lambda: provisioner)
    return Registries(drivers=drivers, surfaces=surfaces, provisioners=provisioners)
