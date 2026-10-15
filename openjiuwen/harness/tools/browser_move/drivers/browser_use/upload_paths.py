# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve the local file paths of an upload or a drop for the browser-use driver."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import upload_paths


def _try_open(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def resolve_upload_file_paths(paths: Sequence[str], *, upload_root: Path | None = None) -> tuple[list[str], str | None]:
    """Return ``(absolute_existing_paths, error_or_none)``; relative names resolve under ``upload_root`` only."""
    try:
        resolved = upload_paths(tuple(str(raw) for raw in paths or ()), upload_root)
    except ValueError as exc:
        return [], str(exc)
    existing: list[str] = []
    missing: list[str] = []
    unreadable: list[str] = []
    for path in resolved:
        if not path.is_file():
            missing.append(str(path))
        elif not _try_open(path):
            unreadable.append(str(path))
        elif str(path) not in existing:
            existing.append(str(path))

    if not existing and not missing and not unreadable:
        return [], "'paths' must contain at least one file path"

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


__all__ = ["resolve_upload_file_paths"]
