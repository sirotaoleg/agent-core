# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``build_agent_parts`` and ``build_agent``: factory tools, core rails before the profile's, one construction path."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    DriverInfo,
    Effect,
    Observe,
    PolicyRule,
    RefResolver,
    Registry,
    RunScript,
    SurfaceBundle,
    SurfaceProfile,
    ToolSpec,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import (
    Registries,
    assemble_driver,
    build_agent,
    build_agent_parts,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.intercepted_action import CommandInterceptedHintRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.repeated_call_guard import CommandRepeatGuardRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolContext
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

CORE = CoreSettings(driver="fake", surface="toy")
DEVICE = ProvisionedDevice(kind="fake-device", endpoint="fake://1", extras={})
MESSAGES = {"driver_restarted": "restarted; observe again", "driver_lost": "the device is gone"}


def _script(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return RunScript(str(args["code"]))


def _look(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe()


class ProfileRail(AgentRail):
    def __init__(self, context: ToolContext) -> None:
        super().__init__()
        self.context = context


@dataclass(frozen=True)
class ToySettings:
    provisioning: str = "toy"


RULE = PolicyRule(RunScript, lambda action, context: None)
PROFILE = SurfaceProfile(
    name="toy",
    commands=(),
    renderer=BrowserRenderer(),
    tool_specs=(
        ToolSpec("toy_look", (Observe,), "Look.", schema={"type": "object"}, build=_look, prompt_section="Look first."),
        ToolSpec("toy_script", (RunScript,), "Run a script.", schema={"type": "object"}, build=_script),
    ),
    capability_catalog={"core": frozenset({(Observe, None), (RunScript, None)})},
    capability_tools={"core": ("toy_look", "toy_script")},
    rails=(ProfileRail,),
    policy_rules=(RULE,),
    timeouts=dict.fromkeys(Effect, 5.0),
    messages=MESSAGES,
    settings=ToySettings(),
)
BUNDLE = SurfaceBundle(PROFILE.settings, PROFILE)


class ToyProvisioner:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    def provision(self, surface_settings: Any) -> ProvisionedDevice:
        self.log.append(("provision", surface_settings))
        return DEVICE

    def release(self, device: ProvisionedDevice) -> None:
        self.log.append(("release", device))

    def is_alive(self, device: ProvisionedDevice) -> bool:
        return True


def _registries(driver: FakeDriver, provisioner: ToyProvisioner) -> Registries:
    surfaces: Registry[SurfaceBundle] = Registry("surface")
    surfaces.register("toy", lambda environ: BUNDLE)
    drivers: Registry[Any] = Registry("driver")
    drivers.register("fake", lambda device, settings: driver)
    provisioners: Registry[Any] = Registry("provisioner")
    provisioners.register("toy", lambda: provisioner)
    return Registries(drivers=drivers, surfaces=surfaces, provisioners=provisioners)


def _toy_driver() -> FakeDriver:
    return FakeDriver(info=DriverInfo(name="fake", surfaces=frozenset({"toy"}), actions={RunScript: frozenset()}))


def _session() -> DriverSession:
    assembly = asyncio.run(assemble_driver(CORE, {}, registries=_registries(_toy_driver(), ToyProvisioner())))
    return DriverSession(assembly, BrowserPageState(), MESSAGES)


def _parts(session: DriverSession, **options: Any):
    values: dict[str, Any] = {"capabilities": ("core",), "image_support": False, "language": "en"}
    values.update(options)
    return build_agent_parts(session, BUNDLE, **values)


def test_parts_hold_the_factory_tools_and_prompt_sections() -> None:
    parts = _parts(_session())

    assert [tool.card.name for tool in parts.tools] == ["toy_look", "toy_script"]
    assert parts.prompt_sections == ("Look first.",)


def test_the_core_rails_come_first_and_the_profile_rails_last() -> None:
    parts = _parts(_session())

    assert [type(rail) for rail in parts.rails] == [
        ActionPolicy,
        CommandRepeatGuardRail,
        CommandInterceptedHintRail,
        ProfileRail,
    ]


def test_the_policy_rail_decodes_with_the_factory_builders_and_checks_the_profile_rules() -> None:
    session = _session()
    parts = _parts(session)
    policy = parts.rails[0]

    assert set(policy._builders) == {"toy_look", "toy_script"}
    assert policy._rules == (RULE,)
    assert policy._page_state is session.page_state


def test_profile_rails_receive_the_tool_context_of_the_session() -> None:
    session = _session()
    parts = _parts(session, workspace="/tmp/ws", image_support=True)
    context = parts.rails[-1].context

    assert (context.driver, context.page_state) == (session, session.page_state)
    assert (context.settings, context.renderer) == (PROFILE.settings, PROFILE.renderer)
    assert (context.workspace, context.language, context.image_support) == ("/tmp/ws", "en", True)


class _Captured:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.error = error

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return "agent"


def _build(create: _Captured, driver: FakeDriver, provisioner: ToyProvisioner, **options: Any) -> Any:
    async def run() -> Any:
        with patch.object(assembly_module, "create_deep_agent", create):
            return await build_agent(
                CORE,
                {},
                model="model",
                capabilities=("core",),
                image_support=False,
                language="en",
                registries=_registries(driver, provisioner),
                **options,
            )

    return asyncio.run(run())


def test_build_agent_hands_the_parts_to_create_deep_agent_over_a_started_session() -> None:
    create = _Captured()
    driver = _toy_driver()

    agent, session = _build(
        create, driver, ToyProvisioner(), system_prompt="You poke screens.", workspace="/tmp/ws", max_iterations=7
    )

    ((args, kwargs),) = create.calls
    assert agent == "agent"
    assert args == ("model",)
    assert kwargs["system_prompt"] == "You poke screens.\n\nLook first."
    assert [tool.card.name for tool in kwargs["tools"]] == ["toy_look", "toy_script"]
    assert [type(rail) for rail in kwargs["rails"]][:3] == [
        ActionPolicy,
        CommandRepeatGuardRail,
        CommandInterceptedHintRail,
    ]
    assert (kwargs["workspace"], kwargs["language"], kwargs["max_iterations"]) == ("/tmp/ws", "en", 7)
    assert session.assembly.stack.inner.inner.inner is driver
    assert driver.kinds() == ["start"]
    assert kwargs["rails"][0]._page_state is session.page_state


def test_a_failing_agent_construction_stops_the_driver_and_releases_the_device() -> None:
    driver = _toy_driver()
    provisioner = ToyProvisioner()

    with pytest.raises(RuntimeError, match="no agent"):
        _build(_Captured(RuntimeError("no agent")), driver, provisioner)

    assert driver.kinds() == ["start", "stop"]
    assert provisioner.log[-1] == ("release", DEVICE)
