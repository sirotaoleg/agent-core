# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scripted command driver for contract tests.

Written against a minimal ``CommandDriver`` protocol until the real contract lands in Phase 2; then it moves to
``contract/testing/fake.py`` as ``FakeDriver``. Actions, observations and results are any objects for now.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CommandDriver(Protocol):
    """Minimal driver shape: lifecycle, one observe command and one execute command."""

    @property
    def info(self) -> Any: ...

    async def start(self, config: Any = None) -> None: ...

    async def stop(self) -> None: ...

    async def observe(self, action: Any) -> Any: ...

    async def execute(self, action: Any) -> Any: ...


class FakeDriverError(Exception):
    """Test-only driver error; ``dispatched`` tells whether the command reached the page."""

    def __init__(self, message: str = "", *, dispatched: bool, connection_lost: bool = False) -> None:
        super().__init__(message)
        self.dispatched = dispatched
        self.connection_lost = connection_lost


@dataclass(frozen=True)
class FakeDriverInfo:
    name: str = "fake"
    surfaces: frozenset[str] = frozenset({"browser"})


@dataclass(frozen=True)
class Command:
    """One recorded call: ``kind`` is ``start``, ``stop``, ``observe`` or ``execute``."""

    kind: str
    payload: Any = None


@dataclass
class FakeCommandDriver:
    """Records every command and answers ``observe``/``execute`` from scripted queues, in order.

    ``fail_next`` raises a scripted error on the next observe/execute call. ``lose_connection_on(n)`` raises a
    connection-loss error on the n-th observe/execute call (1-based); the connection stays lost until ``start()``.
    """

    observations: Iterable[Any] = ()
    results: Iterable[Any] = ()
    driver_info: FakeDriverInfo = field(default_factory=FakeDriverInfo)
    commands: list[Command] = field(default_factory=list)
    started: bool = False

    def __post_init__(self) -> None:
        self._observations: deque[Any] = deque(self.observations)
        self._results: deque[Any] = deque(self.results)
        self._errors: deque[BaseException] = deque()
        self._lose_on: int | None = None
        self._lose_dispatched = True
        self._calls = 0
        self._connection_lost = False

    @property
    def info(self) -> FakeDriverInfo:
        return self.driver_info

    def fail_next(self, error: BaseException | None = None, *, dispatched: bool = False) -> None:
        """Raise ``error`` (default: ``FakeDriverError(dispatched=...)``) on the next observe/execute call."""
        self._errors.append(error if error is not None else FakeDriverError("scripted", dispatched=dispatched))

    def lose_connection_on(self, call: int, *, dispatched: bool = True) -> None:
        """``dispatched`` defaults to True: an unknown outcome is treated as dispatched (plan 4.1)."""
        self._lose_on = call
        self._lose_dispatched = dispatched

    def kinds(self) -> list[str]:
        return [command.kind for command in self.commands]

    async def start(self, config: Any = None) -> None:
        self.commands.append(Command("start", config))
        self.started = True
        self._connection_lost = False

    async def stop(self) -> None:
        self.commands.append(Command("stop"))
        self.started = False

    async def observe(self, action: Any) -> Any:
        return self._answer("observe", action, self._observations)

    async def execute(self, action: Any) -> Any:
        return self._answer("execute", action, self._results)

    def _answer(self, kind: str, action: Any, queue: deque[Any]) -> Any:
        self.commands.append(Command(kind, action))
        self._calls += 1
        if self._calls == self._lose_on:
            self._connection_lost = True
        if self._connection_lost:
            raise FakeDriverError("connection lost", dispatched=self._lose_dispatched, connection_lost=True)
        if self._errors:
            raise self._errors.popleft()
        if not queue:
            raise AssertionError(f"FakeCommandDriver has no scripted answer left for {kind}({action!r})")
        return queue.popleft()
