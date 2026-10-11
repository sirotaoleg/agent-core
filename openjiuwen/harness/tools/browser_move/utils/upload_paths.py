# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Normalize local file paths for file uploads (runtime + driver)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


def _strip_path_text(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


def _try_open(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def _candidates_for(text: str, upload_root: Path | None) -> list[Path]:
    """Build resolve candidates: expanduser path, then the upload root fallback."""
    primary = Path(text).expanduser()
    out: list[Path] = [primary]
    if primary.is_absolute():
        return out
    if upload_root is not None:
        # Bare names / relative paths listed by list_upload_files live under the root.
        out.append(upload_root / text)
        out.append(upload_root / primary.name)
    return out


def resolve_upload_file_paths(
    paths: Sequence[str], *, upload_root: Path | None = None
) -> tuple[list[str], str | None]:
    """Return ``(absolute_existing_paths, error_or_none)``.

    - Strips wrapping quotes (LLM/tool-arg footgun).
    - ``expanduser().resolve()`` before existence checks.
    - Relative / bare names also try ``upload_root`` (``BROWSER_UPLOAD_ROOT``)
      when given (same catalog as ``list_upload_files``).
    - Fail closed: any missing or unreadable path yields an error and an empty
      existing list is still returned for the ones that passed (callers reject
      the whole batch when error is set).
    - Error text says \"not found\" unless an ``open``/read was attempted and failed.
    """
    existing: list[str] = []
    missing: list[str] = []
    unreadable: list[str] = []
    seen: set[str] = set()

    for raw in paths or []:
        text = _strip_path_text(str(raw))
        if not text:
            continue
        resolved_ok: str | None = None
        last_resolved = text
        for candidate in _candidates_for(text, upload_root):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            last_resolved = str(resolved)
            if not resolved.is_file():
                continue
            if not _try_open(resolved):
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
