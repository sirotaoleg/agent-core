# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Playwright-style key names normalized to browser-use's key-combo format; stdlib only."""

from __future__ import annotations

_MODIFIER_ALIASES: dict[str, str] = {
    "control": "ctrl",
    "ctrl": "ctrl",
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
}


def normalize_key_combo(keys: str) -> str:
    """Normalize modifier tokens (``Control+A`` -> ``ctrl+A``); the final key keeps its case."""
    parts = [p for p in keys.split("+") if p]
    if not parts:
        return keys
    *modifiers, final_key = parts
    normalized_modifiers = [_MODIFIER_ALIASES.get(m.strip().lower(), m.strip().lower()) for m in modifiers]
    return "+".join([*normalized_modifiers, final_key.strip()])


__all__ = ["normalize_key_combo"]
