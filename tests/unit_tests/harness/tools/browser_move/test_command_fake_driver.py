# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The command FakeDriver records commands, replays scripted answers and raises scripted errors."""

from __future__ import annotations

import asyncio

import pytest

from tests.unit_tests.harness.tools.browser_move.fakes.command_driver import (
    Command,
    CommandDriver,
    FakeCommandDriver,
    FakeDriverError,
    FakeDriverInfo,
)


def test_satisfies_the_minimal_protocol() -> None:
    driver = FakeCommandDriver()

    assert isinstance(driver, CommandDriver)
    assert driver.info == FakeDriverInfo(name="fake", surfaces=frozenset({"browser"}))


def test_records_every_command_in_order() -> None:
    driver = FakeCommandDriver(observations=["obs-1"], results=["res-1"])

    async def scenario() -> None:
        await driver.start({"headless": True})
        await driver.observe("observe-page")
        await driver.execute("click-e1")
        await driver.stop()

    asyncio.run(scenario())

    assert driver.commands == [
        Command("start", {"headless": True}),
        Command("observe", "observe-page"),
        Command("execute", "click-e1"),
        Command("stop"),
    ]
    assert driver.started is False


def test_returns_scripted_observations_and_results_in_order() -> None:
    driver = FakeCommandDriver(observations=["obs-1", "obs-2"], results=["res-1", "res-2"])

    async def scenario() -> list[str]:
        return [
            await driver.observe("o1"),
            await driver.execute("a1"),
            await driver.execute("a2"),
            await driver.observe("o2"),
        ]

    assert asyncio.run(scenario()) == ["obs-1", "res-1", "res-2", "obs-2"]


def test_running_out_of_script_fails_loudly() -> None:
    driver = FakeCommandDriver()

    with pytest.raises(AssertionError, match="no scripted answer left for execute"):
        asyncio.run(driver.execute("click-e1"))
    assert driver.kinds() == ["execute"]


@pytest.mark.parametrize("dispatched", [True, False])
def test_scripted_error_carries_the_chosen_dispatched_value(dispatched: bool) -> None:
    driver = FakeCommandDriver(results=["res-1"])
    driver.fail_next(dispatched=dispatched)

    with pytest.raises(FakeDriverError) as raised:
        asyncio.run(driver.execute("click-e1"))

    assert raised.value.dispatched is dispatched
    assert raised.value.connection_lost is False
    assert driver.kinds() == ["execute"], "the failed command is still recorded"
    assert asyncio.run(driver.execute("click-e1")) == "res-1", "the error does not consume a scripted result"


def test_any_scripted_exception_can_be_raised() -> None:
    driver = FakeCommandDriver()
    driver.fail_next(TimeoutError("slow page"))

    with pytest.raises(TimeoutError, match="slow page"):
        asyncio.run(driver.observe("o1"))


def test_connection_is_lost_on_the_nth_call_until_restart() -> None:
    driver = FakeCommandDriver(observations=["obs-1", "obs-2"], results=["res-1"])
    driver.lose_connection_on(2)

    async def scenario() -> list[object]:
        outcomes: list[object] = []
        await driver.start()
        for call in (driver.observe("o1"), driver.execute("a1"), driver.observe("o2")):
            try:
                outcomes.append(await call)
            except FakeDriverError as error:
                outcomes.append((error.connection_lost, error.dispatched))
        await driver.start()
        outcomes.append(await driver.execute("a1"))
        outcomes.append(await driver.observe("o2"))
        return outcomes

    assert asyncio.run(scenario()) == ["obs-1", (True, True), (True, True), "res-1", "obs-2"]
    assert driver.kinds() == ["start", "observe", "execute", "observe", "start", "execute", "observe"]


def test_connection_loss_can_be_marked_not_dispatched() -> None:
    driver = FakeCommandDriver()
    driver.lose_connection_on(1, dispatched=False)

    with pytest.raises(FakeDriverError) as raised:
        asyncio.run(driver.execute("a1"))

    assert (raised.value.connection_lost, raised.value.dispatched) == (True, False)
