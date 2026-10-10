# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``DriverSession`` on ``FakeDriver``: one restart per task, re-provisioning, and no re-sent command."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools.browser_move.contract import Click, ConnectionLost, DriverRef, Observe
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice

MESSAGES = {"driver_restarted": "restarted; observe again", "driver_lost": "the device is gone"}
CLICK = Click(DriverRef("e1", "g0"))
FIRST = ProvisionedDevice(kind="fake-device", endpoint="fake://1", extras={})
SECOND = ProvisionedDevice(kind="fake-device", endpoint="fake://2", extras={})


@dataclass(frozen=True)
class FakeSettings:
    provisioning: str = "fake"


class FakeProvisioner:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    def provision(self, surface_settings: Any) -> ProvisionedDevice:
        self.log.append(("provision", surface_settings))
        return SECOND

    def release(self, device: ProvisionedDevice) -> None:
        self.log.append(("release", device))


class StartFailsDriver(FakeDriver):
    """Fails the first ``fails`` restarts, as a driver does when its device is gone."""

    def __init__(self, fails: int) -> None:
        super().__init__()
        self.fails = fails

    async def start(self, config: Any = None) -> None:
        await super().start(config)
        if self.fails:
            self.fails -= 1
            raise ConnectionLost("no device", dispatched=False)


def _session(driver: FakeDriver) -> tuple[DriverSession, FakeProvisioner, BrowserPageState]:
    provisioner = FakeProvisioner()
    page_state = BrowserPageState(page_id="page-test")
    assembly = DriverAssembly(driver, FIRST, provisioner, FakeSettings())
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


def test_a_driver_that_cannot_start_again_is_re_provisioned() -> None:
    driver = StartFailsDriver(fails=1)
    driver.lose_connection_on(1)
    session, provisioner, page_state = _session(driver)

    with pytest.raises(ConnectionLost, match="restarted; observe again"):
        _execute(session)

    assert provisioner.log == [("release", FIRST), ("provision", FakeSettings())]
    assert session.assembly.device == SECOND
    assert driver.kinds() == ["execute", "stop", "start", "start"]
    assert page_state.generation_id == "g1"


def test_a_restart_that_fails_after_re_provisioning_fails_the_task() -> None:
    driver = StartFailsDriver(fails=2)
    driver.lose_connection_on(1)
    session, _, _ = _session(driver)

    with pytest.raises(BaseError, match="the device is gone"):
        _execute(session)


def test_observe_recovers_the_same_way() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    session, _, page_state = _session(driver)

    with pytest.raises(ConnectionLost, match="restarted; observe again"):
        asyncio.run(session.observe(Observe()))

    assert driver.kinds() == ["observe", "stop", "start"]
    assert page_state.generation_id == "g1"


def test_close_releases_the_re_provisioned_device() -> None:
    driver = StartFailsDriver(fails=1)
    driver.lose_connection_on(1)
    session, provisioner, _ = _session(driver)
    with pytest.raises(ConnectionLost):
        _execute(session)

    asyncio.run(session.close())

    assert provisioner.log[-1] == ("release", SECOND)
    assert driver.kinds()[-1] == "stop"
