# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level ownership registry for service resources."""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrowserServiceIdentity:
    """Stable process-level identity for one logical service instance."""

    browser_key: str
    profile_name: str
    driver_mode: str
    display_mode: str
    managed_args: tuple[str, ...]
    browser_binary: str
    user_data_dir: str
    cdp_endpoint: str
    server_id: str


@dataclass(frozen=True)
class BrowserServiceResetSnapshot:
    """Resources that must be stopped for an explicit identity reset."""

    services: tuple[Any, ...]
    managed_drivers: tuple[Any, ...]


@dataclass
class _BrowserServiceEntry:
    services: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    binding_services: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    managed_drivers: list[Any] = field(default_factory=list)


class BrowserServiceRegistry:
    """Coordinate process resources without sharing task/model state.

    BrowserService objects remain task-scoped. Registry entries only coordinate
    the task bindings and the managed Chrome process handles of the services
    that address the same lifecycle identity.
    """

    def __init__(self) -> None:
        self._entries: dict[BrowserServiceIdentity, _BrowserServiceEntry] = {}
        self._lock = threading.RLock()

    def acquire(self, identity: BrowserServiceIdentity, service: Any) -> None:
        with self._lock:
            entry = self._entries.setdefault(identity, _BrowserServiceEntry())
            entry.services.add(service)

    def activate_binding(self, identity: BrowserServiceIdentity, service: Any) -> None:
        """Mark the service's task binding active."""
        with self._lock:
            entry = self._entries.setdefault(identity, _BrowserServiceEntry())
            entry.services.add(service)
            entry.binding_services.add(service)

    def release(
        self,
        identity: BrowserServiceIdentity,
        service: Any,
    ) -> None:
        """Release a task binding."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            entry.binding_services.discard(service)
            entry.services.discard(service)
            # Chrome intentionally outlives the last task binding. Retain
            # only its process handle so an explicit identity-scoped
            # configuration restart can stop it after task collection.
            if not entry.services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def register_managed_driver(
        self,
        identity: BrowserServiceIdentity,
        service: Any,
        driver: Any,
    ) -> None:
        with self._lock:
            entry = self._entries.setdefault(identity, _BrowserServiceEntry())
            entry.services.add(service)
            if getattr(driver, "owns_process", False):
                # A new owner means prior handles for this exact identity are
                # stale (for example, the user closed the previous window).
                entry.managed_drivers = [driver]
                return
            if any(
                getattr(existing, "owns_process", False)
                for existing in entry.managed_drivers
            ):
                return
            if all(existing is not driver for existing in entry.managed_drivers):
                entry.managed_drivers.append(driver)

    def unregister_managed_driver(
        self,
        identity: BrowserServiceIdentity,
        driver: Any,
    ) -> None:
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            entry.managed_drivers = [
                existing
                for existing in entry.managed_drivers
                if existing is not driver
            ]
            if not entry.services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def begin_reset(
        self,
        identity: BrowserServiceIdentity,
    ) -> BrowserServiceResetSnapshot:
        """Detach shared process resources for one explicit reset operation."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return BrowserServiceResetSnapshot(services=(), managed_drivers=())

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

            return BrowserServiceResetSnapshot(
                services=services,
                managed_drivers=tuple(drivers),
            )

    def discard_idle(self, identity: BrowserServiceIdentity) -> None:
        """Discard registry metadata after an explicit full shutdown."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            if not entry.services and not entry.binding_services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def find_managed_driver_identities(
        self,
        *,
        browser_key: str,
        profile_name: str,
        display_mode: str,
        browser_binary: str,
    ) -> tuple[BrowserServiceIdentity, ...]:
        """Find preserved drivers for one exact logical identity."""
        with self._lock:
            matches: list[BrowserServiceIdentity] = []
            for identity, entry in self._entries.items():
                if not entry.managed_drivers:
                    continue
                if not isinstance(identity, BrowserServiceIdentity):
                    continue
                if identity.browser_key != browser_key:
                    continue
                if identity.profile_name != profile_name:
                    continue
                if identity.driver_mode != "managed":
                    continue
                if identity.display_mode != display_mode:
                    continue
                if identity.browser_binary != browser_binary:
                    continue
                matches.append(identity)
            return tuple(matches)

    def clear(self) -> None:
        """Clear metadata for isolated tests after their resources are stopped."""
        with self._lock:
            self._entries.clear()

BROWSER_SERVICE_REGISTRY = BrowserServiceRegistry()


__all__ = [
    "BROWSER_SERVICE_REGISTRY",
    "BrowserServiceIdentity",
    "BrowserServiceRegistry",
    "BrowserServiceResetSnapshot",
]
