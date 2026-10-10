# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``browser_find``: text or regex search over the snapshot of the last observation."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.tools.browser_move.contract import DriverError, Observe

if TYPE_CHECKING:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolContext

_FIND = "browser_find"
_FIND_DESC = (
    "Search the current accessibility / page snapshot for text or a regex. "
    "Returns matching nodes with target_id/ref and short context. "
    "Requires a prior browser_snapshot (or probe) for the current generation."
)
_FIND_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Text or regex pattern to search for in the current snapshot.",
        },
        "regex": {
            "type": "boolean",
            "description": "Treat query as a regular expression. Default false.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum matches to return (1-100). Default 20.",
        },
        "generation_id": {
            "type": "string",
            "description": "Optional PageState generation_id; defaults to current.",
        },
    },
    "required": ["query"],
}
_ERROR = "### Error"
_CONTEXT_LINES = 3
_FENCED_YAML = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_REGEX_LITERAL = re.compile(r"\A/(.*)/([a-z]*)\Z", re.DOTALL)
_REGEX_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


class FindTool(Tool):
    """Searches the last observation's text like the server's own find: matches in context, under their path."""

    def __init__(self, context: ToolContext) -> None:
        native = context.driver_info.native_tools.get(_FIND)
        description, schema = (native.description, native.schema) if native is not None else (_FIND_DESC, _FIND_PARAMS)
        name = context.driver_info.tool_name_prefix + _FIND
        super().__init__(ToolCard(id=name, name=name, description=description, input_params=dict(schema)))
        self._context = context

    async def invoke(self, inputs: Mapping[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        page_state = self._context.page_state
        try:
            inputs = inputs or {}
            if inputs.get("generation_id"):
                page_state.validate_generation(str(inputs["generation_id"]))
            query, matches, limit = _search(inputs)
            observation = page_state.last_observation
            if observation is None:
                observation = page_state.record(Observe(), await self._context.driver.observe(Observe()))
        except (ValueError, DriverError) as error:
            return ToolOutput(success=False, error=f"{_ERROR}\n{error}")
        found = _find(_snapshot(observation.native_text or ""), matches, limit)
        return ToolOutput(success=True, data={"content": f"### Result\n{_answer(query, found)}"})

    async def stream(self, inputs: Mapping[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)


def _find(text: str, matches: Callable[[str], bool], limit: int | None) -> tuple[int, list[str]]:
    lines = text.split("\n")
    indents = [len(line) - len(line.lstrip()) for line in lines]
    matched = [index for index, line in enumerate(lines) if matches(line)][:limit]
    windows: list[list[int]] = []
    for line in matched:
        start, end = max(0, line - _CONTEXT_LINES), min(len(lines) - 1, line + _CONTEXT_LINES)
        if windows and start <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    on_path = set(matched)
    for line in matched:
        on_path.update(_ancestors(lines, indents, line))
    return len(matched), [_snippet(lines, indents, on_path, start, end) for start, end in windows]


def _snippet(lines: list[str], indents: list[int], on_path: set[int], start: int, end: int) -> str:
    indices = [*_ancestors(lines, indents, start), *range(start, end + 1)]
    out = []
    for position, index in enumerate(indices):
        previous = indices[position - 1]
        if position and index > previous + 1 and index not in on_path and previous not in on_path:
            out.append(" " * indents[index] + "...")
        out.append(lines[index])
    return "\n".join(out)


def _ancestors(lines: list[str], indents: list[int], index: int) -> list[int]:
    found = []
    indent = indents[index]
    for line in range(index - 1, -1, -1):
        if indent <= 0:
            break
        if lines[line].strip() and indents[line] < indent:
            found.append(line)
            indent = indents[line]
    return found[::-1]


def _search(args: Mapping[str, Any]) -> tuple[str, Callable[[str], bool], int | None]:
    limit = None
    if "query" in args:
        limit = max(1, min(100, int(args.get("limit") or 20)))
        query = str(args.get("query") or "")
        text, pattern = ("", query) if args.get("regex") is True else (query, "")
    else:
        text, pattern = str(args.get("text") or ""), str(args.get("regex") or "")
    if not text and not pattern:
        raise ValueError('Provide either "text" or "regex" to search for.')
    if text and pattern:
        raise ValueError('Provide only one of "text" or "regex", not both.')
    if text:
        needle = text.lower()
        return f'"{text}"', lambda line: needle in line.lower(), limit
    compiled, shown = _compile(pattern)
    return shown, lambda line: compiled.search(line) is not None, limit


def _compile(source: str) -> tuple[re.Pattern[str], str]:
    literal = _REGEX_LITERAL.match(source)
    pattern, flags = (literal.group(1), literal.group(2).replace("g", "")) if literal else (source, "")
    try:
        compiled = re.compile(pattern, sum(_REGEX_FLAGS.get(flag, 0) for flag in set(flags)))
    except re.error as error:
        raise ValueError(f"Invalid regular expression: {error}") from error
    return compiled, f"/{pattern}/{''.join(sorted(set(flags)))}"


def _snapshot(text: str) -> str:
    fenced = _FENCED_YAML.search(text)
    return fenced.group(1).rstrip("\n") if fenced else text


def _answer(query: str, found: tuple[int, list[str]]) -> str:
    count, snippets = found
    if not count:
        return f"No matches found for {query}."
    word = "match" if count == 1 else "matches"
    return f"Found {count} {word} for {query}:\n\n" + "\n\n----\n\n".join(snippets)
