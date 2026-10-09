# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What a surface hands to the core: its profile, its policy rules and its conformance scene. Dataclasses only."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .actions import Action, Effect
from .observation import Observation
from .refs import Ref


@dataclass(frozen=True)
class ToolSpec:
    """One model-facing tool. It may cover several commands (for example one tool with an operation argument)."""

    name: str
    commands: tuple[type[Action], ...]
    description: str = ""
    prompt_section: str | None = None


@dataclass(frozen=True)
class PolicyRule:
    """Checked by the security rail before middleware and the driver.

    ``check`` returns the reason to deny the command, or ``None`` to allow it.
    """

    command_type: type[Action]
    check: Callable[[Action], str | None]


@dataclass(frozen=True)
class Scene:
    """A reproducible setting the conformance suite runs a driver in, supplied by the surface.

    ``start``/``stop`` bring the scene up and down. After ``start``, ``enter()`` gives the commands that put a fresh
    driver into the scene's initial state, ``target(observation)`` picks the element to click there, ``changed(before,
    after)`` tells whether that click took effect, and ``context_changes()`` gives commands that must report
    ``context_changed``.
    """

    surface: str
    start: Callable[[], None]
    stop: Callable[[], None]
    enter: Callable[[], Sequence[Action]]
    target: Callable[[Observation], Ref]
    changed: Callable[[Observation, Observation], bool]
    context_changes: Callable[[], Sequence[Action]] = tuple


@dataclass(frozen=True)
class SurfaceProfile:
    """The contract between the core and one surface; the core uses nothing else from a surface.

    ``renderer`` turns an ``Observation`` into content blocks for the model. ``capability_catalog`` maps a public
    catalog entry to ``(command, ref kind)`` pairs (``None`` for commands without refs). ``native_tools``,
    ``extra_tools`` and ``rails`` are registered by the core without knowing them. ``messages`` holds model-facing
    texts the core needs; ``settings`` is the surface settings object parsed from its own environment prefix.
    """

    name: str
    commands: tuple[type[Action], ...]
    renderer: Callable[[Observation], Sequence[Any]]
    tool_specs: tuple[ToolSpec, ...] = ()
    native_tools: tuple[Any, ...] = ()
    capability_catalog: Mapping[str, frozenset[tuple[type[Action], type | None]]] = field(
        default_factory=dict, hash=False
    )
    extra_tools: tuple[Any, ...] = ()
    rails: tuple[Any, ...] = ()
    policy_rules: tuple[PolicyRule, ...] = ()
    timeouts: Mapping[Effect, float] = field(default_factory=dict, hash=False)
    messages: Mapping[str, str] = field(default_factory=dict, hash=False)
    settings: Any = field(default=None, hash=False)
    scene: Scene | None = None
