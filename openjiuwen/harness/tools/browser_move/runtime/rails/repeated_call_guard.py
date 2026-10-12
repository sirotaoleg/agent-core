# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stop the worker from looping on tool calls that make no progress."""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ...contract import RunScript
from ..tool_factory import CommandRecord, command_record

REPEAT_NOTE_THRESHOLD = 3
REPEAT_WITHHOLD_TOOLS_THRESHOLD = 5

_STATE_KEY = "_browser_repeated_call_guard"
_FINISH_SECTION = "browser_repeated_call_finish"
_NOTE_HEADER = "### Repeated call"

_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")

# A whitespace-free evaluate function that only returns literals or the document title/location.
_NOOP_ATOM = (
    r"(?:-?\d+(?:\.\d+)?|'[^'\\]*'|\"[^\"\\]*\"|`[^`$\\]*`|true|false|null|undefined"
    r"|document\.title|document\.URL|(?:window\.)?location\.href)"
)
_NOOP_KEY = r"(?:[A-Za-z_$][\w$]*|'[^'\\]*'|\"[^\"\\]*\")"
_NOOP_OBJECT = rf"\{{(?:{_NOOP_KEY}:{_NOOP_ATOM}(?:,{_NOOP_KEY}:{_NOOP_ATOM})*,?)?\}}"
_NOOP_ARRAY = rf"\[(?:{_NOOP_ATOM}(?:,{_NOOP_ATOM})*,?)?\]"
_NOOP_VALUE = rf"(?:{_NOOP_ATOM}|{_NOOP_ARRAY}|\({_NOOP_OBJECT}\)|\({_NOOP_ATOM}\)|\({_NOOP_ARRAY}\))"
_NOOP_BLOCK_VALUE = rf"(?:{_NOOP_VALUE}|{_NOOP_OBJECT})"
_NOOP_FUNCTION = re.compile(rf"(?:async)?\(\)=>(?:{_NOOP_VALUE}|\{{return{_NOOP_BLOCK_VALUE};?\}});?")


@dataclass
class _RepeatState:
    last_key: Any = None
    last_fingerprint: Any = None
    repeat_count: int = 0
    noop_count: int = 0
    withhold_tools: bool = False


def _call_key(tool_name: str, arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return json.dumps([tool_name, arguments.strip()])
    return json.dumps([tool_name, arguments], sort_keys=True, ensure_ascii=False, default=str)


def _record_identity(record: CommandRecord) -> tuple[Any, Any]:
    """(masked command, result); a script compares without whitespace and with its digits masked."""
    action, value = record.action, record.value
    if isinstance(action, RunScript):
        action = dataclasses.replace(action, code=_DIGITS.sub("#", _WHITESPACE.sub("", action.code)))
        value = _DIGITS.sub("#", json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
    return action, (record.ok, record.error, value, record.surface)


def _is_noop_script(record: CommandRecord | None) -> bool:
    action = record.action if record is not None else None
    return isinstance(action, RunScript) and _NOOP_FUNCTION.fullmatch(_WHITESPACE.sub("", action.code)) is not None


def _repeat_note(repeat_count: int) -> str:
    return (
        f"{_NOTE_HEADER}\n"
        f"This exact call has returned the same result {repeat_count} times in a row; "
        "repeating it will not change anything.\n"
        "If you already have what the task needs, return the final JSON answer now. "
        "Otherwise take a different action."
    )


def _noop_note(noop_count: int) -> str:
    return (
        f"{_NOTE_HEADER}\n"
        f"The last {noop_count} calls only returned constants or the page title/URL; "
        "they do not change the page or give new information.\n"
        "If you already have what the task needs, return the final JSON answer now. "
        "Otherwise take a different action."
    )


class RepeatedCallGuardRail(AgentRail):
    """Warn on the 3rd identical or no-op call in a row; withhold tools for the next model call after the 5th."""

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._start(ctx)

    @staticmethod
    def _start(ctx: AgentCallbackContext) -> _RepeatState:
        state = ctx.extra[_STATE_KEY] = _RepeatState()
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            builder.remove_section(_FINISH_SECTION)
        return state

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        state = ctx.extra.get(_STATE_KEY)
        inputs = ctx.inputs
        tool_msg = getattr(inputs, "tool_msg", None)
        if not isinstance(state, _RepeatState) or tool_msg is None:
            return
        record = command_record(getattr(inputs, "tool_result", None))
        if record is None:
            key = _call_key(getattr(inputs, "tool_name", ""), getattr(inputs, "tool_args", None))
            fingerprint = None
        else:
            key, fingerprint = _record_identity(record)
        if key == state.last_key and fingerprint == state.last_fingerprint:
            state.repeat_count += 1
        else:
            state.last_key, state.last_fingerprint, state.repeat_count = key, fingerprint, 1
        state.noop_count = state.noop_count + 1 if _is_noop_script(record) else 0

        count = max(state.repeat_count, state.noop_count)
        if count < REPEAT_NOTE_THRESHOLD:
            return
        if count >= REPEAT_WITHHOLD_TOOLS_THRESHOLD:
            state.withhold_tools = True
        if isinstance(tool_msg.content, str) and _NOTE_HEADER not in tool_msg.content:
            if state.noop_count > state.repeat_count:
                note = _noop_note(state.noop_count)
            else:
                note = _repeat_note(state.repeat_count)
            inputs.tool_msg = tool_msg.model_copy(update={"content": f"{tool_msg.content.rstrip()}\n{note}"})

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        # A deep agent fires before_invoke on an outer context; its inner agent's first model call starts the run.
        state = ctx.extra.get(_STATE_KEY)
        if not isinstance(state, _RepeatState):
            state = self._start(ctx)
        if not state.withhold_tools:
            return
        count = max(state.repeat_count, state.noop_count)
        logger.info(
            "Worker repeated the same tool call %d times; withholding tools for this model call.",
            count,
        )
        ctx.inputs.tools = []
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            text = (
                f"The same tool call returned the same result {count} times in a row. "
                "Tools are disabled for this reply. Return the final JSON answer now from what you "
                "already have; if the task is not complete, set ok=false and fill the progress fields."
            )
            builder.add_section(
                PromptSection(
                    name=_FINISH_SECTION,
                    content={"en": text, "cn": text},
                    priority=100,
                )
            )
