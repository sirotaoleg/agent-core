# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The driver interface, its self-description and the handler-based base class."""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar

from .actions import Action, Observe
from .errors import Unsupported
from .observation import Observation
from .refs import REF_KINDS
from .results import ActionResult


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class DriverInfo:
    """What a driver is and what it can do.

    ``actions`` maps each handled command type to the element ref kinds it accepts (empty for commands without
    refs). ``observes`` names the ``Observation`` fields the driver fills (for example ``elements``, ``screenshot``).
    ``tool_hints`` are optional per-command descriptions; the surface profile's own texts take priority.
    """

    name: str
    surfaces: frozenset[str]
    versions: Mapping[str, str] = field(default_factory=dict, hash=False)
    actions: Mapping[type[Action], frozenset[type]] = field(default_factory=dict, hash=False)
    observes: frozenset[str] = frozenset()
    tool_hints: Mapping[type[Action], str] = field(default_factory=dict, hash=False)


class Driver(ABC):
    """Runs commands on one screen. ``config`` of ``start`` is the driver's own type, built by its factory."""

    @property
    @abstractmethod
    def info(self) -> DriverInfo: ...

    @abstractmethod
    async def start(self, config: Any = None) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def health(self) -> Health: ...

    @abstractmethod
    async def observe(self, command: Observe) -> Observation: ...

    @abstractmethod
    async def execute(self, action: Action) -> ActionResult: ...


_HANDLES_MARK = "__contract_handles__"
_F = TypeVar("_F", bound=Callable[..., Any])


def handles(command: type[Action], *, refs: Iterable[type] = ()) -> Callable[[_F], _F]:
    """Mark a ``HandlerDriver`` method as the handler of ``command`` for the given element ref kinds.

    Marks stack, so one method may handle several commands.
    """
    accepted = frozenset(refs)
    unknown = accepted - set(REF_KINDS)
    if unknown:
        raise TypeError(f"not element ref kinds: {sorted(kind.__name__ for kind in unknown)}")

    def mark(method: _F) -> _F:
        setattr(method, _HANDLES_MARK, getattr(method, _HANDLES_MARK, ()) + ((command, accepted),))
        return method

    return mark


def iter_refs(value: Any) -> Iterator[Any]:
    """Yield every element ref inside a command, looking into nested dataclasses, tuples and lists."""
    if isinstance(value, REF_KINDS):
        yield value
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for item in dataclasses.fields(value):
            yield from iter_refs(getattr(value, item.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from iter_refs(item)


def ensure_supported(driver_name: str, actions: Mapping[type[Action], frozenset[type]], action: Action) -> None:
    """Raise ``Unsupported(dispatched=False)`` unless ``actions`` covers the command and every ref kind in it."""
    command = type(action)
    accepted = actions.get(command)
    if accepted is None:
        raise Unsupported(f"{driver_name} does not handle {command.__name__}", dispatched=False)
    for ref in iter_refs(action):
        if type(ref) not in accepted:
            raise Unsupported(
                f"{driver_name} does not accept {type(ref).__name__} for {command.__name__}", dispatched=False
            )


class HandlerDriver(Driver):
    """A driver whose ``execute`` dispatches by ``type(action)`` to methods marked with ``@handles``.

    Subclasses describe themselves in ``describe()``; ``info.actions`` is always computed from the marks, so the
    supported commands cannot drift from the code. Command classes may come from any package.
    """

    _handlers: ClassVar[Mapping[type[Action], tuple[str, frozenset[type]]]] = MappingProxyType({})

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        handlers: dict[type[Action], tuple[str, frozenset[type]]] = {}
        for klass in reversed(cls.__mro__):
            for name, attribute in vars(klass).items():
                for command, refs in getattr(attribute, _HANDLES_MARK, ()):
                    handlers[command] = (name, refs)
        cls._handlers = MappingProxyType(handlers)

    @classmethod
    def handled_actions(cls) -> Mapping[type[Action], frozenset[type]]:
        return MappingProxyType({command: refs for command, (_name, refs) in cls._handlers.items()})

    @abstractmethod
    def describe(self) -> DriverInfo:
        """Everything but ``actions``, which ``info`` fills from the marks (a value set here is replaced)."""

    @property
    def info(self) -> DriverInfo:
        return dataclasses.replace(self.describe(), actions=self.handled_actions())

    async def execute(self, action: Action) -> ActionResult:
        ensure_supported(type(self).__name__, self.handled_actions(), action)
        name, _refs = self._handlers[type(action)]
        return await getattr(self, name)(action)
