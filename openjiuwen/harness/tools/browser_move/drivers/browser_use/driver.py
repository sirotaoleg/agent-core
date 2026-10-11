# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``BrowserUseDriver``: the driver contract on top of the browser-use sidecar; imports no ``browser_use``."""

from __future__ import annotations

import asyncio
import base64
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    AmbiguousElement,
    Click,
    CloseContext,
    ConnectionLost,
    ContextRef,
    Drag,
    DriverError,
    DriverInfo,
    DriverRef,
    Element,
    ElementNotFound,
    HandleDialog,
    HandlerDriver,
    Health,
    Hover,
    Locator,
    NavigationError,
    Observation,
    Observe,
    PressKey,
    RunScript,
    Screenshot,
    Scroll,
    StaleRef,
    SwitchContext,
    TypeText,
    Unsupported,
    Wait,
    handles,
)
from openjiuwen.harness.tools.browser_move.drivers.browser_use.transport import SidecarTransport
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import get_browser_agent_logger
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FieldKind,
    FillForm,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.utils.upload_paths import resolve_upload_file_paths

_logger = get_browser_agent_logger()

_CONTEXT_KIND = "tab"
# Element-scoped scripts: the target is stamped with this attribute, then found by the returned selector.
_TARGET_ATTRIBUTE = "data-openjiuwen-target-id"
_SCOPED_SCRIPT = (
    "(input) => {{ const element = document.querySelector(input.selector); "
    "if (!element) {{ throw new Error('target element not found: ' + input.selector); }} "
    "return ({code})(element, input.args); }}"
)
_HAS_TEXT_JS = """(text) => {
  const body = (document.body && document.body.innerText) ? document.body.innerText : '';
  return body.includes(String(text || ''));
}"""
# browser-use's state summary takes the title from its cached target info, which lags behind ``document.title``.
_DOCUMENT_TITLE_JS = "() => document.title"
_WAIT_TIMEOUT_S = 10.0
_WAIT_POLL_S = 0.1
_CHECKED_VALUES = frozenset({"1", "true", "yes", "on", "checked"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# dispatched=False only where the sidecar provably raises before any input reaches the page: refs are resolved
# before the first input event and unsupported features are rejected up front. Unlisted codes are unclassified.
_SIDECAR_ERRORS: dict[str, tuple[type[DriverError], bool]] = {
    "StaleIndexError": (StaleRef, False),
    "ElementNotFound": (ElementNotFound, False),
    "StaleNodeError": (StaleRef, False),
    "AmbiguousElement": (AmbiguousElement, True),
    "ActionTimeout": (ActionTimeout, True),
    "NavigationError": (NavigationError, True),
    "DriverNotConnected": (ConnectionLost, False),
    "DriverConnectionError": (ConnectionLost, True),
    "DriverConnectionLost": (ConnectionLost, True),
    "DriverUnsupported": (Unsupported, False),
    "ActionIntercepted": (ActionIntercepted, False),
}
# A dead browser reaches the driver as a plain error once browser-use gives up reconnecting to it; a target that
# detached under it fails the connect the same way.
_DISCONNECTED = re.compile(
    r"\bnot connected\b|Reconnection wait timed out|No valid agent focus available|target may have detached",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrowserUseConfig:
    """What ``start()`` needs: the CDP endpoint of the browser and the connect timeout."""

    cdp_url: str
    timeout_s: float = 30.0
    sidecar_python: Path | None = None
    upload_root: Path | None = None


def _translate_error(method: str, exc: Exception) -> DriverError:
    code = type(exc).__name__
    known = _SIDECAR_ERRORS.get(code)
    if known is None and _DISCONNECTED.search(str(exc)):
        return ConnectionLost(f"{method}: {exc}", dispatched=True)
    if known is None:
        _logger.warning("browser-use sidecar error on %s not classified: %s: %s", method, code, exc)
        return DriverError(f"{method}: {exc}", dispatched=True)
    error_type, dispatched = known
    if error_type is ActionIntercepted:
        return ActionIntercepted(str(exc), by=str(getattr(exc, "by", "")), dispatched=dispatched)
    return error_type(str(exc), dispatched=dispatched)


def _ref_to_wire(ref: DriverRef | Locator, driver_generation: int) -> dict[str, Any]:
    if isinstance(ref, Locator):
        if ref.text is not None or not ref.css:
            raise Unsupported("BrowserUseDriver addresses a Locator by css only", dispatched=False)
        return {"kind": "selector", "css": ref.css, "nth": 0}
    return {"kind": "driver", "handle": ref.ref, "driver_generation": driver_generation}


def _context_to_wire(context: ContextRef) -> dict[str, Any]:
    return {"target_id": context.id, "url": "", "title": context.title or "", "active": False}


def _context_from_wire(data: dict[str, Any]) -> ContextRef:
    return ContextRef(id=str(data["target_id"]), kind=_CONTEXT_KIND, title=str(data.get("title", "")))


def _element_from_wire(data: dict[str, Any]) -> Element:
    box = data.get("box")
    value = data.get("value")
    return Element(
        ref=DriverRef(ref=str(data["driver_ref"]["handle"]), generation=""),
        role=str(data.get("role") or ""),
        name=str(data.get("name") or ""),
        value=None if value is None else str(value),
        bounds=None if box is None else (float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"])),
        extra={
            "index": int(data["index"]),
            "tag": str(data.get("tag", "")),
            "backend_node_id": data.get("backend_node_id"),
            "frame_id": data.get("frame_id"),
            "attributes": dict(data.get("attributes") or {}),
            "visible": bool(data.get("visible", False)),
            "scrollable": bool(data.get("scrollable", False)),
        },
    )


def _dialog_text(dialog: dict[str, Any] | None) -> str | None:
    return None if dialog is None else f"{dialog.get('type') or 'dialog'}: {dialog.get('message', '')}"


def _screenshot_from_b64(data: str) -> Screenshot:
    png = base64.b64decode(data)
    width, height = struct.unpack(">II", png[16:24]) if png[:8] == _PNG_SIGNATURE and len(png) >= 24 else (0, 0)
    return Screenshot(png=png, width=width, height=height)


def _tab_fields(raw_tabs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    # The sidecar marks a tab active when its URL equals the page URL; the first such tab is the focused one.
    active = [index for index, raw in enumerate(raw_tabs) if raw.get("active")]
    return {
        "tabs": tuple(_context_from_wire(tab) for tab in raw_tabs),
        "tab_urls": tuple(str(tab.get("url", "")) for tab in raw_tabs),
        "current_tab": active[0] if active else None,
    }


def _observation_from_wire(data: dict[str, Any], screenshot_b64: str | None) -> Observation:
    tab_fields = _tab_fields(data.get("tabs") or ())
    title = str(data.get("title", ""))
    current = tab_fields["current_tab"]
    focused = ContextRef(id="", kind=_CONTEXT_KIND, title=title) if current is None else tab_fields["tabs"][current]
    return Observation(
        context=focused,
        elements=tuple(_element_from_wire(element) for element in data.get("elements") or ()),
        screenshot=None if not screenshot_b64 else _screenshot_from_b64(screenshot_b64),
        native_text=data.get("ax_text"),
        surface=BrowserContext(
            url=str(data["url"]),
            title=title,
            **tab_fields,
            is_pdf_viewer=bool(data.get("is_pdf_viewer", False)),
            pixels_above=int(data.get("pixels_above", 0)),
            pixels_below=int(data.get("pixels_below", 0)),
            dialog=_dialog_text(data.get("dialog")),
        ),
    )


def _scroll_to_wire(dx: int, dy: int) -> tuple[str, int]:
    if bool(dx) == bool(dy):
        raise Unsupported("BrowserUseDriver scrolls along exactly one axis per command", dispatched=False)
    if dy:
        return ("down", dy) if dy > 0 else ("up", -dy)
    return ("right", dx) if dx > 0 else ("left", -dx)


class BrowserUseDriver(HandlerDriver):
    """Runs contract commands on a Chrome through the browser-use sidecar process."""

    def __init__(self, config: BrowserUseConfig | None = None) -> None:
        self._config = config
        self._transport: SidecarTransport | None = None
        self._driver_generation = 0
        self._versions: dict[str, str] = {}

    def describe(self) -> DriverInfo:
        return DriverInfo(
            name="browser_use",
            surfaces=frozenset({"browser"}),
            versions=MappingProxyType(dict(self._versions)),
            observes=frozenset({"elements", "screenshot", "native_text", "surface"}),
        )

    async def start(self, config: BrowserUseConfig | None = None) -> None:
        config = config or self._config
        if config is None:
            raise DriverError("BrowserUseDriver.start() needs a BrowserUseConfig", dispatched=False)
        if self._transport is not None:
            await self.stop()
        transport = SidecarTransport(sidecar_python=config.sidecar_python, upload_root=config.upload_root)
        try:
            await transport.start(timeout_s=config.timeout_s)
            result = await transport.request(
                "connect", {"cdp_url": config.cdp_url, "timeout_s": config.timeout_s}, timeout_s=config.timeout_s
            )
        except Exception as exc:
            await transport.close()
            raise _translate_error("connect", exc) from exc
        self._config = config
        self._transport = transport
        self._versions = {
            "browser": str(result.get("browser_version", "")),
            "protocol": str(result.get("protocol_version", "")),
            "browser_use": str(result.get("backend_version", "")),
        }

    async def stop(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            await transport.close()

    async def health(self) -> Health:
        if self._transport is None:
            return Health(ok=False, detail="not started")
        try:
            result = await self._transport.request("health", {})
        except Exception as exc:  # noqa: BLE001 - health() never raises
            return Health(ok=False, detail=str(exc))
        return Health(ok=bool(result.get("connected", False)), detail=result.get("error"))

    async def observe(self, command: Observe) -> Observation:
        if command.scoped:
            raise Unsupported("BrowserUseDriver observes the whole page only", dispatched=False)
        viewport_screenshot = command.include_screenshot and not command.full_page
        data = await self._request(
            "observe", {"include_dom": True, "include_screenshot": viewport_screenshot, "cached": False}
        )
        self._driver_generation = int(data.get("driver_generation", self._driver_generation))
        screenshot_b64 = data.get("screenshot_b64")
        # Scripts and captures wait while a dialog is open.
        if data.get("dialog") is None:
            try:
                data = {**data, "title": str(await self._evaluate(_DOCUMENT_TITLE_JS, None))}
            except DriverError:
                pass  # no script context (e.g. the PDF viewer): keep the sidecar's title
            if command.include_screenshot and command.full_page:
                screenshot_b64 = await self._request("screenshot", {"full_page": True, "clip": None})
        return _observation_from_wire(data, screenshot_b64)

    @handles(Click, refs={DriverRef, Locator})
    async def _click(self, action: Click) -> ActionResult:
        data = await self._request(
            "click",
            {
                "ref": self._ref(action.target),
                "button": action.button,
                "click_count": action.count,
                "modifiers": list(action.modifiers),
            },
        )
        return self._act_result(data)

    @handles(TypeText, refs={DriverRef, Locator})
    async def _type_text(self, action: TypeText) -> ActionResult:
        if action.target is None:
            raise Unsupported("BrowserUseDriver types only into a target element", dispatched=False)
        if action.slowly:
            raise Unsupported("BrowserUseDriver cannot type slowly", dispatched=False)
        data = await self._request(
            "type_text",
            {
                "ref": self._ref(action.target),
                "text": action.text,
                "clear": action.clear,
                "press_enter": action.submit,
                "sensitive": action.sensitive,
            },
        )
        return self._act_result(data)

    @handles(PressKey)
    async def _press_key(self, action: PressKey) -> ActionResult:
        return self._act_result(await self._request("press_key", {"keys": action.key}))

    @handles(Scroll, refs={DriverRef})
    async def _scroll(self, action: Scroll) -> ActionResult:
        direction, amount = _scroll_to_wire(action.dx, action.dy)
        ref = None if action.target is None else self._ref(action.target)
        return self._act_result(await self._request("scroll", {"direction": direction, "amount": amount, "ref": ref}))

    @handles(Hover, refs={DriverRef, Locator})
    async def _hover(self, action: Hover) -> ActionResult:
        return self._act_result(await self._request("hover", {"ref": self._ref(action.target)}))

    @handles(Drag, refs={DriverRef, Locator})
    async def _drag(self, action: Drag) -> ActionResult:
        data = await self._request(
            "drag",
            {"source": self._ref(action.source), "target": self._ref(action.destination), "steps": 10, "delay_ms": 0},
        )
        return self._act_result(data)

    @handles(SelectOption, refs={DriverRef, Locator})
    async def _select_option(self, action: SelectOption) -> ActionResult:
        if len(action.values) != 1:
            raise Unsupported("BrowserUseDriver selects exactly one option per command", dispatched=False)
        data = await self._request(
            "select_option", {"ref": self._ref(action.target), "value": action.values[0], "label": None}
        )
        return self._act_result(data)

    @handles(UploadFile, refs={DriverRef, Locator})
    async def _upload_file(self, action: UploadFile) -> ActionResult:
        if action.target is None:
            raise Unsupported("BrowserUseDriver needs the file input as target", dispatched=False)
        existing, path_error = resolve_upload_file_paths(action.paths, upload_root=self._upload_root())
        if path_error:
            return ActionResult(ok=False, detail=path_error)
        data = await self._request("upload_files", {"ref": self._ref(action.target), "paths": existing})
        return self._act_result(data)

    @handles(Drop, refs={DriverRef, Locator})
    async def _drop(self, action: Drop) -> ActionResult:
        existing: list[str] = []
        if action.paths:
            existing, path_error = resolve_upload_file_paths(action.paths, upload_root=self._upload_root())
            if path_error:
                return ActionResult(ok=False, detail=path_error)
        data = await self._request(
            "drop",
            {
                "ref": self._ref(action.target),
                "paths": existing,
                "data": [{"mimeType": mime, "data": value} for mime, value in action.data],
            },
        )
        return self._act_result(data)

    @handles(FillForm, refs={DriverRef, Locator})
    async def _fill_form(self, action: FillForm) -> ActionResult:
        context_changed = False
        refs = [self._ref(field.target) for field in action.fields]  # an unsupported ref fails before any field
        for index, (field, ref) in enumerate(zip(action.fields, refs)):
            if field.kind in (FieldKind.CHECKBOX, FieldKind.RADIO):
                method = "set_checked"
                params = {"ref": ref, "checked": field.value.strip().lower() in _CHECKED_VALUES}
            elif field.kind is FieldKind.COMBOBOX:
                method, params = "select_option", {"ref": ref, "value": field.value, "label": None}
            else:
                method = "type_text"
                params = {
                    "ref": ref,
                    "text": field.value,
                    "clear": True,
                    "press_enter": False,
                    "sensitive": field.sensitive,
                }
            try:
                data = await self._request(method, params)
            except DriverError as exc:
                # Earlier fields already reached the page.
                if index:
                    exc.dispatched = True
                raise
            step = self._act_result(data)
            context_changed = context_changed or step.context_changed
            if not step.ok:
                return ActionResult(
                    ok=False, detail=f"fields[{index}] failed: {step.detail}", context_changed=context_changed
                )
        return ActionResult(ok=True, context_changed=context_changed)

    @handles(HandleDialog)
    async def _handle_dialog(self, action: HandleDialog) -> ActionResult:
        data = await self._request("handle_dialog", {"accept": action.accept, "prompt_text": action.prompt_text})
        return self._act_result(data)

    @handles(Wait)
    async def _wait(self, action: Wait) -> ActionResult:
        if action.until_text is None:
            await asyncio.sleep(action.seconds)
            return ActionResult(ok=True)
        timeout_s = _WAIT_TIMEOUT_S if action.seconds is None else action.seconds
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while not await self._evaluate(_HAS_TEXT_JS, action.until_text):
            if loop.time() >= deadline:
                raise ActionTimeout(f"text {action.until_text!r} did not appear within {timeout_s}s")
            await asyncio.sleep(_WAIT_POLL_S)
        return ActionResult(ok=True)

    @handles(RunScript, refs={DriverRef})
    async def _run_script(self, action: RunScript) -> ActionResult:
        if action.target is None:
            return ActionResult(ok=True, value=await self._evaluate(action.code, action.args))
        selector = await self._request(
            "stamp", {"ref": self._ref(action.target), "attribute": _TARGET_ATTRIBUTE, "value": action.target.ref}
        )
        value = await self._evaluate(
            _SCOPED_SCRIPT.format(code=action.code), {"selector": str(selector), "args": action.args}
        )
        return ActionResult(ok=True, value=value)

    @handles(Navigate)
    async def _navigate(self, action: Navigate) -> ActionResult:
        return self._nav_result(await self._navigate_wire(action.url, new_tab=False))

    @handles(Back)
    async def _back(self, action: Back) -> ActionResult:
        del action
        return self._nav_result(await self._request("go_back", {}))

    @handles(OpenContext)
    async def _open_context(self, action: OpenContext) -> ActionResult:
        before = {str(tab["target_id"]) for tab in await self._request("list_tabs", {})}
        data = await self._navigate_wire(action.url or "about:blank", new_tab=True)
        after = await self._request("list_tabs", {})
        opened = tuple(_context_from_wire(tab) for tab in after if str(tab["target_id"]) not in before)
        return self._nav_result(data, raw_tabs=after, value=opened[0] if opened else None, opened_contexts=opened)

    @handles(SwitchContext)
    async def _switch_context(self, action: SwitchContext) -> ActionResult:
        data = await self._request("switch_tab", {"tab": _context_to_wire(action.context)})
        return await self._tab_result(data)

    @handles(CloseContext)
    async def _close_context(self, action: CloseContext) -> ActionResult:
        data = await self._request("close_tab", {"tab": _context_to_wire(action.context)})
        return await self._tab_result(data, source_context=action.context)

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        if self._transport is None:
            raise ConnectionLost("BrowserUseDriver is not started", dispatched=False)
        try:
            return await self._transport.request(method, params)
        except Exception as exc:
            raise _translate_error(method, exc) from exc

    async def _evaluate(self, source: str, args: Any) -> Any:
        return await self._request(
            "evaluate", {"source": source, "args": args, "await_promise": True, "return_by_value": True}
        )

    async def _navigate_wire(self, url: str, *, new_tab: bool) -> dict[str, Any]:
        return await self._request(
            "navigate", {"url": url, "wait_until": "load", "timeout_ms": None, "new_tab": new_tab}
        )

    def _upload_root(self) -> Path | None:
        return self._config.upload_root if self._config is not None else None

    def _ref(self, ref: DriverRef | Locator) -> dict[str, Any]:
        return _ref_to_wire(ref, self._driver_generation)

    def _act_result(self, data: dict[str, Any], *, context_changed: bool = False, **fields: Any) -> ActionResult:
        self._driver_generation = int(data.get("driver_generation", self._driver_generation))
        detail = data.get("detail") or None
        dialog = data.get("dialog_opened")
        if dialog is not None:
            note = f"{dialog.get('type') or 'dialog'} dialog opened: {dialog.get('message', '')!r}"
            detail = f"{detail}; {note}" if detail else note
        opened = tuple(_context_from_wire(target) for target in data.get("new_targets") or ())
        open_dialog = _dialog_text(data.get("dialog"))
        return ActionResult(
            ok=bool(data["ok"]),
            detail=detail,
            context_changed=(
                context_changed or bool(data.get("document_changed", False)) or dialog is not None or bool(opened)
            ),
            opened_contexts=opened,
            surface=None if open_dialog is None else BrowserContext(url=str(data.get("url", "")), dialog=open_dialog),
            **fields,
        )

    async def _tab_result(self, data: dict[str, Any], **fields: Any) -> ActionResult:
        result = self._act_result(data, context_changed=True, **fields)
        try:
            raw_tabs = await self._request("list_tabs", {})
        except DriverError as exc:
            exc.dispatched = True  # the tab command itself already ran
            raise
        title = next((str(tab.get("title", "")) for tab in raw_tabs if tab.get("active")), "")
        surface = BrowserContext(
            url=str(data.get("url", "")), title=title, **_tab_fields(raw_tabs), dialog=_dialog_text(data.get("dialog"))
        )
        return replace(result, surface=surface)

    def _nav_result(
        self, data: dict[str, Any], *, raw_tabs: Sequence[dict[str, Any]] = (), **fields: Any
    ) -> ActionResult:
        self._driver_generation = int(data.get("driver_generation", self._driver_generation))
        return ActionResult(
            ok=True,
            context_changed=True,  # a navigation always loads a document, even at the same URL
            surface=BrowserContext(
                url=str(data["url"]),
                title=str(data.get("title", "")),
                **_tab_fields(raw_tabs),
                dialog=_dialog_text(data.get("dialog")),
            ),
            **fields,
        )


def create_browser_use_driver(device: ProvisionedDevice, surface_settings: Any) -> BrowserUseDriver:
    """Driver factory (registry signature): the driver connects to ``device.endpoint`` on ``start()``."""
    if surface_settings is None:
        return BrowserUseDriver(BrowserUseConfig(cdp_url=device.endpoint))
    return BrowserUseDriver(
        BrowserUseConfig(
            cdp_url=device.endpoint,
            sidecar_python=surface_settings.sidecar_python,
            upload_root=surface_settings.upload_root,
        )
    )


__all__ = ["BrowserUseConfig", "BrowserUseDriver", "create_browser_use_driver"]
