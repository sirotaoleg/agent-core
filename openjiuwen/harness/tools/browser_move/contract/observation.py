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
    """Base of the per-surface context; the core reads only ``identity``, the surface's own code reads the rest."""

    @property
    def identity(self) -> str:
        """Where the surface is, as the surface names it; a change between two non-empty values is a new generation."""
        return ""


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
    """A snapshot of the focused context; ``generation`` is stamped by the runtime, so drivers leave it empty."""

    context: ContextRef
    generation: str = ""
    elements: tuple[Element, ...] = ()
    screenshot: Screenshot | None = None
    viewport: Viewport | None = None
    native_text: str | None = None
    surface: SurfaceContext | None = None

    @property
    def fingerprint(self) -> str:
        """Backstop for change detection; ``ActionResult.context_changed`` is the primary signal."""
        payload = [self.context.id, [[e.ref.ref, e.role, e.name] for e in self.elements]]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]
