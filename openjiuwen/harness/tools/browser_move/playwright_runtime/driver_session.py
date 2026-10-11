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
from openjiuwen.core.common.exception.errors import build_error

from ..contract import Action, ActionResult, ConnectionLost, DriverError, Health, Observation, Observe
from .page_state import BrowserPageState

if TYPE_CHECKING:
    from .assembly import DriverAssembly


class DriverSession:
    """Forwards commands to the stack; restarts it once per task on ``ConnectionLost`` and never re-sends the call.

    A live device gets the same stack restarted; a dead one is released, provisioned again and gets a new stack.
    """

    def __init__(self, assembly: DriverAssembly, page_state: BrowserPageState, messages: Mapping[str, str]) -> None:
        self.assembly = assembly
        self._page_state = page_state
        self._restarted = messages["driver_restarted"]
        self._lost = messages["driver_lost"]
        self._losses = 0

    @property
    def page_state(self) -> BrowserPageState:
        return self._page_state

    def begin_task(self) -> None:
        self._losses = 0

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
        assembly = self.assembly
        if await asyncio.to_thread(assembly.provisioner.is_alive, assembly.device):
            await assembly.stack.start()
            return
        provisioner = assembly.provisioner
        await asyncio.to_thread(provisioner.release, assembly.device)
        device = await asyncio.to_thread(provisioner.provision, assembly.surface_settings)
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
            raise build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg=self._lost, cause=error) from error
        try:
            await self._restart()
        except Exception as restart_error:
            raise build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg=self._lost, cause=restart_error) from error
        self._page_state.reset()
        raise ConnectionLost(self._restarted, dispatched=error.dispatched) from error

    async def _restart(self) -> None:
        with contextlib.suppress(DriverError):
            await self.assembly.stack.stop()
        # A driver may start without touching its device (lazy connect), so a clean start does not prove it alive.
        await self.start()


__all__ = ["DriverSession"]
