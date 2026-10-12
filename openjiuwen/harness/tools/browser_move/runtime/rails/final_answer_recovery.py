# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One tool-free model call that asks the worker for its final JSON after it ended without one."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

EMPTY_OUTPUT = "empty_output"
NON_JSON_OUTPUT = "non_json_output"
MAX_ITERATIONS_REACHED = "max_iterations_reached"

STEP_LIMIT_SENTENCE = "The step limit has been reached."
RECOVERY_MESSAGE = (
    "Tools are disabled now. Using only what you have already seen in this conversation, reply with the final "
    "JSON object required by your instructions (ok, final, page, screenshot, error, and the progress fields) "
    "and nothing else.\n"
    "If the task is not fully complete, set ok=false and say in final what you found and what is missing."
)

_RECOVERY_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("browser_final_answer_recovery", default=False)


def recovery_reason(output: str, *, max_iterations_reached: bool) -> str:
    if max_iterations_reached:
        return MAX_ITERATIONS_REACHED
    return EMPTY_OUTPUT if not output.strip() else NON_JSON_OUTPUT


def recovery_message(reason: str) -> str:
    if reason == MAX_ITERATIONS_REACHED:
        return f"{STEP_LIMIT_SENTENCE} {RECOVERY_MESSAGE}"
    return RECOVERY_MESSAGE


@contextmanager
def final_answer_recovery() -> Iterator[None]:
    """Mark the worker invoke run inside this block as the recovery call."""
    token = _RECOVERY_ACTIVE.set(True)
    try:
        yield
    finally:
        _RECOVERY_ACTIVE.reset(token)


class FinalAnswerRecoveryRail(AgentRail):
    """During recovery: no tools, and stop after one model call."""

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if _RECOVERY_ACTIVE.get():
            ctx.inputs.tools = []

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if not _RECOVERY_ACTIVE.get():
            return
        response = getattr(ctx.inputs, "response", None)
        if getattr(response, "tool_calls", None):
            ctx.request_force_finish({"output": getattr(response, "content", "") or "", "result_type": "answer"})
