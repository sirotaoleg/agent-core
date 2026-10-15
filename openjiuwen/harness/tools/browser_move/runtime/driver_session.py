# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runs commands on the assembled driver stack and recovers from one lost connection per task."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, NoReturn

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error

from ..contract import Action, ActionResult, ConnectionLost, DriverError, Health, Observation, Observe
from .page_state import PageState

if TYPE_CHECKING:
    from .assembly import DriverAssembly


class DriverSession:
    """Runs commands on the stack; on ``ConnectionLost`` it restarts once per task and never re-sends."""

    def __init__(self, assembly: DriverAssembly, page_state: PageState, messages: Mapping[str, str]) -> None:
        self.assembly = assembly
        self._page_state = page_state
        self._restarted = messages["driver_restarted"]
        self._lost = messages["driver_lost"]
        self._losses = 0
        self._ended = False

    @property
    def page_state(self) -> PageState:
        return self._page_state

    @property
    def lost(self) -> str | None:
        """The ``driver_lost`` message once a loss ended the task, ``None`` while the stack can still serve it."""
        return self._lost if self._ended else None

    def begin_task(self) -> None:
        self._losses = 0
        self._ended = False

    async def execute(self, action: Action) -> ActionResult:
        try:
            return await self.assembly.stack.execute(action)
        except ConnectionLost as error:
            await self._recover(error)

    async def observe(self, command: Observe) -> Observation:
        try:
            return await self.assembly.stack.observe(command)
        except ConnectionLost as error:
            await self._recover(error)

    async def stop(self) -> None:
        """Stop the stack and keep the device, between tasks."""
        await self.assembly.stack.stop()

    async def start(self) -> None:
        """Start the stack again on a live device; a dead one is released and provisioned again first."""
        try:
            await self._start()
        except ConnectionLost as error:
            await self._restart(error)

    async def _start(self) -> None:
        assembly = self.assembly
        if await asyncio.to_thread(assembly.provisioner.is_alive, assembly.device):
            await assembly.stack.start()
            return
        provisioner = assembly.provisioner
        await asyncio.to_thread(provisioner.release, assembly.device)
        device = await asyncio.to_thread(provisioner.provision, assembly.surface.settings)
        try:
            stack = assembly.rebuild(device)
        except BaseException:
            await asyncio.to_thread(provisioner.release, device)
            raise
        self.assembly = dataclasses.replace(assembly, stack=stack, device=device)
        await stack.start()

    async def health(self) -> Health:
        return await self.assembly.stack.health()

    async def close(self) -> None:
        await self.assembly.close()

    async def _recover(self, error: ConnectionLost) -> NoReturn:
        self._losses += 1
        if self._losses > 1:
            raise self._end(error) from error
        await self._restart(error)
        raise ConnectionLost(self._restarted, dispatched=error.dispatched) from error

    async def _restart(self, error: ConnectionLost) -> None:
        try:
            with contextlib.suppress(DriverError):
                await self.assembly.stack.stop()
            await self._start()
        except Exception as restart_error:
            raise self._end(restart_error) from error
        self._page_state.reset()

    def _end(self, cause: Exception) -> BaseError:
        self._ended = True
        return build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg=self._lost, cause=cause)


__all__ = ["DriverSession"]
