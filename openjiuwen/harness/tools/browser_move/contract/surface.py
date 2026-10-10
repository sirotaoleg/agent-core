# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What a surface hands to the core: its settings, its profile, its policy rules and its conformance scene."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .actions import Action, Effect
from .observation import Observation
from .results import ActionResult


class SurfaceSettings(Protocol):
    """Settings a surface parses from its own environment variables; the core reads only ``provisioning``."""

    @property
    def provisioning(self) -> str: ...


@dataclass(frozen=True)
class ToolSpec:
    """One model-facing tool. It may cover several commands (for example one tool with an operation argument)."""

    name: str
    commands: tuple[type[Action], ...]
    description: str = ""
    prompt_section: str | None = None


@dataclass(frozen=True)
class PolicyRule:
    """Checked by the security rail before middleware; ``check`` returns a deny reason or ``None``."""

    command_type: type[Action]
    check: Callable[[Action], str | None]


SceneStep = Callable[[Sequence[Observation]], Action]
HandledActions = Mapping[type[Action], frozenset[type]]


@dataclass(frozen=True)
class SceneCase:
    """A short script the conformance suite plays in a scene; the last step is the command under test."""

    steps: tuple[SceneStep, ...]
    check: Callable[[Observation, Observation, ActionResult], bool] | None = None
    observe_between: bool = True
    cleanup: tuple[SceneStep, ...] = ()


def _not_provided(*_args: Any) -> str:
    return "the scene does not provide this case"


@dataclass(frozen=True)
class Scene:
    """A reproducible setting the conformance suite runs a driver in; a case builder returns a skip reason."""

    surface: str
    start: Callable[[], None]
    stop: Callable[[], None]
    enter: Callable[[], Sequence[Action]] = tuple
    happy: Callable[[type[Action], type | None, HandledActions], SceneCase | str] = _not_provided
    stale: Callable[[HandledActions], SceneCase | str] = _not_provided
    intercepted: Callable[[HandledActions], SceneCase | str] = _not_provided
    timeout: Callable[[HandledActions], SceneCase | str] = _not_provided
    context_changes: Mapping[str, Callable[[HandledActions], SceneCase | str]] = field(default_factory=dict, hash=False)
    sensitive: Mapping[str, Callable[[HandledActions, str], SceneCase | str]] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class SurfaceProfile:
    """The contract between the core and one surface; the core uses nothing else from a surface."""

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


@dataclass(frozen=True)
class SurfaceBundle:
    """What a surface factory returns: the surface settings and, once the surface has one, its profile."""

    settings: SurfaceSettings
    profile: SurfaceProfile | None = None
