# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What a surface hands to the core: its profile, its policy rules and its conformance scene. Dataclasses only."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .actions import Action, Effect
from .observation import Observation
from .results import ActionResult


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


SceneStep = Callable[[Sequence[Observation]], Action]
HandledActions = Mapping[type[Action], frozenset[type]]


@dataclass(frozen=True)
class SceneCase:
    """A short script the conformance suite plays in a scene.

    Each step builds one command from the observations taken so far (oldest first). The last step is the command
    under test; the steps before it set the case up. With ``observe_between`` the suite observes after every setup
    step; without it every step sees only the first observation (for a setup step that leaves the screen modal, such
    as an open dialog). ``check(before, after, result)`` tells whether the command under test took effect: ``before``
    is the observation it was built from, ``after`` a fresh one. ``cleanup`` steps run afterwards, best effort; with
    ``observe_between`` they also see an observation taken after the command under test.
    """

    steps: tuple[SceneStep, ...]
    check: Callable[[Observation, Observation, ActionResult], bool] | None = None
    observe_between: bool = True
    cleanup: tuple[SceneStep, ...] = ()


def _not_provided(*_args: Any) -> str:
    return "the scene does not provide this case"


@dataclass(frozen=True)
class Scene:
    """A reproducible setting the conformance suite runs a driver in, supplied by the surface.

    ``start``/``stop`` bring the scene up and down. After ``start``, ``enter()`` gives the commands that put a fresh
    driver into the scene's initial state. The case builders get the driver's ``info.actions`` (so they can pick ref
    kinds the driver accepts) and return a ``SceneCase``, or the reason the scene cannot provide that case as a string:
    the suite skips with that reason. ``happy(command, ref_kind, actions)`` is the happy path of one command with one
    ref kind (``None`` for a command without refs); ``stale`` ends in a command with a stale ref; ``intercepted`` in a
    click another element receives; ``timeout`` in a command that times out after dispatch; each entry of
    ``context_changes`` in a command that must report ``context_changed``.
    """

    surface: str
    start: Callable[[], None]
    stop: Callable[[], None]
    enter: Callable[[], Sequence[Action]] = tuple
    happy: Callable[[type[Action], type | None, HandledActions], SceneCase | str] = _not_provided
    stale: Callable[[HandledActions], SceneCase | str] = _not_provided
    intercepted: Callable[[HandledActions], SceneCase | str] = _not_provided
    timeout: Callable[[HandledActions], SceneCase | str] = _not_provided
    context_changes: Mapping[str, Callable[[HandledActions], SceneCase | str]] = field(default_factory=dict, hash=False)


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
