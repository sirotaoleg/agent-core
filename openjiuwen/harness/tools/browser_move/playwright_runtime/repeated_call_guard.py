# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stop the browser worker from looping on tool calls that make no progress."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from ..contract import RunScript
from .tool_factory import CommandRecord, command_record

REPEAT_NOTE_THRESHOLD = 3
REPEAT_WITHHOLD_TOOLS_THRESHOLD = 5

_STATE_KEY = "_browser_repeated_call_guard"
_FINISH_SECTION = "browser_repeated_call_finish"
_NOTE_HEADER = "### Repeated call"
_EVALUATE_TOOL = "browser_evaluate"

# Playwright MCP response parts that change between otherwise identical calls.
_EVENTS_SECTION = re.compile(r"^### Events\n.*?(?=^### |\Z)", re.MULTILINE | re.DOTALL)
_VOLATILE_LINE = re.compile(r"^- (?:New console entries: .*|Console: \d+ errors, \d+ warnings)\n?", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")

# A whitespace-free evaluate function that only returns literals or the page title/URL.
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


def _tool_name(name: str) -> str:
    """Tool name without the ``mcp_<server>_`` prefix the worker sees."""
    start = name.find("browser_")
    return name[start:] if start > 0 else name


def _parse_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return None
    return arguments


def _evaluate_function(tool_name: str, arguments: Any) -> str | None:
    """The whitespace-free ``function`` of a browser_evaluate call, else None."""
    if _tool_name(str(tool_name or "")) != _EVALUATE_TOOL:
        return None
    arguments = _parse_arguments(arguments)
    if not isinstance(arguments, dict) or not isinstance(arguments.get("function"), str):
        return None
    return _WHITESPACE.sub("", arguments["function"])


def _is_noop_evaluate(tool_name: str, arguments: Any) -> bool:
    """True for browser_evaluate calls that only return constants or the page title/URL."""
    function = _evaluate_function(tool_name, arguments)
    return function is not None and _NOOP_FUNCTION.fullmatch(function) is not None


def _normalise_call_key(tool_name: str, arguments: Any) -> str:
    name = _tool_name(str(tool_name or ""))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return json.dumps([name, arguments.strip()])
    function = _evaluate_function(name, arguments)
    if function is not None:
        arguments = {**arguments, "function": _DIGITS.sub("#", function)}
    return json.dumps([name, arguments], sort_keys=True, ensure_ascii=False, default=str)


def _result_fingerprint(content: Any, tool_name: str = "") -> str:
    text = content if isinstance(content, str) else json.dumps(content, sort_keys=True, default=str)
    text = _VOLATILE_LINE.sub("", _EVENTS_SECTION.sub("", text.replace("\r\n", "\n")))
    if _tool_name(str(tool_name or "")) == _EVALUATE_TOOL:
        text = _DIGITS.sub("#", text)
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


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


def _append_note(content: str, note: str) -> str:
    """Append the note inside the ``{"result": ...}`` envelope when the content uses one."""
    try:
        envelope = json.loads(content)
    except ValueError:
        envelope = None
    if isinstance(envelope, dict) and list(envelope) == ["result"] and isinstance(envelope["result"], str):
        return json.dumps({"result": f"{envelope['result'].rstrip()}\n{note}"}, ensure_ascii=False)
    return f"{content.rstrip()}\n{note}"


class RepeatedCallGuardRail(AgentRail):
    """Warn on the 3rd identical or no-op call in a row; withhold tools for the next model call after the 5th."""

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        ctx.extra[_STATE_KEY] = _RepeatState()
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            builder.remove_section(_FINISH_SECTION)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        state = ctx.extra.get(_STATE_KEY)
        inputs = ctx.inputs
        tool_msg = getattr(inputs, "tool_msg", None)
        if not isinstance(state, _RepeatState) or tool_msg is None:
            return
        tool_name = getattr(inputs, "tool_name", "")
        tool_args = getattr(inputs, "tool_args", None)
        key = _normalise_call_key(tool_name, tool_args)
        fingerprint = _result_fingerprint(tool_msg.content, tool_name)
        if key == state.last_key and fingerprint == state.last_fingerprint:
            state.repeat_count += 1
        else:
            state.last_key, state.last_fingerprint, state.repeat_count = key, fingerprint, 1
        state.noop_count = state.noop_count + 1 if _is_noop_evaluate(tool_name, tool_args) else 0

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
            inputs.tool_msg = tool_msg.model_copy(update={"content": _append_note(tool_msg.content, note)})

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        state = ctx.extra.get(_STATE_KEY)
        if not isinstance(state, _RepeatState) or not state.withhold_tools:
            return
        count = max(state.repeat_count, state.noop_count)
        logger.info(
            "Browser worker repeated the same tool call %d times; withholding tools for this model call.",
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


class CommandRepeatGuardRail(RepeatedCallGuardRail):
    """``RepeatedCallGuardRail`` on command records: a call repeats when its masked command and its result do."""

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        state = ctx.extra.get(_STATE_KEY)
        inputs = ctx.inputs
        tool_msg = getattr(inputs, "tool_msg", None)
        if not isinstance(state, _RepeatState) or tool_msg is None:
            return
        record = command_record(getattr(inputs, "tool_result", None))
        if record is None:
            # Nothing was sent; the call itself is all there is to compare.
            key = _normalise_call_key(getattr(inputs, "tool_name", ""), getattr(inputs, "tool_args", None))
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
