# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Arguments of the @playwright/mcp tools decoded into commands: the inverse of the driver's handlers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverRef,
    HandleDialog,
    Hover,
    Locator,
    Observe,
    PointRef,
    PressKey,
    RefResolver,
    RunScript,
    Scroll,
    SwitchContext,
    TypeText,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import ARIA_REF
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FieldKind,
    FillForm,
    FormField,
    GetConsole,
    GetNetwork,
    GetPdf,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)

Builder = Callable[[Mapping[str, Any], RefResolver], Action]

_FIELD_KINDS = {kind.value: kind for kind in FieldKind}


def _required(args: Mapping[str, Any], name: str) -> Any:
    value = args.get(name)
    if value is None:
        raise ValueError(f"'{name}' is required")
    return value


def _number(args: Mapping[str, Any], name: str, default: int | None = None) -> int:
    value = args.get(name, default)
    if value is None:
        raise ValueError(f"'{name}' is required")
    try:
        return round(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be a number") from None


def _strings(args: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = args.get(name) or []
    if not isinstance(value, list):
        raise ValueError(f"'{name}' must be a list of strings")
    return tuple(str(item) for item in value)


def _target(value: Any, resolver: RefResolver) -> DriverRef | Locator:
    target = str(value or "")
    if not target:
        raise ValueError("'target' is required")
    # The model sends no generation: a ref belongs to the observation it was last seen in.
    generation = resolver.generation_of(target)
    if ARIA_REF.fullmatch(target):
        return DriverRef(target, generation)
    return Locator(css=target, text=None, generation=generation)


def _point(args: Mapping[str, Any], x: str, y: str, resolver: RefResolver) -> PointRef:
    return PointRef(_number(args, x), _number(args, y), resolver.screenshot_generation)


def _current_context(resolver: RefResolver) -> ContextRef:
    context = resolver.current_context
    if context is None:
        raise ValueError("no open tab")
    return context


def build_click(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Click(
        _target(args.get("target"), resolver),
        button=str(args.get("button") or "left"),
        modifiers=_strings(args, "modifiers"),
        count=2 if args.get("doubleClick") else 1,
    )


def build_type(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    slowly = bool(args.get("slowly"))
    return TypeText(
        _target(args.get("target"), resolver),
        str(_required(args, "text")),
        submit=bool(args.get("submit")),
        clear=not slowly,
        slowly=slowly,
    )


def build_press_key(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return PressKey(str(_required(args, "key")))


def build_hover(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Hover(_target(args.get("target"), resolver))


def build_drag(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Drag(_target(args.get("startTarget"), resolver), _target(args.get("endTarget"), resolver))


def build_select_option(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    values = _strings(args, "values")
    if not values:
        raise ValueError("'values' must name at least one option")
    return SelectOption(_target(args.get("target"), resolver), values)


def build_fill_form(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    raw_fields = args.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("'fields' must be a non-empty list")
    fields = []
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, Mapping):
            raise ValueError(f"fields[{index}] must be an object")
        kind = _FIELD_KINDS.get(str(raw.get("type") or ""))
        if kind is None:
            raise ValueError(f"fields[{index}] unsupported type={raw.get('type')!r}")
        fields.append(FormField(_target(raw.get("target"), resolver), kind, str(_required(raw, "value"))))
    return FillForm(tuple(fields))


def build_drop(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    data = args.get("data") or {}
    if not isinstance(data, Mapping):
        raise ValueError("'data' must map MIME types to strings")
    pairs = tuple((str(mime), str(value)) for mime, value in data.items())
    return Drop(_target(args.get("target"), resolver), paths=_strings(args, "paths"), data=pairs)


def build_file_upload(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return UploadFile(None, _strings(args, "paths"))


def build_handle_dialog(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    prompt = args.get("promptText")
    return HandleDialog(bool(_required(args, "accept")), None if prompt is None else str(prompt))


def build_evaluate(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    target = _target(args["target"], resolver) if args.get("target") else None
    return RunScript(str(_required(args, "function")), target=target)


def build_navigate(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Navigate(str(_required(args, "url")))


def build_navigate_back(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Back()


def build_snapshot(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe()


def build_take_screenshot(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe(include_screenshot=True, full_page=bool(args.get("fullPage")))


def build_tabs(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    action = str(_required(args, "action"))
    if action == "list":
        return Observe()
    if action == "new":
        url = args.get("url")
        return OpenContext(None if url is None else str(url))
    if action not in ("select", "close"):
        raise ValueError(f"action must be one of: list, new, close, select (got {action!r})")
    if args.get("index") is None:
        if action == "select":
            raise ValueError("action=select requires index")
        return CloseContext(_current_context(resolver))
    index, contexts = _number(args, "index"), resolver.contexts
    if not 0 <= index < len(contexts):
        raise ValueError(f"tab index {index} is out of range: {len(contexts)} tab(s) open")
    return SwitchContext(contexts[index]) if action == "select" else CloseContext(contexts[index])


def build_close(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return CloseContext(_current_context(resolver))


def build_mouse_click_xy(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Click(
        _point(args, "x", "y", resolver), button=str(args.get("button") or "left"), count=_number(args, "clickCount", 1)
    )


def build_mouse_move_xy(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Hover(_point(args, "x", "y", resolver))


def build_mouse_drag_xy(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Drag(_point(args, "startX", "startY", resolver), _point(args, "endX", "endY", resolver))


def build_mouse_wheel(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Scroll(None, dx=_number(args, "deltaX", 0), dy=_number(args, "deltaY", 0))


def build_pdf_save(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    filename = args.get("filename")
    return GetPdf(None if filename is None else str(filename))


def build_console_messages(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return GetConsole(level=str(args.get("level") or "info"), all=bool(args.get("all")))


def build_network_requests(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    url_filter = args.get("filter")
    return GetNetwork(static=bool(args.get("static")), filter=None if url_filter is None else str(url_filter))


# Arguments not decoded here (element, filename, depth, boxes, type, scale, delay) do not change the command.
DECODERS: Mapping[str, Builder] = MappingProxyType(
    {
        "browser_click": build_click,
        "browser_type": build_type,
        "browser_press_key": build_press_key,
        "browser_hover": build_hover,
        "browser_drag": build_drag,
        "browser_select_option": build_select_option,
        "browser_fill_form": build_fill_form,
        "browser_drop": build_drop,
        "browser_file_upload": build_file_upload,
        "browser_handle_dialog": build_handle_dialog,
        "browser_evaluate": build_evaluate,
        "browser_navigate": build_navigate,
        "browser_navigate_back": build_navigate_back,
        "browser_snapshot": build_snapshot,
        "browser_take_screenshot": build_take_screenshot,
        "browser_tabs": build_tabs,
        "browser_close": build_close,
        "browser_mouse_click_xy": build_mouse_click_xy,
        "browser_mouse_move_xy": build_mouse_move_xy,
        "browser_mouse_drag_xy": build_mouse_drag_xy,
        "browser_mouse_wheel": build_mouse_wheel,
        "browser_pdf_save": build_pdf_save,
        "browser_console_messages": build_console_messages,
        "browser_network_requests": build_network_requests,
    }
)

__all__ = ["Builder", "DECODERS"]
