# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""What a surface hands to the core: its settings, its profile, its policy rules and its conformance scene."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, Union

from .actions import Action, Effect
from .errors import DriverError
from .observation import Observation, Screenshot, SurfaceContext
from .refs import RefResolver
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
    schema: Mapping[str, Any] | None = field(default=None, hash=False)
    build: Callable[[Mapping[str, Any], RefResolver], Action] | None = field(default=None, hash=False)


ContentBlock = Union[str, Screenshot]


class Renderer(Protocol):
    """Turns what a driver returns into the content blocks the model reads (text and/or image)."""

    def observation(self, observation: Observation) -> tuple[ContentBlock, ...]: ...

    def result(self, action: Action, result: ActionResult) -> tuple[ContentBlock, ...]: ...

    def error(
        self, action: Action, error: DriverError, observation: Observation | None
    ) -> tuple[ContentBlock, ...]: ...


@dataclass(frozen=True)
class PolicyRule:
    """Checked by the security rail before middleware; ``check`` returns a deny reason or ``None``."""

    command_type: type[Action]
    check: Callable[[Action, SurfaceContext | None], str | None]


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


class ContextFactory(Protocol):
    """Builds one tool or rail from the core's tool context (``runtime.tool_factory.ToolContext``)."""

    def __call__(self, context: Any, /) -> Any: ...


class CustomActions(Protocol):
    def __call__(self, controller: Any, context: Any, /) -> None: ...


class Metadata(Protocol):
    """``(metadata, error)`` of the current context; ``error`` is ``None`` on success."""

    def __call__(self, context: Any, /) -> Awaitable[tuple[Mapping[str, Any], str | None]]: ...


class PolicyProbe(Protocol):
    def __call__(self, context: Any, source: str, params: Mapping[str, Any], /) -> Awaitable[Mapping[str, Any]]: ...


class Activate(Protocol):
    def __call__(self, context: Any, address: str, /) -> Awaitable[bool]: ...


class WorkerPrompt(Protocol):
    def __call__(
        self,
        screenshot_subdir: str,
        artifacts_subdir: str,
        *,
        tool_names: tuple[str, ...] | None = None,
        driver_name: str,
    ) -> str: ...


@dataclass(frozen=True)
class Capability:
    """One capability catalog entry: a grantable name and the tools it selects."""

    name: str
    description: str
    tool_names: tuple[str, ...]
    policy_only: bool = False


@dataclass(frozen=True)
class SurfaceProfile:
    """The contract between the core and one surface; every hook is optional."""

    name: str
    commands: tuple[type[Action], ...]
    renderer: Renderer
    tool_specs: tuple[ToolSpec, ...] = ()
    capability_catalog: Mapping[str, frozenset[tuple[type[Action], type | None]]] = field(
        default_factory=dict, hash=False
    )
    capability_tools: Mapping[str, tuple[str, ...]] = field(default_factory=dict, hash=False)
    extra_tools: tuple[ContextFactory, ...] = ()
    rails: tuple[ContextFactory, ...] = ()
    policy_rules: tuple[PolicyRule, ...] = ()
    timeouts: Mapping[Effect, float] = field(default_factory=dict, hash=False)
    messages: Mapping[str, str] = field(default_factory=dict, hash=False)
    settings: Any = field(default=None, hash=False)
    scene: Scene | None = None
    capabilities: tuple[Capability, ...] = ()
    custom_actions: CustomActions | None = None
    page_metadata: Metadata | None = None
    policy_probe: PolicyProbe | None = None
    activate: Activate | None = None
    semantic_tracker: Callable[[], Any] | None = None
    worker_prompt: WorkerPrompt | None = None
    page_state: Callable[[], Any] | None = None


@dataclass(frozen=True)
class SurfaceBundle:
    """What a surface factory returns: the surface settings and, once the surface has one, its profile."""

    settings: SurfaceSettings
    profile: SurfaceProfile | None = None
