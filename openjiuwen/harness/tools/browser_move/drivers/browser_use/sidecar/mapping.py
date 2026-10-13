# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Map browser-use's state summary to wire observation dicts, duck-typed and stdlib only."""

from __future__ import annotations

from typing import Any


def map_dom_rect(rect: Any) -> dict[str, float] | None:
    """Map a DOMRect-like object to a wire ``Box`` dict, or None."""
    if rect is None:
        return None
    return {
        "x": float(getattr(rect, "x", 0.0) or 0.0),
        "y": float(getattr(rect, "y", 0.0) or 0.0),
        "width": float(getattr(rect, "width", 0.0) or 0.0),
        "height": float(getattr(rect, "height", 0.0) or 0.0),
    }


def map_enhanced_node(index: int, node: Any, *, driver_generation: int) -> dict[str, Any]:
    """Map one DOM node to a wire element dict stamped with ``driver_generation``."""
    ax_node = getattr(node, "ax_node", None)
    role = getattr(ax_node, "role", None) if ax_node is not None else None
    name = getattr(ax_node, "name", None) if ax_node is not None else None
    attributes = dict(getattr(node, "attributes", None) or {})
    backend_node_id = int(getattr(node, "backend_node_id", 0) or 0)
    return {
        "index": int(index),
        "backend_node_id": backend_node_id,
        "driver_ref": {"handle": f"bnid:{backend_node_id}", "driver_generation": int(driver_generation)},
        "frame_id": getattr(node, "frame_id", None),
        "tag": str(getattr(node, "node_name", "") or "").lower(),
        "role": role,
        "name": name,
        "value": getattr(node, "node_value", None),
        "attributes": attributes,
        "box": map_dom_rect(getattr(node, "absolute_position", None)),
        "visible": bool(getattr(node, "is_visible", False)),
        "scrollable": bool(getattr(node, "is_scrollable", False)),
    }


def map_tab(tab: Any, current_url: str) -> dict[str, Any]:
    """Map one tab to a wire tab dict; a tab is active when its url is the current url."""
    tab_url = str(getattr(tab, "url", "") or "")
    return {
        "target_id": str(getattr(tab, "target_id", "") or ""),
        "url": tab_url,
        "title": str(getattr(tab, "title", "") or ""),
        "active": bool(current_url) and tab_url == current_url,
    }


def map_browser_state_summary(
    summary: Any,
    *,
    driver_generation: int,
    captured_at: float,
    include_screenshot: bool = False,
) -> dict[str, Any]:
    """Map a ``BrowserStateSummary``-like object to a wire ``Observation`` dict."""
    dom_state = getattr(summary, "dom_state", None)
    selector_map = dict(getattr(dom_state, "selector_map", None) or {})
    elements = tuple(
        map_enhanced_node(index, node, driver_generation=driver_generation)
        for index, node in sorted(selector_map.items())
    )

    ax_text: str | None = None
    llm_representation = getattr(dom_state, "llm_representation", None) if dom_state is not None else None
    if callable(llm_representation):
        try:
            ax_text = llm_representation()
        except Exception:  # noqa: BLE001
            ax_text = None

    current_url = str(getattr(summary, "url", "") or "")
    tabs = tuple(map_tab(tab, current_url) for tab in (getattr(summary, "tabs", None) or []))

    errors: list[str] = [str(e) for e in (getattr(summary, "browser_errors", None) or [])]
    state_error = getattr(summary, "state_error", None)
    if state_error:
        errors.append(str(state_error))

    screenshot_b64 = getattr(summary, "screenshot", None) if include_screenshot else None

    return {
        "url": current_url,
        "title": str(getattr(summary, "title", "") or ""),
        "tabs": tabs,
        "elements": elements,
        "ax_text": ax_text,
        "screenshot_b64": screenshot_b64,
        "pixels_above": int(getattr(summary, "pixels_above", 0) or 0),
        "pixels_below": int(getattr(summary, "pixels_below", 0) or 0),
        "errors": tuple(errors),
        "is_pdf_viewer": bool(getattr(summary, "is_pdf_viewer", False)),
        "captured_at": float(captured_at),
        "driver_generation": int(driver_generation),
    }


__all__ = [
    "map_browser_state_summary",
    "map_dom_rect",
    "map_enhanced_node",
    "map_tab",
]
