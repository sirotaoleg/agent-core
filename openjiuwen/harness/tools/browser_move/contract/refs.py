# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ways a command points at an element or a context; element refs carry their observation's ``generation``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union


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


# ``ContextRef`` is not an element ref kind: no handler accepts it.
Ref = Union[DriverRef, PointRef, IndexRef, Locator]
REF_KINDS: tuple[type, ...] = (DriverRef, PointRef, IndexRef, Locator)


class RefResolver(Protocol):
    """What a tool's argument decoder asks the runtime about ids the model sends."""

    @property
    def contexts(self) -> tuple[ContextRef, ...]: ...

    @property
    def current_context(self) -> ContextRef | None: ...

    def resolve(self, model_id: str, generation: str) -> Ref:
        """The ref behind a runtime-minted element id; raises ``ValueError`` for an id it does not know."""

    def generation_of(self, ref: str) -> str:
        """The generation a driver ref was last seen in; the current one for a ref never seen."""
