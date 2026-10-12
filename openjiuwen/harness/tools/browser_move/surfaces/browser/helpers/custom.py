# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The controller's browser custom actions as commands; each takes the tool context and the action's parameters."""

from __future__ import annotations

import functools
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
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
from openjiuwen.harness.tools.browser_move.runtime.controllers.action import ActionController
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import ToolContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate, UploadFile
from openjiuwen.harness.tools.browser_move.surfaces.browser.policy import policy_denial, policy_rules

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
    denial = policy_denial(UploadFile(target, tuple(paths)), None, policy_rules(context.settings))
    if denial:
        return {"ok": False, "error": denial, **shape}
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


def register_custom_actions(controller: ActionController, context: ToolContext) -> None:
    """Register the custom actions on the controller as handlers over the context, with their specs."""
    register_custom_action_specs(controller, upload_root=context.settings.upload_root)
    for name, handler in CUSTOM_ACTIONS.items():
        controller.register_action(name, functools.partial(handler, context))


def register_custom_action_specs(controller: ActionController, *, upload_root: Path | None) -> None:
    """The three custom actions' specs and the list_upload_files action over the upload root."""

    async def list_upload_files(
        session_id: str = "",
        request_id: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        del kwargs
        if upload_root is None:
            return {
                "ok": False,
                "error": (
                    "BROWSER_UPLOAD_ROOT is not configured. "
                    "Set this env var to the directory where uploadable files are stored."
                ),
                "files": [],
                "session_id": session_id,
                "request_id": request_id,
            }
        if not upload_root.exists():
            return {
                "ok": False,
                "error": f"Upload root directory does not exist: {upload_root}",
                "files": [],
                "upload_root": str(upload_root),
                "session_id": session_id,
                "request_id": request_id,
            }
        files = _list_dir_files(upload_root)
        return {
            "ok": True,
            "upload_root": str(upload_root),
            "files": files,
            "session_id": session_id,
            "request_id": request_id,
        }

    controller.register_action_spec(
        "browser_get_element_coordinates",
        summary="Resolves source/target screen coordinates by selectors or explicit coordinates.",
        when_to_use=(
            "Use when you need coordinates for one element (element_source only) or two (source + target). "
            "element_target is optional."
        ),
        params={
            "url": "string: optional URL to open before resolving coordinates",
            "element_source": "string: source selector/text alias (required for selector mode)",
            "element_target": "string: target selector/text alias (optional)",
            "coord_source_x": "int: source x coordinate",
            "coord_source_y": "int: source y coordinate",
            "coord_target_x": "int: target x coordinate",
            "coord_target_y": "int: target y coordinate",
            "source/target": "string aliases for element_source/element_target",
            "source_x/source_y/target_x/target_y": "int aliases for coord_* fields",
        },
    )
    controller.register_action_spec(
        "browser_drag_and_drop",
        summary="Performs drag-and-drop using selectors or explicit coordinates.",
        when_to_use=("Use for drag-and-drop tasks instead of generic browser_run_task text-only instructions."),
        params={
            "url": "string: optional URL to open before drag-and-drop",
            "element_source": "string: source selector/text alias",
            "element_target": "string: target selector/text alias",
            "coord_source_x": "int: source x coordinate",
            "coord_source_y": "int: source y coordinate",
            "coord_target_x": "int: target x coordinate",
            "coord_target_y": "int: target y coordinate",
            "steps": "int: optional drag interpolation steps",
            "delay_ms": "int: optional delay between drag steps",
            "source/target": "string aliases for element_source/element_target",
            "source_x/source_y/target_x/target_y": "int aliases for coord_* fields",
        },
    )
    controller.register_action_spec(
        "browser_set_input_files",
        summary=(
            "Sets files on an <input type='file'> element. Requires prior page inspection"
            " to select the correct input — do not call this without first reading the page snapshot."
        ),
        when_to_use=(
            "Use for all file upload tasks. Does NOT require a file chooser dialog — sets files directly "
            "on the DOM element. Call list_upload_files first to get absolute paths, then call this action. "
            "IMPORTANT: pages may have multiple file inputs (e.g. a visible input plus a hidden Dropzone input). "
            "Always prefer a specific selector such as '#file-upload' or 'input#id' over the generic "
            "'input[type=\"file\"]' to avoid strict mode violations. If you have not yet inspected the page, "
            "delegate this action to browser_run_task so the worker can read the page snapshot first."
        ),
        params={
            "selector": (
                "string: CSS/Playwright selector targeting exactly one file input "
                "(default: 'input[type=\"file\"]'). Use a specific ID selector like '#file-upload' "
                "when the page has more than one file input element."
            ),
            "paths": (
                "list[string]: absolute file paths to set on the input (required, non-empty). "
                "Parameter name is 'paths' — not 'files', not 'file_paths'."
            ),
        },
    )
    controller.register_action(
        "list_upload_files",
        list_upload_files,
        overwrite=True,
    )
    controller.register_action_spec(
        "list_upload_files",
        summary="Lists files available for upload from the configured BROWSER_UPLOAD_ROOT directory.",
        when_to_use=(
            "Call this to discover what files are available and get their exact absolute paths "
            "before calling browser_set_input_files to attach them to a file input. "
            "Returns a list of {name, path, size_bytes} entries."
        ),
    )


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


def _list_dir_files(root: Path) -> list[dict[str, Any]]:
    """Return a flat list of files under *root* with name, path, and size."""
    entries: list[dict[str, Any]] = []
    try:
        for item in sorted(root.iterdir()):
            if item.is_file():
                try:
                    size = item.stat().st_size
                except OSError:
                    size = -1
                entries.append({"name": item.name, "path": str(item), "size_bytes": size})
    except OSError:
        pass
    return entries


def _ids(params: Mapping[str, Any]) -> dict[str, str]:
    return {"session_id": str(params.get("session_id") or ""), "request_id": str(params.get("request_id") or "")}


__all__ = [
    "CUSTOM_ACTIONS",
    "drag_and_drop",
    "get_element_coordinates",
    "register_custom_action_specs",
    "register_custom_actions",
    "set_input_files",
]
