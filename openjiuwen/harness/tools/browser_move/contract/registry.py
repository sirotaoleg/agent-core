# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Name -> factory registries; a factory is a callable or a lazy ``"pkg.module:attr"`` import string."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from .driver import Driver
from .surface import SurfaceProfile

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._entries: dict[str, Callable[..., T] | str] = {}

    def register(self, name: str, factory: Callable[..., T] | str) -> None:
        if name in self._entries:
            raise ValueError(f"{self._kind} {name!r} is already registered")
        if isinstance(factory, str):
            module, _, attribute = factory.partition(":")
            if not module or not attribute:
                raise ValueError(f"lazy {self._kind} factory must look like 'pkg.module:attr', got {factory!r}")
        elif not callable(factory):
            raise TypeError(f"{self._kind} factory for {name!r} must be callable or an import string")
        self._entries[name] = factory

    def create(self, name: str, *args: Any, **kwargs: Any) -> T:
        return self._factory(name)(*args, **kwargs)

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def _factory(self, name: str) -> Callable[..., T]:
        try:
            factory = self._entries[name]
        except KeyError:
            known = ", ".join(self._entries) or "none"
            raise KeyError(f"unknown {self._kind} {name!r}; registered: {known}") from None
        if isinstance(factory, str):
            module, _, attribute = factory.partition(":")
            resolved: Any = importlib.import_module(module)
            for part in attribute.split("."):
                resolved = getattr(resolved, part)
            factory = self._entries[name] = resolved
        return factory


# The only module-level state of the package; everything else is passed in by the composition root.
DRIVERS: Registry[Driver] = Registry("driver")
SURFACES: Registry[SurfaceProfile] = Registry("surface")
PROVISIONERS: Registry[Any] = Registry("provisioner")


def register_driver(name: str, factory: Callable[..., Driver] | str) -> None:
    """``factory(device, surface_settings) -> Driver``; the driver builds its own config."""
    DRIVERS.register(name, factory)


def register_surface(name: str, profile_factory: Callable[..., SurfaceProfile] | str) -> None:
    SURFACES.register(name, profile_factory)


def register_provisioner(name: str, factory: Callable[..., Any] | str) -> None:
    PROVISIONERS.register(name, factory)
