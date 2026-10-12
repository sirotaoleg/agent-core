# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The composition root: builds the driver stack and the agent from registry names only."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent

from ..contract import ConnectionLost, Driver, Registry, SurfaceBundle, SurfaceProfile
from ..drivers import DRIVERS
from ..drivers.middleware import RetryOnStale, Timeouts, Tracing
from ..provisioning import PROVISIONERS, ProvisionedDevice, Provisioner
from ..surfaces import SURFACES
from .config import CoreSettings
from .driver_session import DriverSession
from .page_state import BrowserPageState
from .rails.action_policy import ActionPolicy
from .rails.intercepted_action import InterceptedActionHintRail
from .rails.repeated_call_guard import RepeatedCallGuardRail
from .tool_factory import Builder, ToolContext, ToolFactory


@dataclass(frozen=True)
class Registries:
    drivers: Registry[Driver]
    surfaces: Registry[SurfaceBundle]
    provisioners: Registry[Provisioner]


@dataclass(frozen=True)
class DriverAssembly:
    """A started driver stack, the device under it and the surface it serves.

    ``rebuild`` makes a new stack for another device of the same surface.
    """

    stack: Driver
    device: ProvisionedDevice
    provisioner: Provisioner
    rebuild: Callable[[ProvisionedDevice], Driver] | None = field(default=None, compare=False)
    surface: SurfaceBundle | None = None

    async def close(self) -> None:
        try:
            await self.stack.stop()
        finally:
            await asyncio.to_thread(self.provisioner.release, self.device)


@dataclass(frozen=True)
class AgentParts:
    tools: tuple[Tool, ...]
    prompt_sections: tuple[str, ...]
    rails: tuple[AgentRail, ...]
    builders: Mapping[str, Builder] = field(default_factory=dict)


def _log_trace(record: dict[str, Any]) -> None:
    logger.debug("driver trace: %s", record)


def _stack_builder(core: CoreSettings, surface: SurfaceBundle, drivers: Registry[Driver]):
    def build(device: ProvisionedDevice) -> Driver:
        driver = drivers.create(core.driver, device, surface.settings)
        if core.surface not in driver.info.surfaces:
            served = ", ".join(sorted(driver.info.surfaces)) or "none"
            raise ValueError(f"driver {core.driver!r} does not serve surface {core.surface!r} (it serves: {served})")
        return Tracing(RetryOnStale(Timeouts(driver, surface.profile.timeouts)), _log_trace)

    return build


def assemble_surface(
    core: CoreSettings,
    environ: Mapping[str, str],
    *,
    instance: Any = None,
    settings: Any = None,
    registries: Registries | None = None,
) -> SurfaceBundle:
    """The surface by name from ``core``; ``instance`` is its per-agent identity, ``settings`` replaces ``environ``."""
    registries = registries or Registries(DRIVERS, SURFACES, PROVISIONERS)
    return registries.surfaces.create(core.surface, environ, instance, settings)


async def assemble_driver(
    core: CoreSettings,
    environ: Mapping[str, str],
    *,
    instance: Any = None,
    registries: Registries | None = None,
    settings: Any = None,
) -> DriverAssembly:
    """Surface, provisioner, driver and middleware by name from ``core``; returns the started stack.

    The surface factory gets ``instance`` (the surface's own per-agent identity) and ``settings`` (used instead of
    parsing ``environ``), so both reach provisioning, the driver factory and the tool context.
    """
    registries = registries or Registries(DRIVERS, SURFACES, PROVISIONERS)
    surface = assemble_surface(core, environ, instance=instance, settings=settings, registries=registries)
    provisioner = registries.provisioners.create(surface.settings.provisioning)
    build_stack = _stack_builder(core, surface, registries.drivers)
    device = await asyncio.to_thread(provisioner.provision, surface.settings)
    try:
        stack = build_stack(device)
    except BaseException:
        await asyncio.to_thread(provisioner.release, device)
        raise
    assembly = DriverAssembly(stack, device, provisioner, build_stack, surface)
    try:
        await stack.start()
    except BaseException:
        await assembly.close()
        raise
    return assembly


async def open_session(
    core: CoreSettings,
    environ: Mapping[str, str],
    *,
    page_state: BrowserPageState,
    instance: Any = None,
    settings: Any = None,
    registries: Registries | None = None,
) -> DriverSession:
    """A session over a started stack; a first start that loses its connection is provisioned once more."""
    try:
        assembly = await assemble_driver(core, environ, instance=instance, registries=registries, settings=settings)
    except ConnectionLost:
        assembly = await assemble_driver(core, environ, instance=instance, registries=registries, settings=settings)
    return DriverSession(assembly, page_state, assembly.surface.profile.messages)


def core_rails(
    profile: SurfaceProfile, builders: Mapping[str, Builder], page_state: BrowserPageState
) -> tuple[AgentRail, ...]:
    """The policy, the repeated-call guard and the intercepted-action hint, over the live ``builders`` mapping."""
    return (
        ActionPolicy(builders, profile.policy_rules, page_state),
        RepeatedCallGuardRail(),
        InterceptedActionHintRail(profile.messages["retry"]),
    )


def build_agent_parts(
    session: DriverSession,
    bundle: SurfaceBundle,
    *,
    capabilities: Iterable[str],
    image_support: bool,
    language: str,
    workspace: Any = None,
    worker: bool = False,
) -> AgentParts:
    """Tools and prompt sections from the profile and the driver, and the profile's rails."""
    profile = bundle.profile
    info = session.assembly.stack.info
    tools = ToolFactory(profile, info).build(
        capabilities,
        image_support=image_support,
        session=session,
        page_state=session.page_state,
        workspace=workspace,
        language=language,
        worker=worker,
    )
    context = ToolContext(
        session, session.page_state, profile.renderer, info, profile.settings, image_support, workspace, language
    )
    rails = tuple(factory(context) for factory in profile.rails)
    return AgentParts(tools.tools, tools.prompt_sections, rails, tools.builders)


async def build_agent(
    core: CoreSettings,
    environ: Mapping[str, str],
    *,
    model: Model,
    capabilities: Iterable[str],
    image_support: bool,
    language: str,
    system_prompt: str = "",
    workspace: Any = None,
    instance: Any = None,
    registries: Registries | None = None,
    **agent_options: Any,
) -> tuple[DeepAgent, DriverSession]:
    """A deep agent over a started driver session; the caller closes the session when the agent is done."""
    assembly = await assemble_driver(core, environ, instance=instance, registries=registries)
    session = DriverSession(assembly, BrowserPageState(), assembly.surface.profile.messages)
    try:
        parts = build_agent_parts(
            session,
            assembly.surface,
            capabilities=capabilities,
            image_support=image_support,
            language=language,
            workspace=workspace,
        )
        agent = create_deep_agent(
            model,
            system_prompt="\n\n".join(text for text in (system_prompt, *parts.prompt_sections) if text),
            tools=list(parts.tools),
            rails=[*core_rails(assembly.surface.profile, parts.builders, session.page_state), *parts.rails],
            workspace=workspace,
            language=language,
            **agent_options,
        )
    except BaseException:
        await session.close()
        raise
    return agent, session


__all__ = [
    "AgentParts",
    "DriverAssembly",
    "Registries",
    "assemble_driver",
    "assemble_surface",
    "build_agent",
    "build_agent_parts",
    "core_rails",
    "open_session",
]
