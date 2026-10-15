# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level ownership registry for service resources, keyed by the service's identity string."""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceResetSnapshot:
    """Resources that must be stopped for an explicit identity reset."""

    services: tuple[Any, ...]
    managed_drivers: tuple[Any, ...]


@dataclass
class _ServiceEntry:
    services: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    binding_services: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    managed_drivers: list[Any] = field(default_factory=list)


class ServiceRegistry:
    """Shares device handles and task bindings between the services of one identity."""

    def __init__(self) -> None:
        self._entries: dict[str, _ServiceEntry] = {}
        self._lock = threading.RLock()

    def acquire(self, identity: str, service: Any) -> None:
        with self._lock:
            entry = self._entries.setdefault(identity, _ServiceEntry())
            entry.services.add(service)

    def activate_binding(self, identity: str, service: Any) -> None:
        """Mark the service's task binding active."""
        with self._lock:
            entry = self._entries.setdefault(identity, _ServiceEntry())
            entry.services.add(service)
            entry.binding_services.add(service)

    def release(self, identity: str, service: Any) -> None:
        """Release a task binding."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            entry.binding_services.discard(service)
            entry.services.discard(service)
            if not entry.services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def register_managed_driver(self, identity: str, service: Any, driver: Any) -> None:
        with self._lock:
            entry = self._entries.setdefault(identity, _ServiceEntry())
            entry.services.add(service)
            if getattr(driver, "owns_process", False):
                entry.managed_drivers = [driver]
                return
            if any(getattr(existing, "owns_process", False) for existing in entry.managed_drivers):
                return
            if all(existing is not driver for existing in entry.managed_drivers):
                entry.managed_drivers.append(driver)

    def unregister_managed_driver(self, identity: str, driver: Any) -> None:
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            entry.managed_drivers = [existing for existing in entry.managed_drivers if existing is not driver]
            if not entry.services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def begin_reset(self, identity: str) -> ServiceResetSnapshot:
        """Detach shared process resources for one explicit reset operation."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return ServiceResetSnapshot(services=(), managed_drivers=())

            services = tuple(entry.services)
            drivers = list(entry.managed_drivers)
            for service in services:
                driver = getattr(service, "managed_driver", None)
                if driver is not None and all(existing is not driver for existing in drivers):
                    drivers.append(driver)

            entry.binding_services.clear()
            entry.managed_drivers.clear()
            if not entry.services:
                self._entries.pop(identity, None)

            return ServiceResetSnapshot(services=services, managed_drivers=tuple(drivers))

    def discard_idle(self, identity: str) -> None:
        """Discard registry metadata after an explicit full shutdown."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            if not entry.services and not entry.binding_services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def managed_driver_identities(self) -> tuple[str, ...]:
        """The identities that hold a preserved managed driver."""
        with self._lock:
            return tuple(identity for identity, entry in self._entries.items() if entry.managed_drivers)

    def clear(self) -> None:
        """Clear metadata for isolated tests after their resources are stopped."""
        with self._lock:
            self._entries.clear()


SERVICE_REGISTRY = ServiceRegistry()


__all__ = ["SERVICE_REGISTRY", "ServiceRegistry", "ServiceResetSnapshot"]
