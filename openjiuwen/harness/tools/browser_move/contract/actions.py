# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Commands that make sense on any screen; a surface adds its own ``Action`` subclasses in its package."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from .refs import ContextRef, Ref


class Effect(str, Enum):
    """What a command may do; middleware picks timeouts and retry rules by it."""

    READ = "read"
    ACT = "act"
    NAVIGATE = "navigate"
    SCRIPT = "script"


@dataclass(frozen=True)
class Action:
    """Base of every command. Subclasses set ``effect``, and ``__sensitive_fields__`` to mask when ``sensitive``."""

    effect: ClassVar[Effect]


@dataclass(frozen=True)
class Observe(Action):
    """A snapshot of the focused context; ``depth`` or ``root`` scope it to a subtree, a partial observation."""

    effect: ClassVar[Effect] = Effect.READ

    include_screenshot: bool = False
    full_page: bool = False
    depth: int | None = None
    root: Ref | None = None

    @property
    def scoped(self) -> bool:
        return self.depth is not None or self.root is not None


@dataclass(frozen=True)
class Click(Action):
    effect: ClassVar[Effect] = Effect.ACT

    target: Ref
    button: str = "left"
    modifiers: tuple[str, ...] = ()
    count: int = 1


@dataclass(frozen=True)
class TypeText(Action):
    """Type ``text`` into ``target`` (or the focused element when ``None``)."""

    effect: ClassVar[Effect] = Effect.ACT
    __sensitive_fields__: ClassVar[tuple[str, ...]] = ("text",)

    target: Ref | None
    text: str
    submit: bool = False
    sensitive: bool = False
    clear: bool = True
    slowly: bool = False


@dataclass(frozen=True)
class PressKey(Action):
    effect: ClassVar[Effect] = Effect.ACT

    key: str
    target: Ref | None = None


@dataclass(frozen=True)
class Scroll(Action):
    """Scroll ``target`` (or the whole view when ``None``) by ``dx``/``dy`` pixels."""

    effect: ClassVar[Effect] = Effect.ACT

    target: Ref | None = None
    dx: int = 0
    dy: int = 0


@dataclass(frozen=True)
class Hover(Action):
    effect: ClassVar[Effect] = Effect.ACT

    target: Ref


@dataclass(frozen=True)
class Drag(Action):
    effect: ClassVar[Effect] = Effect.ACT

    source: Ref
    destination: Ref


@dataclass(frozen=True)
class Wait(Action):
    """Sleep ``seconds``, or wait at most ``seconds`` (``None``: driver default) for ``until_text``."""

    effect: ClassVar[Effect] = Effect.READ

    seconds: float | None = None
    until_text: str | None = None

    def __post_init__(self) -> None:
        if self.seconds is None and self.until_text is None:
            raise ValueError("Wait needs seconds, until_text or both")


@dataclass(frozen=True)
class SwitchContext(Action):
    effect: ClassVar[Effect] = Effect.NAVIGATE

    context: ContextRef


@dataclass(frozen=True)
class CloseContext(Action):
    effect: ClassVar[Effect] = Effect.NAVIGATE

    context: ContextRef


@dataclass(frozen=True)
class HandleDialog(Action):
    effect: ClassVar[Effect] = Effect.ACT

    accept: bool
    prompt_text: str | None = None


@dataclass(frozen=True)
class RunScript(Action):
    """Run ``code`` in the focused context, scoped to ``target`` if set. ``sensitive`` masks ``code`` and ``args``."""

    effect: ClassVar[Effect] = Effect.SCRIPT
    __sensitive_fields__: ClassVar[tuple[str, ...]] = ("code", "args")

    code: str
    args: Any = field(default=None, hash=False)
    sensitive: bool = False
    target: Ref | None = None
