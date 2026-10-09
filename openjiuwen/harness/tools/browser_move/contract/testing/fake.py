# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A scripted ``Driver`` for tests: records every call and answers from queues, in order."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..actions import (
    Action,
    Click,
    CloseContext,
    Drag,
    HandleDialog,
    Hover,
    Observe,
    PressKey,
    RunScript,
    Scroll,
    SwitchContext,
    TypeText,
    Wait,
)
from ..driver import Driver, DriverInfo, Health, ensure_supported
from ..errors import ConnectionLost, DriverError
from ..observation import Observation
from ..refs import REF_KINDS
from ..results import ActionResult

# Every shared command except ``Observe`` (which goes through ``observe()``), with every element ref kind.
DEFAULT_ACTIONS: dict[type[Action], frozenset[type]] = {
    command: frozenset(REF_KINDS)
    for command in (
        Click,
        TypeText,
        PressKey,
        Scroll,
        Hover,
        Drag,
        Wait,
        SwitchContext,
        CloseContext,
        HandleDialog,
        RunScript,
    )
}


@dataclass(frozen=True)
class Call:
    """One recorded call: ``kind`` is ``start``, ``stop``, ``observe`` or ``execute``."""

    kind: str
    payload: Any = None


class FakeDriver(Driver):
    """Records every call and answers ``observe``/``execute`` from scripted queues, in order.

    ``execute`` raises ``Unsupported(dispatched=False)`` for a command or ref kind missing from ``info.actions``
    (default: ``DEFAULT_ACTIONS``). ``script`` adds answers after construction. ``fail_next`` raises a scripted error
    on the next observe/execute call. ``lose_connection_on(n)`` raises ``ConnectionLost`` on the n-th observe/execute
    call (1-based); the connection stays lost until ``start()``. Running out of scripted answers fails the test with
    ``AssertionError``.
    """

    def __init__(
        self,
        observations: Iterable[Observation] = (),
        results: Iterable[ActionResult] = (),
        *,
        info: DriverInfo | None = None,
    ) -> None:
        self._info = info or DriverInfo(name="fake", surfaces=frozenset({"fake"}), actions=DEFAULT_ACTIONS)
        self._observations: deque[Observation] = deque(observations)
        self._results: deque[ActionResult] = deque(results)
        self._errors: deque[BaseException] = deque()
        self._lose_on: int | None = None
        self._lose_dispatched = True
        self._calls = 0
        self._connection_lost = False
        self.calls: list[Call] = []
        self.started = False

    @property
    def info(self) -> DriverInfo:
        return self._info

    def script(self, *, observations: Iterable[Observation] = (), results: Iterable[ActionResult] = ()) -> None:
        """Append answers to the end of the ``observe`` and ``execute`` queues."""
        self._observations.extend(observations)
        self._results.extend(results)

    def fail_next(self, error: BaseException | None = None, *, dispatched: bool = False) -> None:
        """Raise ``error`` (default: ``DriverError(dispatched=...)``) on the next observe/execute call."""
        self._errors.append(error if error is not None else DriverError("scripted", dispatched=dispatched))

    def lose_connection_on(self, call: int, *, dispatched: bool = True) -> None:
        """``dispatched`` defaults to True: an unknown outcome counts as dispatched."""
        self._lose_on = call
        self._lose_dispatched = dispatched

    def kinds(self) -> list[str]:
        return [call.kind for call in self.calls]

    async def start(self, config: Any = None) -> None:
        self.calls.append(Call("start", config))
        self.started = True
        self._connection_lost = False

    async def stop(self) -> None:
        self.calls.append(Call("stop"))
        self.started = False

    async def health(self) -> Health:
        if self._connection_lost:
            return Health(ok=False, detail="connection lost")
        return Health(ok=self.started, detail=None if self.started else "not started")

    async def observe(self, command: Observe) -> Observation:
        self.calls.append(Call("observe", command))
        return self._answer("observe", command, self._observations)

    async def execute(self, action: Action) -> ActionResult:
        self.calls.append(Call("execute", action))
        ensure_supported(self._info.name, self._info.actions, action)
        return self._answer("execute", action, self._results)

    def _answer(self, kind: str, payload: Any, queue: deque[Any]) -> Any:
        self._calls += 1
        if self._calls == self._lose_on:
            self._connection_lost = True
        if self._connection_lost:
            raise ConnectionLost("connection lost", dispatched=self._lose_dispatched)
        if self._errors:
            raise self._errors.popleft()
        if not queue:
            raise AssertionError(f"FakeDriver has no scripted answer left for {kind}({payload!r})")
        return queue.popleft()
