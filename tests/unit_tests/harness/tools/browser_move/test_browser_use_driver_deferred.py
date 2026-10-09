#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser-use driver and sidecar CDP cases for browser_hover / handle_dialog / drop."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.tools.browser_move.contract import DriverRef, HandleDialog, Hover
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import BrowserUseDriver
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Drop

_SIDECAR_DIR = (
    Path(__file__).resolve().parents[5]
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
    path = _SIDECAR_DIR / f"{name}.py"
    if str(_SIDECAR_DIR) not in sys.path:
        sys.path.insert(0, str(_SIDECAR_DIR))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_cdp = _load_sidecar_module("cdp")
_exceptions = _load_sidecar_module("exceptions")


def _run(coro):
    return asyncio.run(coro)


def _make_driver_with_transport(transport: AsyncMock) -> BrowserUseDriver:
    driver = BrowserUseDriver()
    driver._transport = transport
    driver._driver_generation = 3
    return driver


class _FakeInputSend:
    def __init__(self, client: "_FakeCdpClient") -> None:
        self._client = client

    async def dispatchMouseEvent(self, params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        del session_id
        self._client.calls.append(("Input.dispatchMouseEvent", dict(params)))
        return {}

    async def dispatchDragEvent(self, params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        del session_id
        self._client.calls.append(("Input.dispatchDragEvent", dict(params)))
        return {}


class _FakePageSend:
    def __init__(self, client: "_FakeCdpClient") -> None:
        self._client = client

    async def enable(self, params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        del params, session_id
        self._client.calls.append(("Page.enable", {}))
        return {}

    async def handleJavaScriptDialog(
        self, params: dict[str, Any], session_id: str | None = None
    ) -> dict[str, Any]:
        del session_id
        self._client.calls.append(("Page.handleJavaScriptDialog", dict(params)))
        return {}


class _FakeCdpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.send = SimpleNamespace(Input=_FakeInputSend(self), Page=_FakePageSend(self))
        self._event_registry = SimpleNamespace(register=MagicMock(), unregister=MagicMock())


def test_cdp_hover_move_dispatches_mouse_moved() -> None:
    client = _FakeCdpClient()

    async def _go() -> None:
        await _cdp.dispatch_mouse_move(client, "s1", x=10.0, y=20.0)

    _run(_go())
    assert any(
        name == "Input.dispatchMouseEvent" and params.get("type") == "mouseMoved"
        for name, params in client.calls
    )


def test_cdp_external_drop_rejects_unsupported_mime_without_dispatch() -> None:
    client = _FakeCdpClient()
    with pytest.raises(_exceptions.DriverUnsupported, match="image/png"):
        _run(
            _cdp.perform_external_drop(
                client,
                "s1",
                x=1.0,
                y=2.0,
                items=[{"mimeType": "image/png", "data": "x"}],
            )
        )
    assert not any(name == "Input.dispatchDragEvent" for name, _ in client.calls)


def test_cdp_external_drop_happy_path_with_temp_file() -> None:
    client = _FakeCdpClient()
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        handle.write(b"payload")
        path = handle.name
    try:
        result = _run(
            _cdp.perform_external_drop(
                client,
                "s1",
                x=5.0,
                y=6.0,
                paths=[path],
                items=[{"mimeType": "text/plain", "data": "hello"}],
            )
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert result["ok"] is True
    types = [params.get("type") for name, params in client.calls if name == "Input.dispatchDragEvent"]
    assert types == ["dragEnter", "dragOver", "drop"]


def test_cdp_handle_javascript_dialog_calls_cdp() -> None:
    client = _FakeCdpClient()
    _run(_cdp.handle_javascript_dialog(client, "s1", accept=True, prompt_text="hi"))
    assert ("Page.handleJavaScriptDialog", {"accept": True, "promptText": "hi"}) in client.calls


def test_driver_hover_serializes_to_sidecar() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock(
        return_value={"ok": True, "detail": "hovered", "document_changed": False, "driver_generation": 4}
    )
    driver = _make_driver_with_transport(transport)
    result = _run(driver.execute(Hover(DriverRef("bnid:7", "g1"))))
    assert result.ok is True
    method, params = transport.request.call_args.args
    assert method == "hover"
    assert params["ref"] == {"kind": "driver", "handle": "bnid:7", "driver_generation": 3}


def test_driver_handle_dialog_serializes() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock(
        return_value={
            "ok": True,
            "detail": "armed for next dialog; call before the action that opens it",
            "document_changed": False,
            "driver_generation": 4,
        }
    )
    driver = _make_driver_with_transport(transport)
    result = _run(driver.execute(HandleDialog(accept=True, prompt_text="x")))
    assert result.ok is True
    method, params = transport.request.call_args.args
    assert method == "handle_dialog"
    assert params == {"accept": True, "prompt_text": "x"}


def test_driver_drop_missing_path_skips_sidecar() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock()
    driver = _make_driver_with_transport(transport)
    missing = str(Path(tempfile.gettempdir()) / "openjiuwen_missing_drop2.txt")
    result = _run(driver.execute(Drop(DriverRef("bnid:9", "g1"), paths=(missing,))))
    assert result.ok is False
    assert "not found" in result.detail.lower()
    transport.request.assert_not_called()


def test_driver_drop_temp_file_calls_sidecar() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock(
        return_value={"ok": True, "detail": "dropped", "document_changed": False, "driver_generation": 5}
    )
    driver = _make_driver_with_transport(transport)
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        handle.write(b"x")
        path = handle.name
    try:
        result = _run(
            driver.execute(
                Drop(
                    DriverRef("bnid:9", "g1"),
                    paths=(path,),
                    data=(("text/plain", "hi"),),
                )
            )
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.ok is True
    method, params = transport.request.call_args.args
    assert method == "drop"
    assert params["paths"] == [str(Path(path).resolve())]
    assert params["data"][0]["mimeType"] == "text/plain"
