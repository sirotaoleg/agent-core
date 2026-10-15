# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ends the run once the driver session has lost its connection for good: no further model call."""

from __future__ import annotations

from collections.abc import Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ..driver_session import DriverSession


class ConnectionLostRail(AgentRail):
    """Before each model call, finish the run with the session's loss message when the session reports one."""

    def __init__(self, session_of: Callable[[], DriverSession | None]) -> None:
        super().__init__()
        self._session_of = session_of

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        session = self._session_of()
        lost = None if session is None else session.lost
        if lost is None:
            return
        ctx.request_force_finish({"output": lost, "result_type": "error", "error": lost})


__all__ = ["ConnectionLostRail"]
