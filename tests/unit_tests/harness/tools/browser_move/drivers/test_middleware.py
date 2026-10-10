# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Timeouts, RetryOnStale and Tracing on ``FakeDriver``, and the conformance suite through the full stack."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    ActionTimeout,
    Click,
    ContextRef,
    DriverError,
    DriverInfo,
    DriverRef,
    Effect,
    Health,
    Observation,
    Observe,
    PressKey,
    RunScript,
    StaleRef,
    TypeText,
    Wait,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.contract.testing.conformance import DriverConformance, TraceSink
from openjiuwen.harness.tools.browser_move.drivers.middleware import MASK, RetryOnStale, Timeouts, Tracing
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import FieldKind, FillForm, FormField

from ..contract.test_conformance_fake import FAKE_ACTIONS, FAKE_SCENE, WorldDriver

pytest_plugins = ["openjiuwen.harness.tools.browser_move.contract.testing.conformance"]

REF = DriverRef("e1", "g1")
OK = ActionResult(ok=True)
SECRET = "hunter2-middleware"


def _observation(context: str = "c1") -> Observation:
    return Observation(context=ContextRef(context, "screen"))


class SlowDriver(FakeDriver):
    def __init__(self, delay: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self.delay = delay
        self.cancelled = 0

    async def _slow(self) -> None:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def observe(self, command: Observe) -> Observation:
        await self._slow()
        return await super().observe(command)

    async def execute(self, action: Action) -> ActionResult:
        await self._slow()
        return await super().execute(action)


class TestTimeouts:
    def test_execute_over_its_effect_bound_is_a_dispatched_timeout(self):
        inner = SlowDriver(1.0, results=[OK])
        driver = Timeouts(inner, {Effect.ACT: 0.01})

        with pytest.raises(ActionTimeout) as raised:
            asyncio.run(driver.execute(Click(target=REF)))

        assert raised.value.dispatched is True
        assert inner.cancelled == 1

    def test_observe_is_bounded_as_read(self):
        driver = Timeouts(SlowDriver(1.0, observations=[_observation()]), {Effect.READ: 0.01, Effect.ACT: 5.0})

        with pytest.raises(ActionTimeout) as raised:
            asyncio.run(driver.observe(Observe()))

        assert raised.value.dispatched is True

    def test_each_effect_gets_its_own_bound(self):
        driver = Timeouts(SlowDriver(0.05, results=[OK]), {Effect.ACT: 5.0, Effect.READ: 0.01})

        assert asyncio.run(driver.execute(Click(target=REF))) is OK
        with pytest.raises(ActionTimeout):
            asyncio.run(driver.execute(Wait(seconds=0)))

    def test_an_unlisted_effect_is_not_bounded(self):
        driver = Timeouts(SlowDriver(0.05, results=[OK]), {Effect.READ: 0.01})

        assert asyncio.run(driver.execute(RunScript(code="noop"))) is OK

    def test_inner_errors_pass_through_unchanged(self):
        inner = FakeDriver()
        inner.fail_next(StaleRef("old", dispatched=False))

        with pytest.raises(StaleRef):
            asyncio.run(Timeouts(inner, {Effect.ACT: 5.0}).execute(Click(target=REF)))


class TestRetryOnStale:
    def test_stale_ref_is_raised_again_with_one_fresh_observation(self):
        fresh = _observation("c2")
        inner = FakeDriver(observations=[fresh])
        inner.fail_next(StaleRef("e1 is old", dispatched=False))

        with pytest.raises(StaleRef) as raised:
            asyncio.run(RetryOnStale(inner).execute(Click(target=REF)))

        assert raised.value.observation is fresh
        assert raised.value.dispatched is False
        assert inner.kinds() == ["execute", "observe"]

    def test_a_dispatched_stale_ref_is_neither_observed_nor_retried(self):
        inner = FakeDriver(observations=[_observation()], results=[OK])
        inner.fail_next(StaleRef("sent already", dispatched=True))

        with pytest.raises(StaleRef) as raised:
            asyncio.run(RetryOnStale(inner).execute(Click(target=REF)))

        assert raised.value.observation is None
        assert inner.kinds() == ["execute"]

    @pytest.mark.parametrize(
        "error",
        [ActionTimeout("slow"), DriverError("unknown outcome")],
        ids=["timeout", "unknown-outcome"],
    )
    def test_a_dispatched_error_is_never_retried(self, error):
        inner = FakeDriver(observations=[_observation()], results=[OK])
        inner.fail_next(error)

        with pytest.raises(type(error)):
            asyncio.run(RetryOnStale(inner).execute(Click(target=REF)))

        assert inner.kinds() == ["execute"]

    def test_the_command_is_sent_once_even_when_the_driver_stays_stale(self):
        inner = FakeDriver(observations=[_observation(), _observation()], results=[OK])
        inner.fail_next(StaleRef("old", dispatched=False))
        inner.fail_next(StaleRef("still old", dispatched=False))

        with pytest.raises(StaleRef):
            asyncio.run(RetryOnStale(inner).execute(Click(target=REF)))

        assert inner.kinds() == ["execute", "observe"]

    def test_a_success_is_returned_without_observing(self):
        inner = FakeDriver(results=[OK])

        assert asyncio.run(RetryOnStale(inner).execute(Click(target=REF))) is OK
        assert inner.kinds() == ["execute"]


@dataclass(frozen=True)
class Unlock(Action):
    effect: ClassVar[Effect] = Effect.ACT
    __sensitive_fields__: ClassVar[tuple[str, ...]] = ("pin",)

    pin: str
    sensitive: bool = True


def _traced(action: Action) -> TraceSink:
    sink = TraceSink()
    info = DriverInfo(name="fake", surfaces=frozenset({"fake"}), actions={type(action): frozenset({DriverRef})})
    asyncio.run(Tracing(FakeDriver(results=[OK], info=info), sink).execute(action))
    return sink


class TestTracing:
    @pytest.mark.parametrize(
        "action",
        [
            TypeText(target=REF, text=SECRET, sensitive=True),
            RunScript(code=SECRET, sensitive=True),
            FillForm(fields=(FormField(target=REF, kind=FieldKind.TEXTBOX, value=SECRET, sensitive=True),)),
            Unlock(pin=SECRET),
        ],
        ids=["type-text", "run-script", "form-field", "surface-command"],
    )
    def test_a_sensitive_field_is_masked(self, action):
        sink = _traced(action)

        assert SECRET not in sink.text
        assert MASK in sink.text

    def test_a_field_is_traced_when_not_sensitive(self):
        sink = _traced(TypeText(target=REF, text="Ada"))

        assert sink.records == [
            {
                "call": "execute",
                "command": "TypeText",
                "args": {
                    "target": {"ref": "e1", "generation": "g1"},
                    "text": "Ada",
                    "submit": False,
                    "sensitive": False,
                    "clear": True,
                    "slowly": False,
                },
                "ok": True,
                "detail": None,
                "context_changed": False,
            }
        ]

    def test_only_the_sensitive_form_field_is_masked(self):
        form = FillForm(
            fields=(
                FormField(target=REF, kind=FieldKind.TEXTBOX, value="Ada"),
                FormField(target=REF, kind=FieldKind.TEXTBOX, value=SECRET, sensitive=True),
            )
        )
        sink = _traced(form)

        values = [field["value"] for field in sink.records[0]["args"]["fields"]]
        assert values == ["Ada", MASK]

    def test_an_error_is_traced_and_raised(self):
        inner = FakeDriver()
        inner.fail_next(StaleRef("e1 is old", dispatched=False))
        sink = TraceSink()

        with pytest.raises(StaleRef):
            asyncio.run(Tracing(inner, sink).execute(PressKey(key="Enter")))

        assert sink.records == [
            {
                "call": "execute",
                "command": "PressKey",
                "args": {"key": "Enter", "target": None},
                "error": "StaleRef",
                "message": "e1 is old",
                "dispatched": False,
            }
        ]

    def test_sensitive_script_args_are_masked(self):
        sink = _traced(RunScript(code="(pin) => pin", args={"pin": SECRET}, sensitive=True))

        assert sink.records[0]["args"]["args"] == MASK
        assert SECRET not in sink.text

    @pytest.mark.parametrize(
        "action",
        [TypeText(target=REF, text=SECRET, sensitive=True), RunScript(code=SECRET, sensitive=True)],
        ids=["type-text", "run-script"],
    )
    def test_the_error_message_of_a_sensitive_command_is_dropped(self, action):
        inner = FakeDriver()
        inner.fail_next(DriverError(f"Error: {SECRET} is not defined", dispatched=True))
        sink = TraceSink()

        with pytest.raises(DriverError):
            asyncio.run(Tracing(inner, sink).execute(action))

        assert "message" not in sink.records[0]
        assert sink.records[0]["error"] == "DriverError"
        assert SECRET not in sink.text

    def test_the_error_message_is_kept_when_the_command_is_not_sensitive(self):
        inner = FakeDriver()
        inner.fail_next(DriverError("Error: x is not defined", dispatched=True))
        sink = TraceSink()

        with pytest.raises(DriverError):
            asyncio.run(Tracing(inner, sink).execute(RunScript(code="x")))

        assert sink.records[0]["message"] == "Error: x is not defined"

    def test_observe_is_traced_without_the_element_contents(self):
        observation = Observation(context=ContextRef("c1", "screen"), native_text=SECRET)
        sink = TraceSink()

        assert asyncio.run(Tracing(FakeDriver(observations=[observation]), sink).observe(Observe())) is observation

        assert SECRET not in sink.text
        assert sink.records[0]["context"] == "c1"
        assert sink.records[0]["elements"] == 0


def _stack(inner: FakeDriver, sink: TraceSink) -> Tracing:
    return Tracing(RetryOnStale(Timeouts(inner, {Effect.READ: 5.0, Effect.ACT: 5.0})), sink)


class TestStack:
    def test_info_lifecycle_and_health_reach_the_inner_driver(self):
        inner = FakeDriver()
        driver = _stack(inner, TraceSink())

        async def run() -> Health:
            await driver.start("config")
            health = await driver.health()
            await driver.stop()
            return health

        assert driver.info is inner.info
        assert asyncio.run(run()) == Health(ok=True)
        assert inner.calls[0].payload == "config"
        assert inner.kinds() == ["start", "stop"]

    def test_a_stale_ref_is_traced_once_with_its_fresh_observation(self):
        fresh = _observation("c2")
        inner = FakeDriver(observations=[fresh])
        inner.fail_next(StaleRef("old", dispatched=False))
        sink = TraceSink()

        with pytest.raises(StaleRef) as raised:
            asyncio.run(_stack(inner, sink).execute(Click(target=REF)))

        assert raised.value.observation is fresh
        assert [record.get("error") for record in sink.records] == ["StaleRef"]

    def test_a_timeout_is_traced_as_dispatched(self):
        sink = TraceSink()
        driver = Tracing(RetryOnStale(Timeouts(SlowDriver(1.0, results=[OK]), {Effect.ACT: 0.01})), sink)

        with pytest.raises(ActionTimeout):
            asyncio.run(driver.execute(Click(target=REF)))

        assert sink.records[0]["error"] == "ActionTimeout"
        assert sink.records[0]["dispatched"] is True


class TestConformanceThroughTheStack(DriverConformance):
    scene = FAKE_SCENE
    driver_actions = FAKE_ACTIONS

    @pytest.fixture
    def conformance_driver_factory(self):
        return lambda sink: _stack(WorldDriver(TraceSink()), sink)
