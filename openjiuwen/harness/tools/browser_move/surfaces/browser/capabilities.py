# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The public capability catalog as tool spec names and the (command, ref kind) pairs those tools need."""

from __future__ import annotations

from collections.abc import Mapping

from openjiuwen.harness.tools.browser_move.contract import Action, DriverRef, Locator, PointRef
from openjiuwen.harness.tools.browser_move.runtime.browser_capabilities import DEFAULT_BROWSER_CAPABILITIES
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import BROWSER_TOOL_SPECS

_SPECS = {spec.name: spec for spec in BROWSER_TOOL_SPECS}
# An extra tool over the last observation, so it has no spec but stays in the catalog.
_FIND = "browser_find"
_ELEMENT = (DriverRef, Locator)

# None: the tool may run without an element ref; a tool not listed takes none.
_REF_KINDS: Mapping[str, tuple[type | None, ...]] = {
    "browser_click": _ELEMENT,
    "browser_type": _ELEMENT,
    "browser_hover": _ELEMENT,
    "browser_select_option": _ELEMENT,
    "browser_drag": _ELEMENT,
    "browser_drop": _ELEMENT,
    "browser_fill_form": _ELEMENT,
    "browser_file_upload": (None, *_ELEMENT),
    "browser_evaluate": (None, *_ELEMENT),
    "browser_mouse_click_xy": (PointRef,),
    "browser_mouse_move_xy": (PointRef,),
    "browser_mouse_drag_xy": (PointRef,),
}

CAPABILITY_TOOLS: Mapping[str, tuple[str, ...]] = {
    capability.name: tuple(name for name in capability.tool_names if name in _SPECS or name == _FIND)
    for capability in DEFAULT_BROWSER_CAPABILITIES
}


def _pairs(names: tuple[str, ...]) -> frozenset[tuple[type[Action], type | None]]:
    return frozenset(
        (command, kind)
        for name in names
        if name in _SPECS
        for command in _SPECS[name].commands
        for kind in _REF_KINDS.get(name, (None,))
    )


CAPABILITY_CATALOG: Mapping[str, frozenset[tuple[type[Action], type | None]]] = {
    entry: _pairs(names) for entry, names in CAPABILITY_TOOLS.items()
}
