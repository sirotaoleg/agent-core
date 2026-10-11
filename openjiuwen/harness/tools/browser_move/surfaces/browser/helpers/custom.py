# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The controller's browser custom actions as commands; each takes the tool context and the action's parameters."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    Click,
    Drag,
    DriverError,
    Locator,
    PointRef,
    Ref,
    RunScript,
)
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import ToolContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate, UploadFile

_MARK = "data-openjiuwen-target-id"
_ALIASES = "Aliases source/target and source_x/source_y/target_x/target_y are also supported."
_COORDINATES = ("coord_source_x", "coord_source_y", "coord_target_x", "coord_target_y")
_STRICT_HINT = (
    "Multiple file inputs matched. Use a more specific selector (e.g. an id like #file-upload) targeting the "
    "visible input."
)

# A selector the page cannot parse (Playwright syntax such as :has-text) falls back to the element with that text.
_POINTS_JS = """(input) => {
  const shown = (element) => {
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const byText = (query, role) => {
    const term = String(query || '').trim().toLowerCase();
    if (!term) return null;
    const all = Array.from(document.querySelectorAll('body *'));
    const score = (element) => {
      const text = String(element.textContent || '').trim().toLowerCase();
      return !text ? -1 : text === term ? 2 : text.includes(term) ? 1 : -1;
    };
    for (const wanted of [2, 1]) {
      for (const element of all.filter((candidate) => score(candidate) === wanted)) {
        const candidates = role === 'target' && element.parentElement ? [element.parentElement, element] : [element];
        const found = candidates.find(shown);
        if (found) return found;
      }
    }
    return null;
  };
  const find = (selector, role) => {
    let element = null;
    try { element = document.querySelector(selector); } catch (error) { element = null; }
    if (element && shown(element)) return element;
    const hasText = String(selector).match(/:has-text\\s*\\(\\s*['"]([^'"]*)['"]\\s*\\)/);
    return byText(hasText ? hasText[1] : selector, role);
  };
  const point = (selector, offset, role, mark) => {
    const element = selector ? find(selector, role) : null;
    if (!element) return null;
    if (mark) element.setAttribute('data-openjiuwen-target-id', mark);
    const box = element.getBoundingClientRect();
    if (offset) return {x: Math.trunc(box.x + offset.x), y: Math.trunc(box.y + offset.y)};
    return {x: Math.trunc(box.x + box.width / 2), y: Math.trunc(box.y + box.height / 2)};
  };
  return {
    source: point(input.source, input.source_offset, 'source', input.source_mark),
    target: point(input.target, input.target_offset, 'target', input.target_mark),
  };
}"""


async def get_element_coordinates(context: ToolContext, **params: Any) -> dict[str, Any]:
    """The centre (or the offset point) of one or two elements found by selector or text, or the given points."""
    ends = _ends(params)
    if not (ends["source"] or ends["points"]):
        return {
            "ok": False,
            "error": (
                "Missing location inputs. Provide at least element_source (element_target is optional), or "
                f"coord_source_x/coord_source_y/coord_target_x/coord_target_y. {_ALIASES}"
            ),
        }
    try:
        refused = await _navigate(context, params)
        if refused:
            return {"ok": False, "error": refused, **_ids(params)}
        if not (ends["source"] or ends["target"]):
            sx, sy, tx, ty = ends["points"]
            return {"ok": True, "source": {"x": sx, "y": sy}, "target": {"x": tx, "y": ty}, "error": None}
        found = await _points(context, ends)
    except DriverError as error:
        return _failure("browser_get_element_coordinates", error, params)
    if not isinstance(found, Mapping):
        return {"ok": False, "error": "Could not parse coordinate result JSON from the page script"}
    source, target = found.get("source"), found.get("target")
    if ends["source"] and source is None:
        error = (
            "Failed to determine source coordinates from selector. Use the exact visible text "
            '(e.g. "Learn more" not "More information") or a valid CSS selector.'
        )
        return {"ok": False, "source": None, "target": None, "error": error}
    if ends["target"] and target is None:
        error = "Failed to determine target coordinates from selector"
        return {"ok": False, "source": source, "target": None, "error": error}
    return {"ok": True, "source": source, "target": target, "error": None}


async def drag_and_drop(context: ToolContext, **params: Any) -> dict[str, Any]:
    """Drag one element onto another (found by selector or text), or from one point to another."""
    ends = _ends(params)
    by_elements = bool(ends["source"] and ends["target"])
    if not (by_elements or ends["points"]):
        return {
            "ok": False,
            "error": (
                "Missing drag inputs. Provide either element_source + element_target, or "
                f"coord_source_x/coord_source_y/coord_target_x/coord_target_y. {_ALIASES}"
            ),
        }
    shape = {"steps": _int(params.get("steps")), "delay_ms": _int(params.get("delay_ms"))}
    try:
        refused = await _navigate(context, params)
        if refused:
            return {"ok": False, "error": refused, **_ids(params)}
        if by_elements:
            marks = (uuid.uuid4().hex[:12], uuid.uuid4().hex[:12])
            found = await _points(context, ends, marks)
            source = found.get("source") if isinstance(found, Mapping) else None
            target = found.get("target") if isinstance(found, Mapping) else None
            if source is None or target is None:
                error = "Failed to determine source or target coordinates from selectors"
                return {"ok": False, "error": error, "source": source, "target": target, **shape}
            refs: tuple[Ref, Ref] = (_marked(context, marks[0]), _marked(context, marks[1]))
            message = f"Dragged element '{ends['source']}' to '{ends['target']}'"
        else:
            sx, sy, tx, ty = ends["points"]
            source, target = {"x": sx, "y": sy}, {"x": tx, "y": ty}
            generation = context.page_state.screenshot_generation
            refs = (PointRef(sx, sy, generation), PointRef(tx, ty, generation))
            message = f"Dragged from ({sx}, {sy}) to ({tx}, {ty})"
        result = await context.execute(Drag(*refs))
    except DriverError as error:
        return _failure("browser_drag_and_drop", error, params)
    error = _error(result)
    return {
        "ok": result.ok,
        "message": None if error else message,
        "source": source,
        "target": target,
        **shape,
        "error": error,
    }


async def set_input_files(context: ToolContext, **params: Any) -> dict[str, Any]:
    """Put files on a file input: directly where the driver sets files on an element, else through its chooser."""
    selector = str(params.get("selector") or "").strip() or 'input[type="file"]'
    paths = [str(path) for path in params.get("paths") or [] if path]
    if not paths:
        return {"ok": False, "error": "paths is required and must be non-empty", **_ids(params)}
    target = Locator(css=selector, text=None, generation=context.page_state.generation_id)
    shape = {"selector": selector, "paths": paths}
    try:
        if Locator in context.driver_info.actions.get(UploadFile, frozenset()):
            result = await context.execute(UploadFile(target, tuple(paths)))
        else:
            result = await context.execute(Click(target))
            if result.ok:
                result = await context.execute(UploadFile(None, tuple(paths)))
    except DriverError as error:
        failure = {"ok": False, "error": str(error), **shape}
        if "strict mode violation" in str(error):
            failure["hint"] = _STRICT_HINT
        return failure
    return {"ok": result.ok, **shape, "error": _error(result)}


CUSTOM_ACTIONS: Mapping[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "browser_get_element_coordinates": get_element_coordinates,
    "browser_drag_and_drop": drag_and_drop,
    "browser_set_input_files": set_input_files,
}


async def _navigate(context: ToolContext, params: Mapping[str, Any]) -> str | None:
    url = str(params.get("url") or "").strip()
    return _error(await context.execute(Navigate(url))) if url else None


async def _points(context: ToolContext, ends: Mapping[str, Any], marks: tuple[str, str] = ("", "")) -> Any:
    args = {
        "source": ends["source"],
        "target": ends["target"],
        "source_offset": ends["source_offset"],
        "target_offset": ends["target_offset"],
        "source_mark": marks[0],
        "target_mark": marks[1],
    }
    return (await context.execute(RunScript(code=_POINTS_JS, args=args))).value


def _marked(context: ToolContext, mark: str) -> Locator:
    return Locator(css=f'[{_MARK}="{mark}"]', text=None, generation=context.page_state.generation_id)


def _ends(params: Mapping[str, Any]) -> dict[str, Any]:
    points = [_int(params.get(name), params.get(name.removeprefix("coord_"))) for name in _COORDINATES]
    return {
        "source": str(params.get("element_source") or params.get("source") or "").strip(),
        "target": str(params.get("element_target") or params.get("target") or "").strip(),
        "source_offset": _offset(params.get("element_source_offset")),
        "target_offset": _offset(params.get("element_target_offset")),
        "points": tuple(points) if None not in points else None,
    }


def _offset(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    x, y = _int(value.get("x")), _int(value.get("y"))
    return None if x is None or y is None else {"x": x, "y": y}


def _int(value: Any, fallback: Any = None) -> int | None:
    for candidate in (value, fallback):
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _error(result: ActionResult) -> str | None:
    if result.ok:
        return None
    return str(result.detail or "the command failed").removeprefix("### Error\n")


def _failure(action: str, error: DriverError, params: Mapping[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": f"{action} failed: {error}", **_ids(params)}


def _ids(params: Mapping[str, Any]) -> dict[str, str]:
    return {"session_id": str(params.get("session_id") or ""), "request_id": str(params.get("request_id") or "")}


__all__ = ["CUSTOM_ACTIONS", "drag_and_drop", "get_element_coordinates", "set_input_files"]
