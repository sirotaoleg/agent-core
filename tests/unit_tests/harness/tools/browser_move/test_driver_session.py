# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``DriverSession`` on ``FakeDriver``: one restart per task, a new stack for a dead device, no re-sent command."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    ConnectionLost,
    DriverRef,
    Observe,
    SurfaceBundle,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState

MESSAGES = {"driver_restarted": "restarted; observe again", "driver_lost": "the device is gone"}
CLICK = Click(DriverRef("e1", "g0"))
FIRST = ProvisionedDevice(kind="fake-device", endpoint="fake://1", extras={})
SECOND = ProvisionedDevice(kind="fake-device", endpoint="fake://2", extras={})


@dataclass(frozen=True)
class FakeSettings:
    provisioning: str = "fake"


class FakeProvisioner:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.log: list[tuple[str, Any]] = []

    def provision(self, surface_settings: Any) -> ProvisionedDevice:
        self.log.append(("provision", surface_settings))
        return SECOND

    def release(self, device: ProvisionedDevice) -> None:
        self.log.append(("release", device))

    def is_alive(self, device: ProvisionedDevice) -> bool:
        return self.alive


class StartFailsDriver(FakeDriver):
    async def start(self, config: Any = None) -> None:
        await super().start(config)
        raise ConnectionLost("no device", dispatched=False)


class Rebuilds:
    """The assembly's ``rebuild``: one new driver per device, from ``make``."""

    def __init__(self, make: Any = FakeDriver) -> None:
        self.make = make
        self.drivers: list[tuple[ProvisionedDevice, FakeDriver]] = []

    def __call__(self, device: ProvisionedDevice) -> FakeDriver:
        driver = self.make()
        self.drivers.append((device, driver))
        return driver


def _session(
    driver: FakeDriver, *, alive: bool = True, rebuild: Any = None
) -> tuple[DriverSession, FakeProvisioner, BrowserPageState]:
    provisioner = FakeProvisioner(alive=alive)
    page_state = BrowserPageState(page_id="page-test")
    assembly = DriverAssembly(driver, FIRST, provisioner, rebuild or Rebuilds(), SurfaceBundle(FakeSettings()))
    return DriverSession(assembly, page_state, MESSAGES), provisioner, page_state


def _execute(session: DriverSession) -> Any:
    return asyncio.run(session.execute(CLICK))


def test_connection_lost_restarts_once_reports_restarted_and_resets_the_page_state() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1, dispatched=True)
    session, provisioner, page_state = _session(driver)

    with pytest.raises(ConnectionLost) as raised:
        _execute(session)

    assert str(raised.value) == "restarted; observe again"
    assert raised.value.dispatched is True
    assert driver.kinds() == ["execute", "stop", "start"]
    assert page_state.generation_id == "g1"
    assert provisioner.log == []


def test_the_failed_command_is_never_sent_again() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1, dispatched=False)
    session, _, _ = _session(driver)

    with pytest.raises(ConnectionLost) as raised:
        _execute(session)

    assert raised.value.dispatched is False
    assert [call.payload for call in driver.calls if call.kind == "execute"] == [CLICK]


def test_a_second_connection_loss_in_the_same_task_fails_the_task() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, _, page_state = _session(driver)
    with pytest.raises(ConnectionLost):
        _execute(session)
    driver.fail_next(ConnectionLost("lost again"))

    with pytest.raises(BaseError, match="the device is gone"):
        _execute(session)

    assert driver.kinds() == ["execute", "stop", "start", "execute"]
    assert page_state.generation_id == "g1"


def test_begin_task_resets_the_loss_counter() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, _, page_state = _session(driver)
    with pytest.raises(ConnectionLost):
        _execute(session)
    driver.fail_next(ConnectionLost("lost in the next task"))

    session.begin_task()
    with pytest.raises(ConnectionLost, match="restarted; observe again"):
        _execute(session)

    assert page_state.generation_id == "g2"


def test_a_dead_device_is_released_provisioned_again_and_gets_a_new_stack() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    rebuilds = Rebuilds()
    session, provisioner, page_state = _session(driver, alive=False, rebuild=rebuilds)

    with pytest.raises(ConnectionLost, match="restarted; observe again"):
        _execute(session)

    ((device, rebuilt),) = rebuilds.drivers
    assert provisioner.log == [("release", FIRST), ("provision", FakeSettings())]
    assert device == SECOND
    assert (session.assembly.device, session.assembly.stack) == (SECOND, rebuilt)
    assert driver.kinds() == ["execute", "stop"]
    assert rebuilt.kinds() == ["start"]
    assert page_state.generation_id == "g1"


def test_a_dead_device_behind_a_driver_that_starts_lazily_does_not_fail_the_task() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    rebuilds = Rebuilds(lambda: FakeDriver(results=[ActionResult(ok=True)]))
    session, _, _ = _session(driver, alive=False, rebuild=rebuilds)
    with pytest.raises(ConnectionLost, match="restarted; observe again"):
        _execute(session)

    assert _execute(session) == ActionResult(ok=True)
    assert driver.kinds() == ["execute", "stop"]


def test_a_live_device_keeps_its_stack() -> None:
    driver = FakeDriver(results=[ActionResult(ok=True)])
    driver.lose_connection_on(1)
    rebuilds = Rebuilds()
    session, provisioner, _ = _session(driver, rebuild=rebuilds)
    with pytest.raises(ConnectionLost):
        _execute(session)

    assert _execute(session) == ActionResult(ok=True)
    assert (session.assembly.stack, session.assembly.device) == (driver, FIRST)
    assert rebuilds.drivers == []
    assert provisioner.log == []


def test_a_restart_that_fails_on_a_live_device_fails_the_task() -> None:
    driver = StartFailsDriver()
    driver.lose_connection_on(1)
    session, provisioner, _ = _session(driver)

    with pytest.raises(BaseError, match="the device is gone"):
        _execute(session)

    assert provisioner.log == []


def test_a_new_stack_that_fails_to_start_fails_the_task() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, _, _ = _session(driver, alive=False, rebuild=Rebuilds(StartFailsDriver))

    with pytest.raises(BaseError, match="the device is gone"):
        _execute(session)


def test_a_rebuild_that_fails_releases_the_new_device_and_fails_the_task() -> None:
    def broken(device: ProvisionedDevice) -> FakeDriver:
        raise ValueError("no driver for this device")

    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, provisioner, _ = _session(driver, alive=False, rebuild=broken)

    with pytest.raises(BaseError, match="the device is gone"):
        _execute(session)

    assert provisioner.log == [("release", FIRST), ("provision", FakeSettings()), ("release", SECOND)]


def test_observe_recovers_the_same_way() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, _, page_state = _session(driver)

    with pytest.raises(ConnectionLost, match="restarted; observe again"):
        asyncio.run(session.observe(Observe()))

    assert driver.kinds() == ["observe", "stop", "start"]
    assert page_state.generation_id == "g1"


def test_close_stops_the_new_stack_and_releases_the_new_device() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    rebuilds = Rebuilds()
    session, provisioner, _ = _session(driver, alive=False, rebuild=rebuilds)
    with pytest.raises(ConnectionLost):
        _execute(session)

    asyncio.run(session.close())

    ((_, rebuilt),) = rebuilds.drivers
    assert provisioner.log[-1] == ("release", SECOND)
    assert rebuilt.kinds() == ["start", "stop"]


def test_stop_keeps_the_device_and_start_restarts_the_same_stack_on_a_live_device() -> None:
    driver = FakeDriver()
    session, provisioner, _ = _session(driver)

    asyncio.run(session.stop())
    asyncio.run(session.start())

    assert driver.kinds() == ["stop", "start"]
    assert provisioner.log == []
    assert session.assembly.device == FIRST


def test_start_after_stop_provisions_again_when_the_device_died() -> None:
    driver = FakeDriver()
    rebuilds = Rebuilds()
    session, provisioner, _ = _session(driver, alive=False, rebuild=rebuilds)

    asyncio.run(session.stop())
    asyncio.run(session.start())

    ((device, rebuilt),) = rebuilds.drivers
    assert (device, session.assembly.device) == (SECOND, SECOND)
    assert provisioner.log == [("release", FIRST), ("provision", FakeSettings())]
    assert (driver.kinds(), rebuilt.kinds()) == (["stop"], ["start"])


def test_health_asks_the_stack() -> None:
    driver = FakeDriver()
    session, _, _ = _session(driver)

    assert asyncio.run(session.health()).ok is False
    asyncio.run(session.start())
    assert asyncio.run(session.health()).ok is True


class StartLosesOnceDriver(FakeDriver):
    async def start(self, config: Any = None) -> None:
        await super().start(config)
        if self.kinds().count("start") == 1:
            raise ConnectionLost("no device yet", dispatched=False)


def test_a_start_that_loses_the_connection_restarts_once() -> None:
    driver = StartLosesOnceDriver()
    session, provisioner, page_state = _session(driver)
    asyncio.run(session.stop())

    asyncio.run(session.start())

    assert driver.kinds() == ["stop", "start", "stop", "start"]
    assert page_state.generation_id == "g1"
    assert provisioner.log == []


def test_a_start_that_loses_the_connection_twice_fails() -> None:
    driver = StartFailsDriver()
    session, _, page_state = _session(driver)

    with pytest.raises(BaseError, match="the device is gone") as raised:
        asyncio.run(session.start())

    assert driver.kinds() == ["start", "stop", "start"]
    assert isinstance(raised.value.cause, ConnectionLost)
    assert page_state.generation_id == "g0"


def test_lost_names_the_loss_that_ended_the_task_and_begin_task_clears_it() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, _, _ = _session(driver)
    with pytest.raises(ConnectionLost):
        _execute(session)
    assert session.lost is None
    driver.fail_next(ConnectionLost("lost again"))

    with pytest.raises(BaseError):
        _execute(session)

    assert session.lost == "the device is gone"
    session.begin_task()
    assert session.lost is None


def test_a_failed_restart_also_marks_the_session_lost() -> None:
    driver = StartFailsDriver()
    driver.lose_connection_on(1)
    session, _, _ = _session(driver)

    with pytest.raises(BaseError):
        _execute(session)

    assert session.lost == "the device is gone"
