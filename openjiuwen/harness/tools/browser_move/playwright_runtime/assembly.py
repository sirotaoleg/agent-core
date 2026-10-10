# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The composition root: builds the driver stack from registry names only."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.logging import logger

from ..contract import Driver, Effect, Registry, SurfaceBundle
from ..drivers import DRIVERS
from ..drivers.middleware import RetryOnStale, Timeouts, Tracing
from ..provisioning import PROVISIONERS, ProvisionedDevice, Provisioner
from ..surfaces import SURFACES
from .config import CoreSettings


@dataclass(frozen=True)
class Registries:
    drivers: Registry[Driver]
    surfaces: Registry[SurfaceBundle]
    provisioners: Registry[Provisioner]


@dataclass(frozen=True)
class DriverAssembly:
    """A started driver stack, the device under it and the settings that device was provisioned from.

    ``rebuild`` makes a new stack for another device of the same surface.
    """

    stack: Driver
    device: ProvisionedDevice
    provisioner: Provisioner
    surface_settings: Any
    rebuild: Callable[[ProvisionedDevice], Driver] | None = field(default=None, compare=False)

    async def close(self) -> None:
        try:
            await self.stack.stop()
        finally:
            await asyncio.to_thread(self.provisioner.release, self.device)


def _log_trace(record: dict[str, Any]) -> None:
    logger.debug("driver trace: %s", record)


def _stack_builder(core: CoreSettings, surface: SurfaceBundle, drivers: Registry[Driver]):
    def build(device: ProvisionedDevice) -> Driver:
        driver = drivers.create(core.driver, device, surface.settings)
        if core.surface not in driver.info.surfaces:
            served = ", ".join(sorted(driver.info.surfaces)) or "none"
            raise ValueError(f"driver {core.driver!r} does not serve surface {core.surface!r} (it serves: {served})")
        return Tracing(RetryOnStale(Timeouts(driver, dict.fromkeys(Effect, core.tool_timeout_s))), _log_trace)

    return build


async def assemble_driver(
    core: CoreSettings,
    environ: Mapping[str, str],
    *,
    registries: Registries | None = None,
) -> DriverAssembly:
    """Surface, provisioner, driver and middleware by name from ``core``; returns the started stack."""
    registries = registries or Registries(DRIVERS, SURFACES, PROVISIONERS)
    surface = registries.surfaces.create(core.surface, environ)
    provisioner = registries.provisioners.create(surface.settings.provisioning)
    build_stack = _stack_builder(core, surface, registries.drivers)
    device = await asyncio.to_thread(provisioner.provision, surface.settings)
    try:
        stack = build_stack(device)
    except BaseException:
        await asyncio.to_thread(provisioner.release, device)
        raise
    assembly = DriverAssembly(stack, device, provisioner, surface.settings, build_stack)
    try:
        await stack.start()
    except BaseException:
        await assembly.close()
        raise
    return assembly


__all__ = ["DriverAssembly", "Registries", "assemble_driver"]
