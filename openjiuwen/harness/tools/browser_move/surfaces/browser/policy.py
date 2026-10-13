# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The browser surface's policy rules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...contract import Action, PolicyRule, PressKey, RunScript
from ...contract.surface import SurfaceContext
from .commands import Drop, UploadFile

# The managed Chrome shares the host's OS clipboard: a paste or a clipboard read would hand whatever the user last
# copied to the page.
CLIPBOARD_DENIED = (
    "Blocked: the clipboard belongs to the host machine, not to this task, so pasting or reading it is not allowed. "
    "Read the value from the page instead (its text or an input's value) and type it."
)
_PASTE_MODIFIERS = frozenset({"control", "ctrl", "meta", "cmd", "command", "controlormeta"})
_CLIPBOARD_READ = re.compile(
    r"clipboard['\"\]]*\s*(?:\.|\[\s*['\"])\s*read|execCommand\s*\(\s*['\"`]paste", re.IGNORECASE
)


def _is_paste_chord(key: str) -> bool:
    *modifiers, final = [part.strip().lower() for part in key.split("+")]
    held = set(modifiers)
    return (final == "v" and bool(held & _PASTE_MODIFIERS)) or (final == "insert" and "shift" in held)


def _deny_paste_key(action: Action, context: SurfaceContext | None) -> str | None:
    return CLIPBOARD_DENIED if isinstance(action, PressKey) and _is_paste_chord(action.key) else None


def _deny_clipboard_read(action: Action, context: SurfaceContext | None) -> str | None:
    return CLIPBOARD_DENIED if isinstance(action, RunScript) and _CLIPBOARD_READ.search(action.code) else None


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _confined(raw: str, roots: tuple[Path, ...]) -> bool:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    path = Path(text).expanduser()
    # A relative name is read under a root by the driver, so it must not climb out of the one it is joined to.
    if path.is_absolute():
        return any(_inside(path.resolve(), root) for root in roots)
    return any(_inside((root / path).resolve(), root) for root in roots)


def upload_rules(upload_root: Path | None, workspace_root: Path | None) -> tuple[PolicyRule, ...]:
    """Deny upload and drop paths outside the upload root and the workspace; with neither, outside the process cwd."""

    def deny(action: Action, context: SurfaceContext | None) -> str | None:
        configured = tuple(Path(root).expanduser().resolve() for root in (upload_root, workspace_root) if root)
        roots = configured or (Path.cwd().resolve(),)
        outside = [str(raw) for raw in getattr(action, "paths", ()) if not _confined(str(raw), roots)]
        if not outside:
            return None
        allowed = ", ".join(str(root) for root in roots)
        return (
            f"Blocked: {', '.join(repr(raw) for raw in outside)} is outside the folders files may be uploaded from "
            f"({allowed}). Upload files from BROWSER_UPLOAD_ROOT (list_upload_files) or the workspace."
        )

    return (PolicyRule(UploadFile, deny), PolicyRule(Drop, deny))


POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(PressKey, _deny_paste_key),
    PolicyRule(RunScript, _deny_clipboard_read),
)


def policy_rules(settings: Any) -> tuple[PolicyRule, ...]:
    """The browser's rules for ``settings``: the fixed ones and the upload confinement to its folders."""
    return POLICY_RULES + upload_rules(settings.upload_root, settings.workspace_root)


def policy_denial(
    action: Action, context: SurfaceContext | None, rules: Iterable[PolicyRule] = POLICY_RULES
) -> str | None:
    """The first rule's deny reason for ``action``, for helpers that execute commands without the policy rail."""
    for rule in rules:
        if isinstance(action, rule.command_type):
            reason = rule.check(action, context)
            if reason:
                return reason
    return None


__all__ = ["CLIPBOARD_DENIED", "POLICY_RULES", "policy_denial", "policy_rules", "upload_rules"]
