# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What a driver sees: one ``Observation`` per ``observe()`` call."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .refs import ContextRef, DriverRef


@dataclass(frozen=True)
class SurfaceContext:
    """Empty base for the typed per-surface context; each surface subclasses it in its own package.

    Nothing in ``contract/`` or the core reads a field of a subclass: it is passed through to the surface's renderer
    and rails.
    """


@dataclass(frozen=True)
class Viewport:
    """Size of the visible area, in the same pixels as ``Element.bounds``."""

    width: int
    height: int


@dataclass(frozen=True)
class Screenshot:
    """A PNG image; ``PointRef`` coordinates are always in its pixels. ``scale`` is image pixels per viewport pixel."""

    png: bytes
    width: int
    height: int
    scale: float = 1.0


@dataclass(frozen=True)
class Element:
    """One element the model can point at. ``bounds`` is ``(x, y, width, height)`` in viewport pixels."""

    ref: DriverRef
    role: str
    name: str
    value: str | None = None
    bounds: tuple[float, float, float, float] | None = None
    enabled: bool = True
    focused: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict, hash=False)


@dataclass(frozen=True, kw_only=True)
class Observation:
    """A snapshot of the focused context.

    ``generation`` is stamped by the runtime's state tracker, not by the driver, so drivers leave it empty.
    ``native_text`` is the driver's own textual snapshot, if it has one; the renderer decides whether to show it.
    """

    context: ContextRef
    generation: str = ""
    elements: tuple[Element, ...] = ()
    screenshot: Screenshot | None = None
    viewport: Viewport | None = None
    native_text: str | None = None
    surface: SurfaceContext | None = None

    @property
    def fingerprint(self) -> str:
        """Stable hash over element refs, roles and names plus the context id.

        A backstop for change detection only: the driver's ``ActionResult.context_changed`` is the primary signal.
        Generations, screenshots and values are left out, so two observations of an unchanged scene match.
        """
        payload = [self.context.id, [[e.ref.ref, e.role, e.name] for e in self.elements]]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]
