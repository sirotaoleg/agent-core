#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for browser_use driver upload path validation (fail closed)."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from openjiuwen.harness.tools.browser_move.contract import ActionResult, DriverRef
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import BrowserUseDriver
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import UploadFile
from openjiuwen.harness.tools.browser_move.utils.upload_paths import resolve_upload_file_paths

_FILE_INPUT = DriverRef("bnid:5", "g1")


def _run(coro):
    return asyncio.run(coro)


def _make_driver_with_transport(transport: AsyncMock) -> BrowserUseDriver:
    driver = BrowserUseDriver()
    driver._transport = transport
    driver._driver_generation = 7
    return driver


def test_upload_files_missing_path_returns_failed_act_result_without_sidecar() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock()
    driver = _make_driver_with_transport(transport)
    missing = str(Path(tempfile.gettempdir()) / "openjiuwen_missing_upload_no_such_file.txt")

    result = _run(driver.execute(UploadFile(_FILE_INPUT, (missing,))))

    assert isinstance(result, ActionResult)
    assert result.ok is False
    assert "not found" in result.detail.lower()
    assert "not readable" not in result.detail.lower()
    assert "openjiuwen_missing_upload_no_such_file.txt" in result.detail
    assert "BROWSER_UPLOAD_ROOT" in result.detail
    transport.request.assert_not_called()


def test_upload_files_existing_path_calls_sidecar_with_resolved_absolute() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock(
        return_value={
            "ok": True,
            "detail": "uploaded 1 file(s)",
            "document_changed": False,
            "driver_generation": 8,
        }
    )
    driver = _make_driver_with_transport(transport)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        handle.write(b"hello")
        existing = handle.name
    try:
        parent = Path(existing).parent
        rel = Path(existing).name
        old_cwd = Path.cwd()
        try:
            os.chdir(parent)
            result = _run(driver.execute(UploadFile(_FILE_INPUT, (rel,))))
        finally:
            os.chdir(old_cwd)
    finally:
        Path(existing).unlink(missing_ok=True)

    assert result.ok is True
    transport.request.assert_called_once()
    method, params = transport.request.call_args.args
    assert method == "upload_files"
    assert params["paths"] == [str(Path(existing).resolve())]


def test_upload_files_tilde_path_is_expanded_before_sidecar() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock(
        return_value={
            "ok": True,
            "detail": "uploaded 1 file(s)",
            "document_changed": False,
            "driver_generation": 8,
        }
    )
    driver = _make_driver_with_transport(transport)

    home = Path.home()
    with tempfile.NamedTemporaryFile(suffix=".txt", dir=home, delete=False) as handle:
        handle.write(b"home-upload")
        existing = Path(handle.name)
    try:
        tilde_path = f"~/{existing.name}"
        result = _run(driver.execute(UploadFile(_FILE_INPUT, (tilde_path,))))
    finally:
        existing.unlink(missing_ok=True)

    assert result.ok is True
    method, params = transport.request.call_args.args
    assert method == "upload_files"
    assert params["paths"] == [str(existing.resolve())]
    assert "~" not in params["paths"][0]


def test_upload_files_strips_wrapping_quotes_before_is_file() -> None:
    transport = AsyncMock()
    transport.request = AsyncMock(
        return_value={
            "ok": True,
            "detail": "uploaded 1 file(s)",
            "document_changed": False,
            "driver_generation": 8,
        }
    )
    driver = _make_driver_with_transport(transport)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        handle.write(b"quoted")
        existing = handle.name
    try:
        quoted = f"'{existing}'"
        result = _run(driver.execute(UploadFile(_FILE_INPUT, (quoted,))))
    finally:
        Path(existing).unlink(missing_ok=True)

    assert result.ok is True
    method, params = transport.request.call_args.args
    assert params["paths"] == [str(Path(existing).resolve())]


def test_resolve_upload_file_paths_uses_browser_upload_root_for_basename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "staged_upload.txt"
        target.write_text("staged", encoding="utf-8")
        existing, err = resolve_upload_file_paths(["staged_upload.txt"], upload_root=root.resolve())

    assert err is None
    assert existing == [str(target.resolve())]
