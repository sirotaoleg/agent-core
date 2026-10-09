# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ways a command points at an element or at a context.

Every element ref carries the ``generation`` of the observation it came from, so a driver can tell a stale ref
before it sends anything (``StaleRef`` is raised only before dispatch).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class DriverRef:
    """The model-facing element ref (``e57``) minted by the driver in an observation."""

    ref: str
    generation: str


@dataclass(frozen=True)
class PointRef:
    """A point in the pixels of that generation's screenshot."""

    x: int
    y: int
    generation: str


@dataclass(frozen=True)
class IndexRef:
    """A driver-internal element index; never shown to the model."""

    index: int
    generation: str


@dataclass(frozen=True)
class Locator:
    """A CSS selector and/or visible text that identifies one element."""

    css: str | None
    text: str | None
    generation: str


@dataclass(frozen=True)
class ContextRef:
    """A context the driver can focus: ``kind`` is surface-defined (for example a window or an activity)."""

    id: str
    kind: str
    title: str | None = None


# The element ref kinds a handler may accept (``@handles(..., refs={...})``). ``ContextRef`` is not one of them.
Ref = Union[DriverRef, PointRef, IndexRef, Locator]
REF_KINDS: tuple[type, ...] = (DriverRef, PointRef, IndexRef, Locator)
