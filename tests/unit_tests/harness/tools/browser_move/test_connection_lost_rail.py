# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ConnectionLostRail``: a session lost for good ends the run before the next model call."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.browser_move.runtime.rails.connection_lost import ConnectionLostRail

LOST = "the connection was lost again; the task cannot continue"


def _before_model_call(session: object) -> AgentCallbackContext:
    ctx = AgentCallbackContext(agent=MagicMock())
    asyncio.run(ConnectionLostRail(lambda: session).before_model_call(ctx))
    return ctx


def test_a_lost_session_ends_the_run_with_its_message_as_the_error() -> None:
    ctx = _before_model_call(SimpleNamespace(lost=LOST))

    assert ctx.consume_force_finish().result == {"output": LOST, "result_type": "error", "error": LOST}


def test_a_live_session_and_a_session_not_opened_yet_let_the_model_call_through() -> None:
    assert _before_model_call(SimpleNamespace(lost=None)).has_force_finish_request is False
    assert _before_model_call(None).has_force_finish_request is False
