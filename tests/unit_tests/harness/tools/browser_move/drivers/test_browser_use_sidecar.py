# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser-use sidecar's ``SessionAdapter`` on a fake CDP client: dialogs during actions.

No Chrome and no ``browser_use``: the fake client answers CDP commands from scripted handlers and, like cdp-use,
keeps one handler per event. Sidecar modules are flat (``import cdp``), so they are loaded by path.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

_SIDECAR_DIR = (
    Path(__file__).resolve().parents[6]
    / "openjiuwen"
    / "harness"
    / "tools"
    / "browser_move"
    / "drivers"
    / "browser_use"
    / "sidecar"
)


def _load_sidecar_module(name: str) -> Any:
    """Load a flat sidecar module (``import exceptions`` / ``import cdp``) under its own name."""
    if name in sys.modules and getattr(sys.modules[name], "__file__", "").startswith(str(_SIDECAR_DIR)):
        return sys.modules[name]
    if str(_SIDECAR_DIR) not in sys.path:
        sys.path.insert(0, str(_SIDECAR_DIR))
    spec = importlib.util.spec_from_file_location(name, _SIDECAR_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


session_adapter = _load_sidecar_module("session_adapter")
errors = session_adapter.exceptions

SESSION = "S1"
TARGET = "T1"
POPUP_SESSION = "S2"
BUTTON = {"kind": "driver", "handle": "bnid:7", "driver_generation": 0}
QUICK_S = 2.0


class _Registry:
    """cdp-use's ``EventRegistry``: one handler per event method."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}

    def register(self, method: str, callback: Callable) -> None:
        self._handlers[method] = callback

    def unregister(self, method: str) -> None:
        self._handlers.pop(method, None)


class _Domain:
    def __init__(self, client: FakeCdp, domain: str) -> None:
        self._client = client
        self._domain = domain

    def __getattr__(self, method: str) -> Callable:
        name = f"{self._domain}.{method}"

        async def send(params: dict[str, Any] | None = None, session_id: str | None = None) -> Any:
            params = dict(params or {})
            self._client.calls.append((name, params, session_id))
            reply = self._client.replies.get(name)
            if reply is None:
                return {}
            result = reply(params, session_id)
            return await result if inspect.isawaitable(result) else result

        return send


class FakeCdp:
    """Answers ``send.<Domain>.<method>`` from ``replies`` and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.replies: dict[str, Callable[[dict[str, Any], str | None], Any]] = {}
        self._event_registry = _Registry()
        self.send = _Send(self)

    def emit(self, method: str, params: dict[str, Any], session_id: str | None = SESSION) -> None:
        handler = self._event_registry._handlers.get(method)
        if handler is not None:
            handler(params, session_id)

    def sent(self, name: str) -> list[dict[str, Any]]:
        return [params for called, params, _sid in self.calls if called == name]


class _Send:
    def __init__(self, client: FakeCdp) -> None:
        self._client = client

    def __getattr__(self, domain: str) -> _Domain:
        return _Domain(self._client, domain)


class Page:
    """A page with one button at (10, 20, 100, 40); clicking it can open a JS dialog that blocks the release."""

    def __init__(self, client: FakeCdp, *, dialog: dict[str, str] | None = None) -> None:
        self.client = client
        self.dialog = dialog
        self.dialog_closed: asyncio.Future | None = None
        client.replies["DOM.resolveNode"] = lambda params, sid: {"object": {"objectId": "obj-7"}}
        client.replies["Runtime.callFunctionOn"] = self._call_function
        client.replies["Input.dispatchMouseEvent"] = self._mouse
        client.replies["Page.handleJavaScriptDialog"] = self._handle_dialog

    def _call_function(self, params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        if "getBoundingClientRect" in params["functionDeclaration"]:
            return {"result": {"value": {"x": 10, "y": 20, "width": 100, "height": 40}}}
        return {"result": {"value": None}}

    async def _mouse(self, params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        if params["type"] == "mouseReleased" and self.dialog is not None:
            self.dialog_closed = asyncio.get_running_loop().create_future()
            self.client.emit("Page.javascriptDialogOpening", self.dialog)
            await self.dialog_closed  # Chrome answers the release only after the dialog closes
        return {}

    def _handle_dialog(self, params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        if self.dialog_closed is not None and not self.dialog_closed.done():
            self.dialog_closed.set_result(params["accept"])
        return {}


def _adapter(client: FakeCdp) -> Any:
    adapter = session_adapter.SessionAdapter()
    adapter._session = SimpleNamespace(
        get_current_page_url=AsyncMock(return_value="http://x/index.html"),
        get_tabs=AsyncMock(return_value=[]),
        stop=AsyncMock(),
    )
    adapter._cdp_session = SimpleNamespace(cdp_client=client, session_id=SESSION, target_id=TARGET)
    return adapter


def _quick(awaitable: Any) -> Any:
    """Fails the test when the call blocks, as the click on a dialog used to (30 s live)."""
    return asyncio.wait_for(awaitable, QUICK_S)


# -- dialogs -----------------------------------------------------------------


def test_a_click_that_opens_a_dialog_returns_with_the_dialog():
    async def scenario() -> None:
        client = FakeCdp()
        page = Page(client, dialog={"type": "confirm", "message": "Proceed?"})
        adapter = _adapter(client)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["ok"] is True
        assert reply["dialog_opened"] == {"type": "confirm", "message": "Proceed?"}
        assert not page.dialog_closed.done(), "the click must not wait for the dialog"
        await adapter.close()

    asyncio.run(scenario())


def test_handle_dialog_resolves_the_open_dialog_and_the_waiting_input():
    async def scenario() -> None:
        client = FakeCdp()
        page = Page(client, dialog={"type": "confirm", "message": "Proceed?"})
        adapter = _adapter(client)
        await _quick(adapter.click(BUTTON))
        reply = await _quick(adapter.handle_dialog(accept=True))
        assert reply["ok"] is True
        assert reply["detail"] == "accepted confirm"
        assert client.sent("Page.handleJavaScriptDialog") == [{"accept": True}]
        assert page.dialog_closed.result() is True
        await asyncio.sleep(0)
        assert not adapter._waiting_inputs, "the click's input finished once the dialog closed"

    asyncio.run(scenario())


def test_the_dialog_is_handled_on_the_session_that_reported_it():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        adapter = _adapter(client)
        await adapter._ensure_dialog_listener()
        client.emit("Page.javascriptDialogOpening", {"type": "alert", "message": "Hi"}, session_id=POPUP_SESSION)
        await _quick(adapter.handle_dialog(accept=True))
        assert [sid for name, _params, sid in client.calls if name == "Page.handleJavaScriptDialog"] == [POPUP_SESSION]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda adapter: adapter.evaluate("() => document.title"), "EvaluateError"),
        (lambda adapter: adapter.observe(), "ObserveError"),
        (lambda adapter: adapter.screenshot(), "ObserveError"),
        (lambda adapter: adapter.stamp(BUTTON, attribute="data-x", value="1"), "EvaluateError"),
    ],
    ids=["evaluate", "observe", "screenshot", "stamp"],
)
def test_page_reads_fail_at_once_while_a_dialog_is_open(call, error):
    async def scenario() -> None:
        client = FakeCdp()
        Page(client, dialog={"type": "alert", "message": "Saved"})
        adapter = _adapter(client)
        await _quick(adapter.click(BUTTON))
        sent_before = len(client.calls)
        with pytest.raises(getattr(errors, error), match="alert dialog is open"):
            await _quick(call(adapter))
        assert len(client.calls) == sent_before, "nothing reaches the blocked page"
        await adapter.close()

    asyncio.run(scenario())


def test_an_action_while_a_dialog_is_open_sends_nothing():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client, dialog={"type": "confirm", "message": "Proceed?"})
        adapter = _adapter(client)
        await _quick(adapter.click(BUTTON))
        presses = len(client.sent("Input.dispatchMouseEvent"))
        reply = await _quick(adapter.click(BUTTON))
        assert reply["ok"] is False
        assert "confirm dialog is open" in reply["detail"]
        assert reply["dialog_opened"] is None, "the dialog opened during the earlier action"
        assert len(client.sent("Input.dispatchMouseEvent")) == presses
        await adapter.close()

    asyncio.run(scenario())


def test_an_armed_dialog_is_handled_and_still_reported():
    async def scenario() -> None:
        client = FakeCdp()
        page = Page(client, dialog={"type": "confirm", "message": "Proceed?"})
        adapter = _adapter(client)
        armed = await _quick(adapter.handle_dialog(accept=False))
        assert armed["detail"].startswith("armed")
        reply = await _quick(adapter.click(BUTTON))
        assert reply["ok"] is True
        assert reply["detail"] == "clicked"
        assert reply["dialog_opened"] == {"type": "confirm", "message": "Proceed?"}
        assert page.dialog_closed.result() is False

    asyncio.run(scenario())


def test_every_action_reclaims_the_dialog_event_from_browser_use():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client, dialog={"type": "alert", "message": "Hi"})
        adapter = _adapter(client)
        await adapter._ensure_dialog_listener()
        # browser-use's popups watchdog registers its own auto-accepting handler when a tab is created.
        client._event_registry.register("Page.javascriptDialogOpening", lambda event, sid: None)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["dialog_opened"] == {"type": "alert", "message": "Hi"}
        await adapter.close()

    asyncio.run(scenario())


def test_page_events_are_enabled_once_per_session():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        adapter = _adapter(client)
        await _quick(adapter.click(BUTTON))
        await _quick(adapter.click(BUTTON))
        assert len(client.sent("Page.enable")) == 1

    asyncio.run(scenario())
