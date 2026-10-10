# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``assemble_driver`` with fake registries, and the built-in registrations it uses by default."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    ActionTimeout,
    Click,
    ConnectionLost,
    ContextRef,
    DriverInfo,
    DriverRef,
    Effect,
    Observation,
    Observe,
    Registry,
    RunScript,
    SurfaceBundle,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.drivers import DRIVERS
from openjiuwen.harness.tools.browser_move.drivers.middleware import RetryOnStale, Timeouts, Tracing
from openjiuwen.harness.tools.browser_move.playwright_runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import (
    DriverAssembly,
    Registries,
    assemble_driver,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.provisioning import PROVISIONERS, ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces import SURFACES
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings

ENVIRON = {"FAKE_SURFACE_FLAG": "on"}
CORE = CoreSettings(driver="fake_driver", surface="fake")
DEVICE = ProvisionedDevice(kind="fake-device", endpoint="fake://1", extras={})
OTHER_DEVICE = ProvisionedDevice(kind="fake-device", endpoint="fake://2", extras={})


@dataclass(frozen=True)
class FakeSettings:
    provisioning: str = "fake_provisioning"


class FakeProvisioner:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log

    def provision(self, surface_settings: Any) -> ProvisionedDevice:
        self.log.append(("provision", surface_settings))
        return DEVICE

    def release(self, device: ProvisionedDevice) -> None:
        self.log.append(("release", device))


class FailingStartDriver(FakeDriver):
    async def start(self, config: Any = None) -> None:
        await super().start(config)
        raise ConnectionLost("no device", dispatched=False)


class FailingStopDriver(FakeDriver):
    async def stop(self) -> None:
        await super().stop()
        raise ConnectionLost("gone", dispatched=True)


class HangingDriver(FakeDriver):
    async def observe(self, command: Observe) -> Observation:
        await asyncio.sleep(10)
        raise AssertionError("not reached")

    async def execute(self, action: Action) -> ActionResult:
        await asyncio.sleep(10)
        raise AssertionError("not reached")


def _fake_info(*surfaces: str) -> DriverInfo:
    return DriverInfo(name="fake_driver", surfaces=frozenset(surfaces or {"fake"}))


class World:
    """Fake registries that record every factory and provisioner call in one log."""

    def __init__(self, driver: FakeDriver | None = None, *, driver_error: BaseException | None = None) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.settings = FakeSettings()
        self.driver = driver if driver is not None else FakeDriver(info=_fake_info())
        self.provisioner = FakeProvisioner(self.log)
        self._driver_error = driver_error
        surfaces: Registry[SurfaceBundle] = Registry("surface")
        surfaces.register("fake", self._surface)
        provisioners: Registry[Any] = Registry("provisioner")
        for name in ("fake_provisioning", "other_provisioning"):
            provisioners.register(name, self._provisioner_factory(name))
        drivers: Registry[Any] = Registry("driver")
        drivers.register("fake_driver", self._driver)
        self.registries = Registries(drivers=drivers, surfaces=surfaces, provisioners=provisioners)

    def _surface(self, environ: Any) -> SurfaceBundle:
        self.log.append(("surface", environ))
        return SurfaceBundle(settings=self.settings)

    def _provisioner_factory(self, name: str):
        def create() -> FakeProvisioner:
            self.log.append(("provisioner", name))
            return self.provisioner

        return create

    def _driver(self, device: ProvisionedDevice, surface_settings: Any) -> FakeDriver:
        self.log.append(("driver", device, surface_settings))
        if self._driver_error is not None:
            raise self._driver_error
        return self.driver

    def assemble(self, core: CoreSettings = CORE) -> DriverAssembly:
        return asyncio.run(assemble_driver(core, ENVIRON, registries=self.registries))


class TestAssembleDriver:
    def test_builds_surface_provisioner_device_and_driver_in_order_then_starts(self) -> None:
        world = World()

        assembly = world.assemble()

        assert world.log == [
            ("surface", ENVIRON),
            ("provisioner", "fake_provisioning"),
            ("provision", world.settings),
            ("driver", DEVICE, world.settings),
        ]
        assert world.driver.kinds() == ["start"]
        assert (assembly.device, assembly.provisioner) == (DEVICE, world.provisioner)

    def test_stack_is_tracing_over_stale_retry_over_timeouts_over_the_driver(self) -> None:
        world = World()

        stack = world.assemble().stack

        assert type(stack) is Tracing
        assert type(stack.inner) is RetryOnStale
        assert type(stack.inner.inner) is Timeouts
        assert stack.inner.inner.inner is world.driver

    def test_rebuild_makes_a_new_stack_from_the_driver_factory_for_another_device(self) -> None:
        world = World()
        assembly = world.assemble()
        rebuilt_driver = FakeDriver(info=_fake_info())
        world.driver = rebuilt_driver

        stack = assembly.rebuild(OTHER_DEVICE)

        assert world.log[-1] == ("driver", OTHER_DEVICE, world.settings)
        assert type(stack) is Tracing
        assert stack.inner.inner.inner is rebuilt_driver
        assert rebuilt_driver.kinds() == []

    def test_rebuild_refuses_a_driver_that_does_not_serve_the_surface(self) -> None:
        world = World()
        assembly = world.assemble()
        world.driver = FakeDriver(info=_fake_info("phone"))

        with pytest.raises(ValueError, match="does not serve surface 'fake'"):
            assembly.rebuild(OTHER_DEVICE)

    def test_driver_that_does_not_serve_the_surface_is_refused_and_the_device_released(self) -> None:
        world = World(FakeDriver(info=_fake_info("desktop", "phone")))

        with pytest.raises(
            ValueError, match=r"driver 'fake_driver' does not serve surface 'fake' \(it serves: desktop, phone\)"
        ):
            world.assemble()

        assert world.log[-1] == ("release", DEVICE)
        assert world.driver.kinds() == []

    def test_failing_driver_factory_releases_the_device(self) -> None:
        world = World(driver_error=RuntimeError("factory broke"))

        with pytest.raises(RuntimeError, match="factory broke"):
            world.assemble()

        assert world.log[-1] == ("release", DEVICE)

    def test_failing_start_stops_the_driver_and_releases_the_device(self) -> None:
        world = World(FailingStartDriver(info=_fake_info()))

        with pytest.raises(ConnectionLost, match="no device"):
            world.assemble()

        assert world.driver.kinds() == ["start", "stop"]
        assert world.log[-1] == ("release", DEVICE)

    def test_failing_provision_creates_no_driver(self) -> None:
        world = World()

        def provision(surface_settings: Any) -> ProvisionedDevice:
            raise RuntimeError("no chrome")

        world.provisioner.provision = provision

        with pytest.raises(RuntimeError, match="no chrome"):
            world.assemble()

        assert [entry[0] for entry in world.log] == ["surface", "provisioner"]

    @pytest.mark.parametrize(
        "command",
        [Observe(), Click(DriverRef("e1", "g1")), SwitchContext(ContextRef("w2", "window")), RunScript("1")],
        ids=lambda command: command.effect.value,
    )
    def test_every_effect_is_bounded_by_the_tool_timeout(self, command: Action) -> None:
        world = World(HangingDriver(info=_fake_info()))

        async def run() -> None:
            core = CoreSettings(driver="fake_driver", surface="fake", tool_timeout_s=0.05)
            stack = (await assemble_driver(core, ENVIRON, registries=world.registries)).stack
            if isinstance(command, Observe):
                await stack.observe(command)
            else:
                await stack.execute(command)

        with pytest.raises(ActionTimeout) as caught:
            asyncio.run(run())

        assert caught.value.dispatched is True

    def test_the_timeout_cases_cover_every_effect(self) -> None:
        commands = (Observe(), Click(DriverRef("e1", "g1")), SwitchContext(ContextRef("w2", "window")), RunScript("1"))
        assert {command.effect for command in commands} == set(Effect)

    def test_trace_records_go_to_the_debug_log(self) -> None:
        world = World(FakeDriver([Observation(context=ContextRef("c1", "screen"))], info=_fake_info()))

        async def run() -> None:
            stack = (await assemble_driver(CORE, ENVIRON, registries=world.registries)).stack
            await stack.observe(Observe())

        with patch.object(assembly_module.logger, "debug") as debug:
            asyncio.run(run())

        (record,) = [call.args[1] for call in debug.call_args_list]
        assert (record["call"], record["command"], record["context"]) == ("observe", "Observe", "c1")


class TestDriverAssemblyClose:
    def test_close_stops_the_driver_and_releases_the_device(self) -> None:
        world = World()
        assembly = world.assemble()

        asyncio.run(assembly.close())

        assert world.driver.kinds() == ["start", "stop"]
        assert world.log[-1] == ("release", DEVICE)

    def test_close_releases_the_device_when_stop_fails(self) -> None:
        world = World(FailingStopDriver(info=_fake_info()))
        assembly = world.assemble()

        with pytest.raises(ConnectionLost, match="gone"):
            asyncio.run(assembly.close())

        assert world.log[-1] == ("release", DEVICE)


class TestBuiltInRegistrations:
    def test_drivers_register_browser_use_and_playwright_mcp(self) -> None:
        assert {"browser_use", "playwright_mcp"} <= set(DRIVERS.names())

    def test_playwright_mcp_driver_is_built_from_its_registry_name(self) -> None:
        driver = DRIVERS.create("playwright_mcp", DEVICE, BrowserSettings())

        assert type(driver).__name__ == "PlaywrightMcpDriver"
        assert driver.info.surfaces == frozenset({"browser"})

    def test_provisioners_register_managed_remote_and_extension(self) -> None:
        assert {"managed", "remote", "extension"} <= set(PROVISIONERS.names())

    def test_browser_surface_returns_its_settings_and_its_profile(self) -> None:
        bundle = SURFACES.create("browser", {"BROWSER_PROVISIONING": "managed", "BROWSER_MANAGED_PORT": "9444"})

        assert isinstance(bundle, SurfaceBundle)
        assert bundle.settings == BrowserSettings.from_env(
            {"BROWSER_PROVISIONING": "managed", "BROWSER_MANAGED_PORT": "9444"}
        )
        assert bundle.profile.name == "browser"
        assert bundle.profile.settings is bundle.settings

    def test_default_registries_reach_the_built_in_names(self) -> None:
        assert {"playwright_mcp", "browser_use"} <= set(DRIVERS.names())
        assert "browser" in SURFACES.names()


_CONCRETE_MODULES_PROBE = """
import json, sys
import openjiuwen.harness.tools.browser_move
after_package = sorted(sys.modules)
import openjiuwen.harness.tools.browser_move.playwright_runtime.assembly
print(json.dumps({"package": after_package, "assembly": sorted(sys.modules)}))
"""

_CONCRETE_PREFIXES = (
    "browser_use",
    "openjiuwen.harness.tools.browser_move.drivers.browser_use",
    "openjiuwen.harness.tools.browser_move.drivers.playwright_mcp",
    "openjiuwen.harness.tools.browser_move.surfaces.browser",
    "openjiuwen.harness.tools.browser_move.provisioning.managed_browser",
)


def test_importing_the_package_and_the_assembly_loads_no_concrete_driver_surface_or_provisioner() -> None:
    completed = subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", _CONCRETE_MODULES_PROBE], capture_output=True, text=True, check=True, timeout=120
    )
    loaded = json.loads(completed.stdout.strip().splitlines()[-1])

    for stage in ("package", "assembly"):
        concrete = [
            name for name in loaded[stage] if any(name == p or name.startswith(p + ".") for p in _CONCRETE_PREFIXES)
        ]
        assert concrete == [], f"importing the {stage} loaded {concrete}"
