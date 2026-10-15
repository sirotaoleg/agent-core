# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The only sidecar module that imports ``browser_use``: wire dicts in, wire dicts out."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import cdp
import exceptions
import keys as key_utils
import mapping


def _strip_upload_path_text(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


def _upload_root() -> Path | None:
    raw = (os.environ.get("BROWSER_UPLOAD_ROOT") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def _normalize_upload_paths(paths: list[str]) -> tuple[list[str], str | None]:
    """Return ``(existing_absolute_paths, error_or_none)``."""
    existing: list[str] = []
    missing: list[str] = []
    unreadable: list[str] = []
    seen: set[str] = set()
    root = _upload_root()

    for raw in paths or []:
        text = _strip_upload_path_text(str(raw))
        if not text:
            continue
        primary = Path(text).expanduser()
        candidates = [primary]
        if not primary.is_absolute() and root is not None:
            candidates.append(root / text)
            candidates.append(root / primary.name)
        resolved_ok: str | None = None
        last_resolved = text
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            last_resolved = str(resolved)
            if not resolved.is_file():
                continue
            try:
                with resolved.open("rb") as handle:
                    handle.read(1)
            except OSError:
                unreadable.append(str(resolved))
                resolved_ok = None
                break
            resolved_ok = str(resolved)
            break
        if resolved_ok is None:
            if last_resolved not in unreadable:
                missing.append(last_resolved)
            continue
        if resolved_ok not in seen:
            seen.add(resolved_ok)
            existing.append(resolved_ok)

    if not existing and not missing and not unreadable:
        return [], "upload_files requires at least one path"
    err_parts: list[str] = []
    if missing:
        listed = ", ".join(repr(p) for p in missing)
        err_parts.append(
            f"upload file(s) not found: {listed} "
            "(create the file, use an absolute path, or place it under "
            "BROWSER_UPLOAD_ROOT and call list_upload_files)"
        )
    if unreadable:
        listed = ", ".join(repr(p) for p in unreadable)
        err_parts.append(f"upload file(s) not readable: {listed}")
    if err_parts:
        return existing, "; ".join(err_parts)
    return existing, None


_DRIVER_REF_HANDLE_PREFIX = "bnid:"
_RESUME_TIMEOUT_S = 1.0
_NAVIGATION_COMMIT_WAIT_S = 1.0
_SLOW_TYPING_DELAY_S = 0.03


def _mint_driver_ref(backend_node_id: int, driver_generation: int) -> dict[str, Any]:
    """Mint this backend's ``DriverRef`` wire dict for a resolved CDP node."""
    return {"handle": f"{_DRIVER_REF_HANDLE_PREFIX}{int(backend_node_id)}", "driver_generation": int(driver_generation)}


def _parse_driver_ref_handle(handle: str) -> int:
    """Recover the backend node id this adapter minted into ``handle``."""
    if not handle.startswith(_DRIVER_REF_HANDLE_PREFIX):
        raise exceptions.ElementNotFound(f"driver ref handle {handle!r} was not minted by this backend")
    try:
        return int(handle[len(_DRIVER_REF_HANDLE_PREFIX) :])
    except ValueError as exc:
        raise exceptions.ElementNotFound(f"driver ref handle {handle!r} does not encode a backend node id") from exc


def browser_use_version() -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version("browser-use")
    except Exception:  # noqa: BLE001
        return "unknown"


class _Act:
    def __init__(self, main_frame_id: str | None) -> None:
        self.dispatched = False
        self.main_frame_id = main_frame_id
        self.navigation_requested = False
        self.navigation_committed = asyncio.Event()
        self.dialog: dict[str, str] | None = None
        self.dialog_blocking = asyncio.Event()
        self.new_targets: list[dict[str, Any]] = []
        self.target_created = asyncio.Event()

    def dispatching(self) -> None:
        self.dispatched = True


_HIT_TEST_JS = (
    "function(x, y) {"
    "  const root = this.getRootNode ? this.getRootNode() : this.ownerDocument;"
    "  const hit = (root.elementFromPoint ? root : this.ownerDocument).elementFromPoint(x, y);"
    "  if (!hit || hit === this || this.contains(hit)) { return null; }"
    "  if (hit.tagName === 'LABEL' && hit.control === this) { return null; }"
    "  const name = (hit.getAttribute('aria-label') || hit.getAttribute('title') || hit.innerText || '')"
    "    .trim().replace(/\\s+/g, ' ').slice(0, 40);"
    "  const classes = typeof hit.className === 'string' ? hit.className.trim().split(/\\s+/).filter(Boolean) : [];"
    "  return hit.tagName.toLowerCase() + (hit.id ? '#' + hit.id : '')"
    "    + classes.slice(0, 2).map(c => '.' + c).join('') + (name ? ' \"' + name + '\"' : '');"
    "}"
)


def _dialog_note(dialog: dict[str, Any]) -> str:
    return f"a {dialog.get('type') or 'dialog'} dialog is open ({dialog.get('message', '')!r}); handle it first"


class SessionAdapter:
    """Owns the browser-use session and translates wire calls into it."""

    def __init__(self) -> None:
        self._session: Any = None
        self._cdp_session: Any = None
        self._driver_generation = 0
        self._index_cache: dict[int, Any] = {}
        self._dialog_armed: dict[str, Any] | None = None
        self._dialog_pending: dict[str, Any] | None = None
        self._dialog_listener_registered = False
        self._dialog_unregister: Any = None
        self._dialog_handler: Any = None
        self._dialog_handle_tasks: set[asyncio.Task] = set()
        self._current_act: _Act | None = None
        self._waiting_inputs: set[asyncio.Task] = set()
        self._listening_client: Any = None
        self._init_script: str | None = None
        self._guarded_sessions: set[str] = set()

    async def connect(self, *, cdp_url: str, timeout_s: float = 30.0, init_script: str | None = None) -> dict[str, Any]:
        from browser_use.browser.profile import BrowserProfile
        from browser_use.browser.session import BrowserSession

        profile = BrowserProfile(headless=None)
        self._session = BrowserSession(cdp_url=cdp_url, is_local=False, browser_profile=profile)
        await self._session.start()
        self._init_script = init_script or None
        await self._ensure_cdp_session()
        await self._ensure_listeners()

        version_info: dict[str, Any] = {}
        try:
            version_info = await self._cdp_session.cdp_client.send.Browser.getVersion(session_id=None) or {}
        except Exception:  # noqa: BLE001
            version_info = {}

        try:
            initial_url = await self._session.get_current_page_url()
        except Exception:  # noqa: BLE001
            initial_url = ""

        return {
            "backend": "browser_use",
            "browser_version": str(version_info.get("product", "") or ""),
            "protocol_version": str(version_info.get("protocolVersion", "") or ""),
            "backend_version": browser_use_version(),
            "initial_url": initial_url,
        }

    async def health(self) -> dict[str, Any]:
        if self._session is None:
            return {"connected": False, "url": "", "tab_count": 0, "latency_ms": 0.0, "error": "not connected"}
        started = time.monotonic()
        try:
            url = await self._session.get_current_page_url()
            tabs = await self._session.get_tabs()
            latency_ms = (time.monotonic() - started) * 1000.0
            return {"connected": True, "url": url, "tab_count": len(tabs), "latency_ms": latency_ms, "error": None}
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.monotonic() - started) * 1000.0
            return {"connected": False, "url": "", "tab_count": 0, "latency_ms": latency_ms, "error": str(exc)}

    async def close(self) -> None:
        """Detach only. MUST NOT KILL CHROME -- ``stop()``, never ``kill()``."""
        for task in list(self._waiting_inputs):
            task.cancel()
        if self._session is not None:
            await self._session.stop()
        self._session = None
        self._cdp_session = None
        self._index_cache = {}
        self._guarded_sessions = set()

    async def observe(
        self, *, include_dom: bool = True, include_screenshot: bool = False, cached: bool = False
    ) -> dict[str, Any]:
        if self._dialog_pending is not None:
            return await self._dialog_observation()
        await self._ensure_listeners()
        summary = await self._session.get_browser_state_summary(include_screenshot=include_screenshot, cached=cached)
        self._driver_generation += 1
        dom_state = getattr(summary, "dom_state", None)
        self._index_cache = dict(getattr(dom_state, "selector_map", None) or {})
        return mapping.map_browser_state_summary(
            summary,
            driver_generation=self._driver_generation,
            captured_at=time.time(),
            include_screenshot=include_screenshot,
        )

    async def screenshot(self, *, full_page: bool = False, clip: dict[str, Any] | None = None) -> str:
        self._refuse_while_dialog_open("screenshot", exceptions.ObserveError)
        data = await self._session.take_screenshot(full_page=full_page, clip=clip)
        return base64.b64encode(data).decode("ascii")

    async def list_tabs(self) -> list[dict[str, Any]]:
        tabs = await self._session.get_tabs()
        try:
            current_url = await self._session.get_current_page_url()
        except Exception:  # noqa: BLE001
            current_url = ""
        return [mapping.map_tab(tab, current_url) for tab in tabs]

    async def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
        box = await self._element_box(backend_node_id)
        tag, attributes, visible = await self._element_facts(backend_node_id)
        return {
            "backend_node_id": backend_node_id,
            "driver_ref": _mint_driver_ref(backend_node_id, self._driver_generation),
            "frame_id": ref.get("frame_id"),
            "box": box,
            "visible": visible,
            "tag": tag,
            "attributes": attributes,
        }

    async def stamp(self, ref: dict[str, Any], *, attribute: str, value: str) -> str:
        self._refuse_while_dialog_open("stamp", exceptions.EvaluateError)
        backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
        cdp_session = await self._ensure_cdp_session()
        await cdp.call_function_on_backend_node(
            cdp_session.cdp_client,
            cdp_session.session_id,
            backend_node_id,
            "function(attr, val){ this.setAttribute(attr, val); }",
            arguments=[{"value": attribute}, {"value": value}],
            return_by_value=True,
        )
        return f'[{attribute}="{value}"]'

    async def navigate(
        self,
        *,
        url: str,
        wait_until: str = "load",
        timeout_ms: int | None = None,
        new_tab: bool = False,
    ) -> dict[str, Any]:
        from browser_use.browser.events import NavigateToUrlEvent

        before_url = await self._safe_current_url()
        event = self._session.event_bus.dispatch(
            NavigateToUrlEvent(url=url, wait_until=wait_until, timeout_ms=timeout_ms, new_tab=new_tab)
        )
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        return await self._nav_result(before_url)

    async def go_back(self) -> dict[str, Any]:
        from browser_use.browser.events import GoBackEvent

        before_url = await self._safe_current_url()
        event = self._session.event_bus.dispatch(GoBackEvent())
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        return await self._nav_result(before_url)

    async def go_forward(self) -> dict[str, Any]:
        from browser_use.browser.events import GoForwardEvent

        before_url = await self._safe_current_url()
        event = self._session.event_bus.dispatch(GoForwardEvent())
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        return await self._nav_result(before_url)

    async def reload(self) -> dict[str, Any]:
        from browser_use.browser.events import RefreshEvent

        before_url = await self._safe_current_url()
        event = self._session.event_bus.dispatch(RefreshEvent())
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        return await self._nav_result(before_url)

    async def click(
        self,
        ref: dict[str, Any],
        *,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
            box = await self._element_box(backend_node_id)
            if box is None:
                raise exceptions.ElementNotFound(f"element (backend_node_id={backend_node_id}) has no bounding box")
            cx, cy = box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0
            await self._ensure_not_intercepted("click", backend_node_id, cx, cy)
            cdp_session = await self._ensure_cdp_session()
            act.dispatching()
            for _ in range(max(1, click_count)):
                await cdp.dispatch_mouse_click(
                    cdp_session.cdp_client, cdp_session.session_id, x=cx, y=cy, button=button, click_count=click_count
                )
            return True, "clicked"

        return await self._act(body)

    async def type_text(
        self,
        ref: dict[str, Any],
        text: str,
        *,
        clear: bool = True,
        press_enter: bool = False,
        sensitive: bool = False,
        slowly: bool = False,
    ) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
            cdp_session = await self._ensure_cdp_session()
            box = await self._element_box(backend_node_id)
            act.dispatching()
            if box is not None:
                cx, cy = box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0
                await cdp.dispatch_mouse_click(cdp_session.cdp_client, cdp_session.session_id, x=cx, y=cy)
            if clear:
                await cdp.call_function_on_backend_node(
                    cdp_session.cdp_client,
                    cdp_session.session_id,
                    backend_node_id,
                    "function(){ if ('value' in this) { this.value = ''; } else { this.textContent = ''; } "
                    "this.dispatchEvent(new Event('input', {bubbles: true})); }",
                )
            if slowly:
                for character in text:
                    await cdp.insert_text(cdp_session.cdp_client, cdp_session.session_id, character)
                    await asyncio.sleep(_SLOW_TYPING_DELAY_S)
            else:
                await cdp.insert_text(cdp_session.cdp_client, cdp_session.session_id, text)
            if press_enter:
                await cdp.dispatch_key_press(cdp_session.cdp_client, cdp_session.session_id, "Enter")
            return True, "typed" if not sensitive else "typed (sensitive)"

        return await self._act(body)

    async def press_key(self, keys: str) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            from browser_use.browser.events import SendKeysEvent

            act.dispatching()
            event = self._session.event_bus.dispatch(SendKeysEvent(keys=key_utils.normalize_key_combo(keys)))
            await event
            await event.event_result(raise_if_any=True, raise_if_none=False)
            return True, "keys sent"

        return await self._act(body)

    async def select_option(
        self, ref: dict[str, Any], *, value: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            from browser_use.browser.events import SelectDropdownOptionEvent

            node = await self._node_for_event(ref)
            text = label if label is not None else (value or "")
            act.dispatching()
            event = self._session.event_bus.dispatch(SelectDropdownOptionEvent(node=node, text=text))
            await event
            await event.event_result(raise_if_any=True, raise_if_none=False)
            return True, "option selected"

        return await self._act(body)

    async def set_checked(self, ref: dict[str, Any], checked: bool) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
            cdp_session = await self._ensure_cdp_session()
            await cdp.call_function_on_backend_node(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                "function(want){ if (this.checked !== want) { this.checked = want; "
                "this.dispatchEvent(new Event('click', {bubbles: true})); "
                "this.dispatchEvent(new Event('change', {bubbles: true})); } }",
                arguments=[{"value": bool(checked)}],
            )
            return True, "checked state set"

        return await self._act(body)

    async def scroll(self, *, direction: str, amount: float, ref: dict[str, Any] | None = None) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            from browser_use.browser.events import ScrollEvent

            node = await self._node_for_event(ref) if ref is not None else None
            act.dispatching()
            event = self._session.event_bus.dispatch(ScrollEvent(node=node, direction=direction, amount=amount))
            await event
            await event.event_result(raise_if_any=True, raise_if_none=False)
            return True, "scrolled"

        return await self._act(body)

    async def upload_files(self, ref: dict[str, Any], paths: list[str]) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            normalized, path_error = _normalize_upload_paths(paths or [])
            if path_error:
                return False, path_error
            backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
            cdp_session = await self._ensure_cdp_session()
            facts = await cdp.call_function_on_backend_node(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                "function(){"
                "  const isFile = !!(this && this.tagName === 'INPUT' &&"
                "    String(this.type || '').toLowerCase() === 'file');"
                "  return {isFile: isFile, multiple: !!(isFile && this.multiple),"
                "    files: (isFile && this.files) ? this.files.length : 0};"
                "}",
            )
            if not isinstance(facts, dict) or not facts.get("isFile"):
                return False, "upload target is not an <input type=file>"
            if len(normalized) > 1 and not facts.get("multiple"):
                return False, (
                    f"upload target does not accept multiple files "
                    f"(got {len(normalized)} paths); pass a single path or a multiple= file input"
                )
            act.dispatching()
            await cdp.set_file_input_files(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                normalized,
            )
            attached = await cdp.call_function_on_backend_node(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                "function(){ return (this.files && this.files.length) || 0; }",
            )
            count = int(attached or 0)
            if count < 1:
                return False, (
                    "file input has no files after attach "
                    "(DOM.setFileInputFiles did not stick; check path is readable by Chrome)"
                )
            if count != len(normalized):
                return False, f"file input has {count} file(s) after attach, expected {len(normalized)}"
            return True, f"uploaded {count} file(s)"

        return await self._act(body)

    async def drag(
        self, source: dict[str, Any], target: dict[str, Any], *, steps: int = 10, delay_ms: int = 0
    ) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            source_id = await self._resolve_ref_to_backend_node_id(source)
            target_id = await self._resolve_ref_to_backend_node_id(target)
            source_box = await self._element_box(source_id)
            target_box = await self._element_box(target_id)
            if source_box is None or target_box is None:
                raise exceptions.ElementNotFound("drag source or target has no bounding box")
            sx, sy = source_box["x"] + source_box["width"] / 2.0, source_box["y"] + source_box["height"] / 2.0
            tx, ty = target_box["x"] + target_box["width"] / 2.0, target_box["y"] + target_box["height"] / 2.0
            await self._ensure_not_intercepted("drag", source_id, sx, sy)
            source_is_html5 = await self._element_is_html5_draggable(source_id)
            cdp_session = await self._ensure_cdp_session()
            act.dispatching()
            result = await cdp.perform_drag(
                cdp_session.cdp_client,
                cdp_session.session_id,
                sx=sx,
                sy=sy,
                tx=tx,
                ty=ty,
                steps=steps,
                delay_ms=delay_ms,
                source_is_html5=source_is_html5,
                source_backend_node_id=source_id,
                target_backend_node_id=target_id,
            )
            return bool(result.get("ok")), str(result.get("detail") or "")

        return await self._act(body)

    async def hover(self, ref: dict[str, Any]) -> dict[str, Any]:
        async def body(act: _Act) -> tuple[bool, str]:
            backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
            box = await self._element_box(backend_node_id)
            if box is None:
                raise exceptions.ElementNotFound(f"element (backend_node_id={backend_node_id}) has no bounding box")
            cx, cy = box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0
            await self._ensure_not_intercepted("hover", backend_node_id, cx, cy)
            cdp_session = await self._ensure_cdp_session()
            act.dispatching()
            await cdp.dispatch_mouse_move(
                cdp_session.cdp_client,
                cdp_session.session_id,
                x=max(0.0, cx - 4.0),
                y=max(0.0, cy - 4.0),
            )
            await cdp.dispatch_mouse_move(cdp_session.cdp_client, cdp_session.session_id, x=cx, y=cy)
            return True, "hovered"

        return await self._act(body)

    async def handle_dialog(self, *, accept: bool, prompt_text: str | None = None) -> dict[str, Any]:
        """Handle the open dialog, or arm the handler for the next one."""

        async def body(act: _Act) -> tuple[bool, str]:
            if self._dialog_pending is not None:
                pending = self._dialog_pending
                self._dialog_pending = None
                cdp_session = await self._ensure_cdp_session()
                await cdp.handle_javascript_dialog(
                    cdp_session.cdp_client,
                    pending.get("session_id") or cdp_session.session_id,
                    accept=bool(accept),
                    prompt_text=prompt_text,
                )
                dialog_type = str(pending.get("type") or "dialog")
                action = "accepted" if accept else "dismissed"
                return True, f"{action} {dialog_type}"
            self._dialog_armed = {"accept": bool(accept), "prompt_text": prompt_text}
            return True, "armed for next dialog; call before the action that opens it"

        return await self._act(body, while_dialog=True)

    async def drop(
        self,
        ref: dict[str, Any],
        *,
        paths: list[str] | None = None,
        data: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Drop external files and/or MIME data onto an element (not element→element drag)."""

        async def body(act: _Act) -> tuple[bool, str]:
            path_list = list(paths or [])
            data_list = list(data or [])
            if not path_list and not data_list:
                return False, "drop requires at least one of paths or data"
            normalized: list[str] = []
            if path_list:
                normalized, path_error = _normalize_upload_paths(path_list)
                if path_error:
                    return False, path_error
            backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
            box = await self._element_box(backend_node_id)
            if box is None:
                raise exceptions.ElementNotFound(f"drop target (backend_node_id={backend_node_id}) has no bounding box")
            cx, cy = box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0
            cdp_session = await self._ensure_cdp_session()
            act.dispatching()
            try:
                result = await cdp.perform_external_drop(
                    cdp_session.cdp_client,
                    cdp_session.session_id,
                    x=cx,
                    y=cy,
                    paths=normalized,
                    items=data_list,
                )
            except exceptions.DriverUnsupported:
                raise
            except exceptions.DriverError as exc:
                return False, str(exc)
            detail = str(result.get("detail") or "dropped")
            if normalized:
                attached = await cdp.call_function_on_backend_node(
                    cdp_session.cdp_client,
                    cdp_session.session_id,
                    backend_node_id,
                    "function(){"
                    "  if (this && this.tagName === 'INPUT' &&"
                    "      String(this.type || '').toLowerCase() === 'file') {"
                    "    return (this.files && this.files.length) || 0;"
                    "  }"
                    "  return null;"
                    "}",
                )
                if isinstance(attached, int) and attached > 0:
                    detail = f"{detail}; file input now has {attached} file(s)"
            return bool(result.get("ok")), detail

        return await self._act(body)

    async def switch_tab(self, tab: dict[str, Any]) -> dict[str, Any]:
        before_url = await self._safe_current_url()
        from browser_use.browser.events import SwitchTabEvent

        event = self._session.event_bus.dispatch(SwitchTabEvent(target_id=tab["target_id"]))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        self._cdp_session = None
        self._dialog_listener_registered = False
        self._dialog_unregister = None
        return await self._act_result(before_url, ok=True, detail="switched tab")

    async def close_tab(self, tab: dict[str, Any]) -> dict[str, Any]:
        before_url = await self._safe_current_url()
        from browser_use.browser.events import CloseTabEvent

        event = self._session.event_bus.dispatch(CloseTabEvent(target_id=tab["target_id"]))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        return await self._act_result(before_url, ok=True, detail="closed tab")

    async def evaluate(
        self,
        source: str,
        *,
        args: Any = None,
        await_promise: bool = True,
        return_by_value: bool = True,
    ) -> Any:
        self._refuse_while_dialog_open("evaluate", exceptions.EvaluateError)
        cdp_session = await self._ensure_cdp_session()
        return await cdp.evaluate_expression(
            cdp_session.cdp_client,
            cdp_session.session_id,
            source,
            args,
            await_promise=await_promise,
            return_by_value=return_by_value,
        )

    async def wait_load_state(self, *, state: str = "load", timeout_ms: int) -> dict[str, Any]:
        before_url = await self._safe_current_url()
        await asyncio.sleep(min(timeout_ms / 1000.0, 5.0))
        return await self._act_result(before_url, ok=True, detail=f"waited for {state}")

    async def _ensure_cdp_session(self) -> Any:
        if self._cdp_session is None:
            self._cdp_session = await self._session.get_or_create_cdp_session()
            await self._guard_document(self._cdp_session)
        return self._cdp_session

    async def _guard_document(self, cdp_session: Any) -> None:
        if not self._init_script or cdp_session.session_id in self._guarded_sessions:
            return
        await cdp.add_script_on_new_document(cdp_session.cdp_client, cdp_session.session_id, self._init_script)
        # The document open at attach time loaded before the script was registered, so it gets it now.
        await cdp.evaluate_expression(
            cdp_session.cdp_client, cdp_session.session_id, self._init_script, await_promise=False
        )
        self._guarded_sessions.add(cdp_session.session_id)

    async def _ensure_listeners(self) -> None:
        cdp_session = await self._ensure_cdp_session()
        if not self._dialog_listener_registered:
            await cdp.enable_page_domain(cdp_session.cdp_client, cdp_session.session_id)
            self._dialog_listener_registered = True
        if self._dialog_handler is None:
            self._dialog_handler = self._on_dialog
        self._dialog_unregister = cdp.register_javascript_dialog_opening(cdp_session.cdp_client, self._dialog_handler)
        if self._listening_client is not cdp_session.cdp_client:
            client = cdp_session.cdp_client
            cdp.add_event_listener(client, "Target.targetCreated", self._on_target_created)
            cdp.add_event_listener(client, "Page.frameRequestedNavigation", self._on_navigation_requested)
            cdp.add_event_listener(client, "Page.frameStartedNavigating", self._on_navigation_requested)
            cdp.add_event_listener(client, "Page.frameNavigated", self._on_frame_navigated)
            self._listening_client = client

    def _on_navigation_requested(self, event: Any, session_id: Any = None) -> None:
        act = self._current_act
        event = event or {}
        if act is None or event.get("frameId") != act.main_frame_id:
            return
        if event.get("disposition", "currentTab") == "currentTab":
            act.navigation_requested = True

    def _on_frame_navigated(self, event: Any, session_id: Any = None) -> None:
        act = self._current_act
        frame = (event or {}).get("frame") or {}
        if act is not None and not frame.get("parentId") and frame.get("id") == act.main_frame_id:
            act.navigation_committed.set()

    def _on_target_created(self, event: Any, session_id: Any = None) -> None:
        info = (event or {}).get("targetInfo") or {}
        act = self._current_act
        if act is None or info.get("type") != "page":
            return
        target_id = str(info.get("targetId") or "")
        if all(target["target_id"] != target_id for target in act.new_targets):
            url, title = str(info.get("url") or ""), str(info.get("title") or "")
            act.new_targets.append({"target_id": target_id, "url": url, "title": title, "active": False})
        act.target_created.set()

    def _on_dialog(self, event: Any, session_id: Any = None) -> None:
        payload = event if isinstance(event, dict) else getattr(event, "__dict__", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        info = {"type": str(payload.get("type") or "alert"), "message": str(payload.get("message") or "")}
        act = self._current_act
        if act is not None:
            act.dialog = info
        armed = self._dialog_armed
        if armed is not None:
            self._dialog_armed = None
            self._dialog_pending = None
            task = asyncio.create_task(
                self._apply_armed_dialog(
                    accept=bool(armed.get("accept")),
                    prompt_text=armed.get("prompt_text"),
                    session_id=session_id,
                )
            )
            self._dialog_handle_tasks.add(task)
            task.add_done_callback(self._dialog_handle_tasks.discard)
        else:
            self._dialog_pending = {**info, "session_id": session_id}
            if act is not None:
                act.dialog_blocking.set()

    async def _apply_armed_dialog(self, *, accept: bool, prompt_text: str | None, session_id: Any = None) -> None:
        try:
            cdp_session = await self._ensure_cdp_session()
            await cdp.handle_javascript_dialog(
                cdp_session.cdp_client,
                session_id or cdp_session.session_id,
                accept=accept,
                prompt_text=prompt_text,
            )
        except Exception:  # noqa: BLE001
            pass

    def _open_dialog(self) -> dict[str, str] | None:
        pending = self._dialog_pending
        return None if pending is None else {"type": pending["type"], "message": pending["message"]}

    async def _dialog_observation(self) -> dict[str, Any]:
        tabs = await self.list_tabs()
        return {
            "url": await self._safe_current_url(),
            "title": next((tab["title"] for tab in tabs if tab["active"]), ""),
            "tabs": tabs,
            "elements": [],
            "dialog": self._open_dialog(),
            "driver_generation": self._driver_generation,
        }

    def _refuse_while_dialog_open(self, method: str, error: type[exceptions.DriverError]) -> None:
        if self._dialog_pending is not None:
            raise error(f"{method}: {_dialog_note(self._dialog_pending)}")

    async def _act(self, body: Any, *, while_dialog: bool = False) -> dict[str, Any]:
        before_url = await self._safe_current_url()
        if self._dialog_pending is not None and not while_dialog:
            return await self._act_result(before_url, ok=False, detail=_dialog_note(self._dialog_pending))
        await self._ensure_listeners()
        act = _Act(getattr(self._cdp_session, "target_id", None))
        self._current_act = act
        task = asyncio.ensure_future(body(act))
        dialog_waiter = asyncio.ensure_future(act.dialog_blocking.wait())
        target_waiter = asyncio.ensure_future(act.target_created.wait())
        try:
            waiters = {dialog_waiter, target_waiter}
            while not task.done() and not dialog_waiter.done():
                done, _pending = await asyncio.wait({task, *waiters}, return_when=asyncio.FIRST_COMPLETED)
                if target_waiter in done and target_waiter in waiters:
                    waiters.discard(target_waiter)
                    await self._resume_current_page()
            if task.done():
                ok, detail = self._body_result(task, act)
                await self._await_navigation(act)
            else:
                self._waiting_inputs.add(task)
                task.add_done_callback(self._forget_waiting_input)
                ok, detail = True, "the input waits until the dialog is handled"
        finally:
            dialog_waiter.cancel()
            target_waiter.cancel()
            if not task.done() and task not in self._waiting_inputs:
                task.cancel()
            self._current_act = None
        return await self._act_result(before_url, ok=ok, detail=detail, act=act)

    @staticmethod
    def _body_result(task: asyncio.Task, act: _Act) -> tuple[bool, str]:
        try:
            return task.result()
        except (exceptions.StaleNodeError, exceptions.StaleIndexError, exceptions.ElementNotFound) as exc:
            if not act.dispatched:
                raise
            raise exceptions.DriverError(f"{exc} (after input reached the page)") from exc

    @staticmethod
    async def _await_navigation(act: _Act) -> None:
        if act.navigation_requested and not act.navigation_committed.is_set():
            try:
                await asyncio.wait_for(act.navigation_committed.wait(), _NAVIGATION_COMMIT_WAIT_S)
            except asyncio.TimeoutError:
                pass

    async def _resume_current_page(self) -> None:
        cdp_session = await self._ensure_cdp_session()
        try:
            await asyncio.wait_for(
                cdp.run_if_waiting_for_debugger(cdp_session.cdp_client, cdp_session.session_id), _RESUME_TIMEOUT_S
            )
        except Exception:  # noqa: BLE001
            pass

    def _forget_waiting_input(self, task: asyncio.Task) -> None:
        self._waiting_inputs.discard(task)
        if not task.cancelled():
            task.exception()

    async def _safe_current_url(self) -> str:
        try:
            return await self._session.get_current_page_url()
        except Exception:  # noqa: BLE001
            return ""

    async def _nav_result(self, before_url: str) -> dict[str, Any]:
        url = await self._safe_current_url()
        try:
            title = await self._session.get_current_page_title()
        except Exception:  # noqa: BLE001
            title = ""
        self._driver_generation += 1
        return {
            "url": url,
            "title": title,
            "changed_document": url != before_url,
            "driver_generation": self._driver_generation,
            "dialog": self._open_dialog(),
        }

    async def _act_result(self, before_url: str, *, ok: bool, detail: str, act: _Act | None = None) -> dict[str, Any]:
        url = await self._safe_current_url()
        changed = url != before_url or (act is not None and act.navigation_committed.is_set())
        if changed:
            self._driver_generation += 1
        return {
            "ok": ok,
            "detail": detail,
            "url": url,
            "document_changed": changed,
            "driver_generation": self._driver_generation,
            "dialog_opened": None if act is None else act.dialog,
            "dialog": self._open_dialog(),
            "new_targets": [] if act is None else list(act.new_targets),
        }

    def _node_from_index(self, index: int, driver_generation: int) -> Any:
        if driver_generation != self._driver_generation:
            raise exceptions.StaleIndexError(
                f"index {index} was observed at driver_generation {driver_generation}, "
                f"current is {self._driver_generation}"
            )
        node = self._index_cache.get(index)
        if node is None:
            raise exceptions.StaleIndexError(f"index {index} is not present in the last observation")
        return node

    async def _resolve_ref_to_backend_node_id(self, ref: dict[str, Any]) -> int:
        """Resolve any wire ElementRef dict to a CDP backendNodeId."""
        kind = ref.get("kind")
        if kind == "index":
            node = self._node_from_index(int(ref["index"]), int(ref["driver_generation"]))
            backend_node_id = getattr(node, "backend_node_id", None)
            if backend_node_id is None:
                raise exceptions.StaleIndexError(f"index {ref['index']} has no backend_node_id")
            return int(backend_node_id)
        if kind == "node":
            return int(ref["backend_node_id"])
        if kind == "driver":
            return _parse_driver_ref_handle(str(ref["handle"]))
        if kind == "selector":
            cdp_session = await self._ensure_cdp_session()
            node_id = await cdp.query_selector_node_id(cdp_session.cdp_client, cdp_session.session_id, ref["css"])
            if node_id is None:
                raise exceptions.ElementNotFound(f"no element matches selector {ref['css']!r}")
            return await cdp.describe_node_backend_id(cdp_session.cdp_client, cdp_session.session_id, node_id)
        if kind == "text":
            return await self._resolve_text_ref(ref)
        raise exceptions.ElementNotFound(f"unsupported element ref kind {kind!r}")

    async def _resolve_text_ref(self, ref: dict[str, Any]) -> int:
        cdp_session = await self._ensure_cdp_session()
        text = str(ref.get("text", ""))
        role = ref.get("role")
        find_fn = (
            "(args) => { "
            "const text = args.text; const role = args.role; "
            "const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT); "
            "let node = walker.currentNode; "
            "while (node) { "
            "  if (node.textContent && node.textContent.includes(text) && "
            "      (!role || node.getAttribute('role') === role)) { return node; } "
            "  node = walker.nextNode(); "
            "} "
            "return null; }"
        )
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={
                "expression": f"({find_fn})({json.dumps({'text': text, 'role': role})})",
                "awaitPromise": True,
                "returnByValue": False,
            },
            session_id=cdp_session.session_id,
        )
        object_id = (result.get("result") or {}).get("objectId")
        if not object_id:
            raise exceptions.ElementNotFound(f"no element matches text {text!r}")
        node_id = await cdp.request_node_id_from_object(cdp_session.cdp_client, cdp_session.session_id, object_id)
        return await cdp.describe_node_backend_id(cdp_session.cdp_client, cdp_session.session_id, node_id)

    async def _node_for_event(self, ref: dict[str, Any] | None) -> Any:
        """A real DOM node for browser-use's high-level events, fresh when possible."""
        if ref is None:
            return None
        if ref.get("kind") == "index":
            return self._node_from_index(int(ref["index"]), int(ref["driver_generation"]))
        backend_node_id = await self._resolve_ref_to_backend_node_id(ref)
        cdp_session = await self._ensure_cdp_session()
        await cdp.resolve_object_id_by_backend_node(cdp_session.cdp_client, cdp_session.session_id, backend_node_id)
        for node in self._index_cache.values():
            if getattr(node, "backend_node_id", None) == backend_node_id:
                return node
        raise exceptions.ElementNotFound(
            f"backend_node_id={backend_node_id} has no cached selector-map node for this action; "
            "re-observe the page first"
        )

    async def _element_box(self, backend_node_id: int) -> dict[str, float] | None:
        cdp_session = await self._ensure_cdp_session()
        try:
            rect = await cdp.call_function_on_backend_node(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                "function(){ const r = this.getBoundingClientRect(); "
                "return {x: r.x, y: r.y, width: r.width, height: r.height}; }",
            )
        except exceptions.StaleNodeError:
            raise
        except exceptions.DriverError:
            return None
        if not isinstance(rect, dict):
            return None
        return {k: float(rect.get(k, 0.0) or 0.0) for k in ("x", "y", "width", "height")}

    async def _ensure_not_intercepted(self, method: str, backend_node_id: int, x: float, y: float) -> None:
        cdp_session = await self._ensure_cdp_session()
        by = await cdp.call_function_on_backend_node(
            cdp_session.cdp_client,
            cdp_session.session_id,
            backend_node_id,
            _HIT_TEST_JS,
            arguments=[{"value": x}, {"value": y}],
        )
        if by:
            raise exceptions.ActionIntercepted(
                f"{method} at ({x:.0f}, {y:.0f}) would reach {by}, not the target; nothing was sent", by=str(by)
            )

    async def _element_is_html5_draggable(self, backend_node_id: int) -> bool:
        """True when the node opts into HTML5 DnD (mouse-only drag would be a no-op)."""
        cdp_session = await self._ensure_cdp_session()
        try:
            result = await cdp.call_function_on_backend_node(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                "function(){"
                "  if (this.getAttribute && this.getAttribute('draggable') === 'true') return true;"
                "  if (this.draggable === true) return true;"
                "  return false;"
                "}",
            )
        except exceptions.DriverError:
            return False
        return bool(result)

    async def _element_facts(self, backend_node_id: int) -> tuple[str, dict[str, str], bool]:
        cdp_session = await self._ensure_cdp_session()
        try:
            facts = await cdp.call_function_on_backend_node(
                cdp_session.cdp_client,
                cdp_session.session_id,
                backend_node_id,
                "function(){ const attrs = {}; for (const a of this.attributes || []) { attrs[a.name] = a.value; } "
                "const r = this.getBoundingClientRect(); "
                "return {tag: this.tagName ? this.tagName.toLowerCase() : '', attributes: attrs, "
                "visible: !!(r.width > 0 && r.height > 0)}; }",
            )
        except exceptions.DriverError:
            return "", {}, False
        if not isinstance(facts, dict):
            return "", {}, False
        return (
            str(facts.get("tag", "")),
            dict(facts.get("attributes", {}) or {}),
            bool(facts.get("visible", False)),
        )


__all__ = ["SessionAdapter", "browser_use_version"]
