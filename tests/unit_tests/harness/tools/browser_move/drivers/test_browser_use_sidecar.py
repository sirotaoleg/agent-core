# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The sidecar's ``SessionAdapter`` on a fake CDP client; flat sidecar modules (``import cdp``) load by path."""

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
    """A page with one button at (10, 20, 100, 40); a dialog or popup it opens blocks the release, as in Chrome."""

    def __init__(
        self,
        client: FakeCdp,
        *,
        dialog: dict[str, str] | None = None,
        popup: dict[str, str] | None = None,
        navigation: dict[str, Any] | None = None,
        commit_after: float | None = None,
        covered_by: str | None = None,
    ) -> None:
        self.client = client
        self.covered_by = covered_by
        self.navigation = navigation
        self.commit_after = commit_after
        self.dialog = dialog
        self.dialog_closed: asyncio.Future | None = None
        self.popup = popup
        self.opener_resumed: asyncio.Future | None = None
        client.replies["DOM.resolveNode"] = lambda params, sid: {"object": {"objectId": "obj-7"}}
        client.replies["Runtime.callFunctionOn"] = self._call_function
        client.replies["Input.dispatchMouseEvent"] = self._mouse
        client.replies["Page.handleJavaScriptDialog"] = self._handle_dialog
        client.replies["Runtime.runIfWaitingForDebugger"] = self._run_if_waiting

    def _call_function(self, params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        if "elementFromPoint" in params["functionDeclaration"]:
            return {"result": {"value": self.covered_by}}
        if "getBoundingClientRect" in params["functionDeclaration"]:
            return {"result": {"value": {"x": 10, "y": 20, "width": 100, "height": 40}}}
        return {"result": {"value": None}}

    async def _mouse(self, params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        if params["type"] == "mouseReleased" and self.dialog is not None:
            self.dialog_closed = asyncio.get_running_loop().create_future()
            self.client.emit("Page.javascriptDialogOpening", self.dialog)
            await self.dialog_closed  # Chrome answers the release only after the dialog closes
        if params["type"] == "mouseReleased" and self.navigation is not None:
            self.client.emit("Page.frameRequestedNavigation", self.navigation)
            if self.commit_after is not None:
                frame = {"id": self.navigation["frameId"], "loaderId": "L2", "url": self.navigation["url"]}
                asyncio.get_running_loop().call_later(
                    self.commit_after, self.client.emit, "Page.frameNavigated", {"frame": frame}
                )
        if params["type"] == "mouseReleased" and self.popup is not None:
            self.opener_resumed = asyncio.get_running_loop().create_future()
            self.client.emit("Target.targetCreated", {"targetInfo": self.popup}, session_id=None)
            await self.opener_resumed
        return {}

    def _run_if_waiting(self, params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        if sid == SESSION and self.opener_resumed is not None and not self.opener_resumed.done():
            self.opener_resumed.set_result(True)
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
        await adapter._ensure_listeners()
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
        await adapter._ensure_listeners()
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


POPUP = {"targetId": "T2", "type": "page", "title": "", "url": "", "openerId": TARGET}


def test_a_click_that_opens_a_popup_resumes_the_page_and_reports_the_new_target():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client, popup=POPUP)
        adapter = _adapter(client)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["ok"] is True
        assert reply["new_targets"] == [{"target_id": "T2", "url": "", "title": "", "active": False}]
        assert [sid for name, _params, sid in client.calls if name == "Runtime.runIfWaitingForDebugger"] == [SESSION]

    asyncio.run(scenario())


def test_targets_that_are_not_pages_are_not_new_contexts():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        adapter = _adapter(client)
        await adapter._ensure_listeners()

        async def body(act: Any) -> tuple[bool, str]:
            client.emit("Target.targetCreated", {"targetInfo": {**POPUP, "type": "service_worker"}}, session_id=None)
            return True, "done"

        reply = await _quick(adapter._act(body))
        assert reply["new_targets"] == []

    asyncio.run(scenario())


def test_a_page_opened_between_actions_is_not_reported_by_the_next_one():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        adapter = _adapter(client)
        await adapter._ensure_listeners()
        client.emit("Target.targetCreated", {"targetInfo": POPUP}, session_id=None)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["new_targets"] == []
        assert client.sent("Runtime.runIfWaitingForDebugger") == []

    asyncio.run(scenario())


def test_new_target_listener_keeps_the_handler_browser_use_registered():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        seen: list[str] = []
        client._event_registry.register("Target.targetCreated", lambda event, sid: seen.append("browser-use"))
        adapter = _adapter(client)
        await adapter._ensure_listeners()
        await adapter._ensure_listeners()
        client.emit("Target.targetCreated", {"targetInfo": POPUP}, session_id=None)
        assert seen == ["browser-use"]

    asyncio.run(scenario())


LINK = {"frameId": TARGET, "reason": "anchorClick", "url": "http://x/second.html", "disposition": "currentTab"}
# The fake URL never changes: only the navigation events tell the adapter that the document changed.
RELOAD = {**LINK, "reason": "formSubmissionGet", "url": "http://x/index.html"}


@pytest.mark.parametrize("navigation", [LINK, RELOAD], ids=["link", "same-url-reload"])
def test_a_navigation_the_action_started_is_waited_for_and_changes_the_document(navigation):
    async def scenario() -> None:
        client = FakeCdp()
        Page(client, navigation=navigation, commit_after=0.05)
        adapter = _adapter(client)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["document_changed"] is True
        assert reply["driver_generation"] == 1

    asyncio.run(scenario())


def test_a_navigation_that_does_not_commit_in_time_is_not_a_document_change(monkeypatch):
    monkeypatch.setattr(session_adapter, "_NAVIGATION_COMMIT_WAIT_S", 0.05)

    async def scenario() -> None:
        client = FakeCdp()
        Page(client, navigation=LINK, commit_after=None)
        adapter = _adapter(client)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["document_changed"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "navigation",
    [{**LINK, "disposition": "newForegroundTab"}, {**LINK, "frameId": "T2"}],
    ids=["new-tab", "other-frame"],
)
def test_navigations_outside_the_main_frame_are_not_waited_for(monkeypatch, navigation):
    monkeypatch.setattr(session_adapter, "_NAVIGATION_COMMIT_WAIT_S", 60.0)

    async def scenario() -> None:
        client = FakeCdp()
        Page(client, navigation=navigation, commit_after=None)
        adapter = _adapter(client)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["document_changed"] is False

    asyncio.run(scenario())


def test_a_child_frame_document_is_not_a_document_change():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        adapter = _adapter(client)
        await adapter._ensure_listeners()

        async def body(act: Any) -> tuple[bool, str]:
            frame = {"id": TARGET, "parentId": "P", "loaderId": "L2", "url": "http://x/frame.html"}
            client.emit("Page.frameNavigated", {"frame": frame})
            return True, "done"

        reply = await _quick(adapter._act(body))
        assert reply["document_changed"] is False

    asyncio.run(scenario())


cdp = session_adapter.cdp
NOT_IN_DOCUMENT = RuntimeError({"code": -32000, "message": "Node with given id does not belong to the document"})
NO_SUCH_NODE = RuntimeError({"code": -32000, "message": "No node with given id found"})


def _resolve_fails_from(client: FakeCdp, call: int, error: Exception) -> None:
    calls = []

    def resolve(params: dict[str, Any], sid: str | None) -> dict[str, Any]:
        calls.append(params)
        if len(calls) >= call:
            raise error
        return {"object": {"objectId": "obj-7"}}

    client.replies["DOM.resolveNode"] = resolve


@pytest.mark.parametrize("error", [NOT_IN_DOCUMENT, NO_SUCH_NODE], ids=["not-in-document", "no-such-node"])
def test_a_node_that_is_not_in_the_document_is_stale(error):
    client = FakeCdp()
    _resolve_fails_from(client, 1, error)
    with pytest.raises(errors.StaleNodeError, match="backend node 7 is not in the current document"):
        asyncio.run(cdp.resolve_object_id_by_backend_node(client, SESSION, 7))


def test_other_resolve_failures_are_not_stale():
    client = FakeCdp()
    _resolve_fails_from(client, 1, RuntimeError({"code": -32000, "message": "Target closed"}))
    with pytest.raises(RuntimeError, match="Target closed"):
        asyncio.run(cdp.resolve_object_id_by_backend_node(client, SESSION, 7))


@pytest.mark.parametrize(
    "act",
    [lambda adapter: adapter.click(BUTTON), lambda adapter: adapter.hover(BUTTON)],
    ids=["click", "hover"],
)
def test_an_action_on_a_node_of_an_old_document_is_stale_and_sends_nothing(act):
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        _resolve_fails_from(client, 1, NOT_IN_DOCUMENT)
        adapter = _adapter(client)
        with pytest.raises(errors.StaleNodeError):
            await _quick(act(adapter))
        assert client.sent("Input.dispatchMouseEvent") == []

    asyncio.run(scenario())


def test_a_node_lost_after_the_input_is_not_stale():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        # type_text: the box (1st resolve) is found and clicked; the node is gone when the field is cleared.
        _resolve_fails_from(client, 2, NOT_IN_DOCUMENT)
        adapter = _adapter(client)
        with pytest.raises(errors.DriverError, match="after input reached the page") as info:
            await _quick(adapter.type_text(BUTTON, "Ada"))
        assert type(info.value) is errors.DriverError
        assert client.sent("Input.dispatchMouseEvent"), "the click reached the page"

    asyncio.run(scenario())


def test_a_cached_node_of_an_old_document_is_stale():
    async def scenario() -> None:
        client = FakeCdp()
        _resolve_fails_from(client, 1, NOT_IN_DOCUMENT)
        adapter = _adapter(client)
        adapter._index_cache = {1: SimpleNamespace(backend_node_id=7)}
        with pytest.raises(errors.StaleNodeError):
            await adapter._node_for_event(BUTTON)

    asyncio.run(scenario())


OVERLAY = 'span#overlay "Overlay banner"'
OTHER = {"kind": "driver", "handle": "bnid:8", "driver_generation": 0}


@pytest.mark.parametrize(
    ("method", "act"),
    [
        ("click", lambda adapter: adapter.click(BUTTON)),
        ("hover", lambda adapter: adapter.hover(BUTTON)),
        ("drag", lambda adapter: adapter.drag(BUTTON, OTHER)),
    ],
    ids=["click", "hover", "drag-source"],
)
def test_input_another_element_would_receive_is_intercepted_before_it_is_sent(method, act):
    async def scenario() -> None:
        client = FakeCdp()
        Page(client, covered_by=OVERLAY)
        adapter = _adapter(client)
        with pytest.raises(errors.ActionIntercepted, match=f"{method} at \\(60, 40\\) would reach") as info:
            await _quick(act(adapter))
        assert info.value.by == OVERLAY
        assert client.sent("Input.dispatchMouseEvent") == []

    asyncio.run(scenario())


def test_the_hit_test_uses_the_point_the_mouse_will_use():
    async def scenario() -> None:
        client = FakeCdp()
        Page(client)
        adapter = _adapter(client)
        reply = await _quick(adapter.click(BUTTON))
        assert reply["ok"] is True
        hit_tests = [p for p in client.sent("Runtime.callFunctionOn") if "elementFromPoint" in p["functionDeclaration"]]
        assert [p["arguments"] for p in hit_tests] == [[{"value": 60.0}, {"value": 40.0}]]
        presses = [p for p in client.sent("Input.dispatchMouseEvent") if p["type"] == "mousePressed"]
        assert [(p["x"], p["y"]) for p in presses] == [(60.0, 40.0)]

    asyncio.run(scenario())


def test_the_wire_error_carries_the_receiver():
    main = _load_sidecar_module("main")
    exc = errors.ActionIntercepted("click would reach it", by=OVERLAY)
    assert main._error_code_for(exc) == "ActionIntercepted"
    assert main._error_data_for(exc) == {"by": OVERLAY}
