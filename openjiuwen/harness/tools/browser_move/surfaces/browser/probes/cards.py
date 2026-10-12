# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The page script that finds repeated cards; the script body is cards.js, the parameters go in at build time."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Dict, List, Optional

_TEMPLATE = files(__package__).joinpath("cards.js").read_text(encoding="utf-8").strip()


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def build_card_probe_js(
    *,
    max_cards: int = 20,
    viewport_only: bool = True,
    include_buttons: bool = True,
    query: str = "",
    site_profiles: Optional[List[Dict[str, Any]]] = None,
    selector_cache_records: Optional[List[Dict[str, Any]]] = None,
    generation_id: str = "g0",
) -> str:
    """Build the JavaScript for compact repeated-card probing."""

    params: Dict[str, Any] = {
        "max_cards": _clamp_int(max_cards, default=20, minimum=1, maximum=50),
        "viewport_only": bool(viewport_only),
        "include_buttons": bool(include_buttons),
        "query": str(query or "").strip().lower(),
        "site_profiles": site_profiles or [],
        "selector_cache_records": selector_cache_records or [],
        "generation_id": str(generation_id or "g0"),
    }
    params_json = json.dumps(params, ensure_ascii=False)

    return _TEMPLATE.replace("__PARAMS__", params_json)
