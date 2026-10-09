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
    """The typed outcome of one command; drivers leave ``generation`` empty for the runtime to fill."""

    ok: bool
    value: Any = field(default=None, hash=False)
    detail: str | None = None
    context_changed: bool = False
    generation: str = ""
    observation: Observation | None = None
    source_context: ContextRef | None = None
    opened_contexts: tuple[ContextRef, ...] = ()
    surface: SurfaceContext | None = None
