#!/usr/bin/env python
# coding: utf-8
"""Tests for file-upload helpers and built-in upload actions."""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from openjiuwen.harness.tools.browser_move.controllers.action import (
    ActionController,
    _list_dir_files,
    register_builtin_actions,
)


def _run(coro):
    return asyncio.run(coro)


def _make_ctl_with_builtins(upload_root: Path | None = None) -> ActionController:
    ctl = ActionController()
    register_builtin_actions(controller=ctl, upload_root=upload_root)
    return ctl


# ---------------------------------------------------------------------------
# _list_dir_files
# ---------------------------------------------------------------------------


def test_list_dir_files_returns_file_entries_with_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "doc.pdf").write_bytes(b"hello")
        (root / "img.png").write_bytes(b"xx")
        result = _list_dir_files(root)

    names = {e["name"] for e in result}
    assert names == {"doc.pdf", "img.png"}
    for entry in result:
        assert "path" in entry
        assert entry["size_bytes"] >= 0


def test_list_dir_files_handles_missing_directory() -> None:
    result = _list_dir_files(Path("/nonexistent/path/that/does/not/exist"))
    assert result == []


# ---------------------------------------------------------------------------
# list_upload_files builtin action
# ---------------------------------------------------------------------------


def test_list_upload_files_returns_error_when_env_not_set() -> None:
    ctl = _make_ctl_with_builtins()
    result = _run(ctl.run_action("list_upload_files"))
    assert result["ok"] is False
    assert "BROWSER_UPLOAD_ROOT" in result["error"]
    assert result["files"] == []


def test_list_upload_files_returns_error_when_dir_missing() -> None:
    ctl = _make_ctl_with_builtins(Path("/tmp/does_not_exist_xyz_99"))
    result = _run(ctl.run_action("list_upload_files"))
    assert result["ok"] is False
    assert result["files"] == []


def test_list_upload_files_returns_file_list_when_dir_exists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "report.xlsx").write_bytes(b"data")
        result = _run(_make_ctl_with_builtins(root).run_action("list_upload_files"))

    assert result["ok"] is True
    assert any(f["name"] == "report.xlsx" for f in result["files"])

