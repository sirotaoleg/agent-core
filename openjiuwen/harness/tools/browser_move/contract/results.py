# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What ``Driver.execute`` returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .observation import Observation, SurfaceContext
from .refs import ContextRef


@dataclass(frozen=True, kw_only=True)
class ActionResult:
    """The typed outcome of one command.

    ``context_changed`` comes from the driver's own signal: a new document loaded (including a reload of the same
    address), the address changed, a dialog opened, or a context was opened, closed or switched.
    ``generation`` is filled by the runtime's state tracker after the call; drivers leave it empty.
    ``observation`` is a fresh observation when the driver got one for free, so the runtime can skip ``observe()``.
    ``surface`` is the surface context after the command.
    """

    ok: bool
    value: Any = field(default=None, hash=False)
    detail: str | None = None
    context_changed: bool = False
    generation: str = ""
    observation: Observation | None = None
    source_context: ContextRef | None = None
    opened_contexts: tuple[ContextRef, ...] = ()
    surface: SurfaceContext | None = None
