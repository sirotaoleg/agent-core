# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``FakeDriver`` records calls, replays scripted answers and raises contract errors on request."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    ConnectionLost,
    ContextRef,
    Driver,
    DriverError,
    DriverInfo,
    DriverRef,
    Observation,
    Observe,
    PointRef,
    TypeText,
    Unsupported,
)
from openjiuwen.harness.tools.browser_move.contract.testing import DEFAULT_ACTIONS, Call, FakeDriver
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate

E1 = DriverRef("e1", "g1")
OBS_1 = Observation(context=ContextRef("c1", "window"))
OBS_2 = Observation(context=ContextRef("c2", "window"))
RES_1 = ActionResult(ok=True, value=1)
RES_2 = ActionResult(ok=True, value=2)


def test_is_a_driver_with_a_neutral_default_info() -> None:
    driver = FakeDriver()

    assert isinstance(driver, Driver)
    assert (driver.info.name, driver.info.surfaces) == ("fake", frozenset({"fake"}))
    assert driver.info.actions == DEFAULT_ACTIONS
    assert Observe not in driver.info.actions


def test_records_every_call_in_order() -> None:
    driver = FakeDriver(observations=[OBS_1], results=[RES_1])

    async def scenario() -> None:
        await driver.start({"headless": True})
        await driver.observe(Observe())
        await driver.execute(Click(E1))
        await driver.stop()

    asyncio.run(scenario())

    assert driver.calls == [
        Call("start", {"headless": True}),
        Call("observe", Observe()),
        Call("execute", Click(E1)),
        Call("stop"),
    ]
    assert driver.started is False


def test_returns_scripted_observations_and_results_in_order() -> None:
    driver = FakeDriver(observations=[OBS_1, OBS_2], results=[RES_1, RES_2])

    async def scenario() -> list[object]:
        return [
            await driver.observe(Observe()),
            await driver.execute(Click(E1)),
            await driver.execute(Click(E1)),
            await driver.observe(Observe()),
        ]

    assert asyncio.run(scenario()) == [OBS_1, RES_1, RES_2, OBS_2]


def test_running_out_of_script_fails_loudly() -> None:
    driver = FakeDriver()

    with pytest.raises(AssertionError, match="no scripted answer left for execute"):
        asyncio.run(driver.execute(Click(E1)))
    assert driver.kinds() == ["execute"]


@pytest.mark.parametrize("dispatched", [True, False])
def test_scripted_error_is_a_driver_error_with_the_chosen_dispatched_value(dispatched: bool) -> None:
    driver = FakeDriver(results=[RES_1])
    driver.fail_next(dispatched=dispatched)

    with pytest.raises(DriverError) as raised:
        asyncio.run(driver.execute(Click(E1)))

    assert type(raised.value) is DriverError
    assert raised.value.dispatched is dispatched
    assert driver.kinds() == ["execute"], "the failed call is still recorded"
    assert asyncio.run(driver.execute(Click(E1))) == RES_1, "the error does not consume a scripted result"


def test_any_scripted_exception_can_be_raised() -> None:
    driver = FakeDriver()
    driver.fail_next(TimeoutError("slow scene"))

    with pytest.raises(TimeoutError, match="slow scene"):
        asyncio.run(driver.observe(Observe()))


def test_connection_is_lost_on_the_nth_call_until_restart() -> None:
    driver = FakeDriver(observations=[OBS_1, OBS_2], results=[RES_1])
    driver.lose_connection_on(2)

    async def scenario() -> list[object]:
        outcomes: list[object] = []
        await driver.start()
        for call in (driver.observe(Observe()), driver.execute(Click(E1)), driver.observe(Observe())):
            try:
                outcomes.append(await call)
            except ConnectionLost as error:
                outcomes.append(("lost", error.dispatched))
        outcomes.append((await driver.health()).ok)
        await driver.start()
        outcomes.append((await driver.health()).ok)
        outcomes.append(await driver.execute(Click(E1)))
        outcomes.append(await driver.observe(Observe()))
        return outcomes

    assert asyncio.run(scenario()) == [OBS_1, ("lost", True), ("lost", True), False, True, RES_1, OBS_2]
    assert driver.kinds() == ["start", "observe", "execute", "observe", "start", "execute", "observe"]


def test_connection_loss_can_be_marked_not_dispatched() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1, dispatched=False)

    with pytest.raises(ConnectionLost) as raised:
        asyncio.run(driver.execute(Click(E1)))

    assert raised.value.dispatched is False


def test_health_reflects_start_and_stop() -> None:
    driver = FakeDriver()

    async def scenario() -> list[bool]:
        before = (await driver.health()).ok
        await driver.start()
        running = (await driver.health()).ok
        await driver.stop()
        return [before, running, (await driver.health()).ok]

    assert asyncio.run(scenario()) == [False, True, False]


def test_command_missing_from_info_actions_is_unsupported() -> None:
    driver = FakeDriver(results=[RES_1])

    with pytest.raises(Unsupported) as raised:
        asyncio.run(driver.execute(Navigate("https://example.test/")))

    assert raised.value.dispatched is False
    assert asyncio.run(driver.execute(Click(E1))) == RES_1, "an unsupported call consumes no scripted result"


def test_info_can_restrict_commands_and_ref_kinds() -> None:
    info = DriverInfo(name="points", surfaces=frozenset({"other"}), actions={Click: frozenset({PointRef})})
    driver = FakeDriver(results=[RES_1], info=info)

    assert driver.info is info
    with pytest.raises(Unsupported, match="does not accept DriverRef"):
        asyncio.run(driver.execute(Click(E1)))
    with pytest.raises(Unsupported, match="does not handle TypeText"):
        asyncio.run(driver.execute(TypeText(None, "x")))
    assert asyncio.run(driver.execute(Click(PointRef(1, 2, "g1")))) == RES_1
