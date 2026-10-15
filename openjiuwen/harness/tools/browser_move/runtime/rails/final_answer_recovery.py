# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One model call that asks the worker for its final JSON after it ended without one; no tool runs."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

EMPTY_OUTPUT = "empty_output"
NON_JSON_OUTPUT = "non_json_output"
MAX_ITERATIONS_REACHED = "max_iterations_reached"

STEP_LIMIT_SENTENCE = "The step limit has been reached."
_JSON_REPLY = "the final JSON object required by your instructions and nothing else.\n"
RECOVERY_MESSAGE = (
    "Tools are disabled now. Using only what you have already seen in this conversation, reply with "
    f"{_JSON_REPLY}"
    "If the task is not fully complete, set ok=false and say in final what you found and what is missing."
)
RESTATE_MESSAGE = (
    f"Tools are disabled now. Your previous reply already answered the task. Restate that answer as {_JSON_REPLY}"
    "Put the answer in final as you gave it: do not re-check it, change it or add caveats. Set ok=false only if your "
    "previous reply said the task could not be completed."
)

_RECOVERY_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("browser_final_answer_recovery", default=False)


def recovery_reason(output: str, *, max_iterations_reached: bool) -> str:
    if max_iterations_reached:
        return MAX_ITERATIONS_REACHED
    return EMPTY_OUTPUT if not output.strip() else NON_JSON_OUTPUT


def recovery_message(reason: str) -> str:
    if reason == MAX_ITERATIONS_REACHED:
        return f"{STEP_LIMIT_SENTENCE} {RECOVERY_MESSAGE}"
    return RESTATE_MESSAGE if reason == NON_JSON_OUTPUT else RECOVERY_MESSAGE


@contextmanager
def final_answer_recovery() -> Iterator[None]:
    """Mark the worker invoke run inside this block as the recovery call."""
    token = _RECOVERY_ACTIVE.set(True)
    try:
        yield
    finally:
        _RECOVERY_ACTIVE.reset(token)


class FinalAnswerRecoveryRail(AgentRail):
    """During recovery: stop after one model call and run none of its tool calls."""

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if not _RECOVERY_ACTIVE.get():
            return
        response = getattr(ctx.inputs, "response", None)
        if getattr(response, "tool_calls", None):
            ctx.request_force_finish({"output": getattr(response, "content", "") or "", "result_type": "answer"})
