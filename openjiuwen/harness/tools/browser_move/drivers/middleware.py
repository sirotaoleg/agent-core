# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wrappers every driver gets; each is a ``Driver`` around an inner ``Driver``."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Mapping
from typing import Any, Protocol, TypeVar

from ..contract import (
    Action,
    ActionResult,
    ActionTimeout,
    Driver,
    DriverError,
    DriverInfo,
    Effect,
    Health,
    Observation,
    Observe,
    StaleRef,
)
from ..contract.masking import MASK, is_sensitive, masked

_T = TypeVar("_T")


class TraceSink(Protocol):
    def __call__(self, record: dict[str, Any]) -> None: ...


class _Wrapper(Driver):
    def __init__(self, inner: Driver) -> None:
        self.inner = inner

    @property
    def info(self) -> DriverInfo:
        return self.inner.info

    async def start(self, config: Any = None) -> None:
        await self.inner.start(config)

    async def stop(self) -> None:
        await self.inner.stop()

    async def health(self) -> Health:
        return await self.inner.health()

    async def observe(self, command: Observe) -> Observation:
        return await self.inner.observe(command)

    async def execute(self, action: Action) -> ActionResult:
        return await self.inner.execute(action)


class Timeouts(_Wrapper):
    """Bounds each call by the command's ``effect``; an effect without a bound runs unbounded."""

    def __init__(self, inner: Driver, per_effect: Mapping[Effect, float]) -> None:
        super().__init__(inner)
        self._per_effect = dict(per_effect)

    async def observe(self, command: Observe) -> Observation:
        return await self._bounded(command, self.inner.observe(command))

    async def execute(self, action: Action) -> ActionResult:
        return await self._bounded(action, self.inner.execute(action))

    async def _bounded(self, command: Action, call: Awaitable[_T]) -> _T:
        seconds = self._per_effect.get(command.effect)
        if seconds is None:
            return await call
        try:
            async with asyncio.timeout(seconds):
                return await call
        except TimeoutError as error:
            # The inner call was cancelled mid-flight, so whether it took effect is unknown.
            raise ActionTimeout(f"{type(command).__name__} took longer than {seconds}s", dispatched=True) from error


class RetryOnStale(_Wrapper):
    """On ``StaleRef`` before dispatch, observes once and re-raises it with that fresh observation attached."""

    async def execute(self, action: Action) -> ActionResult:
        try:
            return await self.inner.execute(action)
        except StaleRef as error:
            if error.dispatched:
                raise
            fresh = await self.inner.observe(Observe())
            raise StaleRef(str(error), dispatched=False, observation=fresh) from error


def _traced(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {item.name: _traced(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [_traced(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _traced(item) for key, item in value.items()}
    return value


class Tracing(_Wrapper):
    """Writes one record per ``observe``/``execute`` to ``sink``, masking the ``__sensitive_fields__`` of commands."""

    def __init__(self, inner: Driver, sink: TraceSink) -> None:
        super().__init__(inner)
        self._sink = sink

    async def observe(self, command: Observe) -> Observation:
        record = self._command_record("observe", command)
        try:
            observation = await self.inner.observe(command)
        except DriverError as error:
            self._sink(record | self._error_record(error, command))
            raise
        # Elements and native text may show what a sensitive command typed, so only their shape is traced.
        self._sink(record | {"context": observation.context.id, "elements": len(observation.elements)})
        return observation

    async def execute(self, action: Action) -> ActionResult:
        record = self._command_record("execute", action)
        try:
            result = await self.inner.execute(action)
        except DriverError as error:
            self._sink(record | self._error_record(error, action))
            raise
        self._sink(record | {"ok": result.ok, "detail": result.detail, "context_changed": result.context_changed})
        return result

    @staticmethod
    def _command_record(call: str, command: Action) -> dict[str, Any]:
        return {"call": call, "command": type(command).__name__, "args": _traced(masked(command))}

    @staticmethod
    def _error_record(error: DriverError, command: Action) -> dict[str, Any]:
        record = {"error": type(error).__name__, "dispatched": error.dispatched}
        # Driver errors may quote the typed text or the script, e.g. a JS exception message.
        if not is_sensitive(command):
            record["message"] = str(error)
        return record


__all__ = ["MASK", "RetryOnStale", "Timeouts", "TraceSink", "Tracing", "masked"]
