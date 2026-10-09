# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed driver errors; ``dispatched`` (also when the outcome is unknown) means never retried automatically."""

from __future__ import annotations

from typing import ClassVar

from .observation import Observation


class DriverError(Exception):
    """Base driver error. ``dispatched`` defaults to the class's ``default_dispatched`` (True unless stated)."""

    default_dispatched: ClassVar[bool] = True

    def __init__(self, message: str = "", *, dispatched: bool | None = None) -> None:
        super().__init__(message)
        self.dispatched: bool = type(self).default_dispatched if dispatched is None else dispatched


class StaleRef(DriverError):
    """The ref belongs to an older generation. Raised only before anything is sent; may carry a fresh observation."""

    default_dispatched: ClassVar[bool] = False

    def __init__(
        self, message: str = "", *, dispatched: bool | None = None, observation: Observation | None = None
    ) -> None:
        super().__init__(message, dispatched=dispatched)
        self.observation = observation


class ElementNotFound(DriverError):
    pass


class AmbiguousElement(DriverError):
    pass


class ActionIntercepted(DriverError):
    """Another element would receive the input; ``by`` describes it."""

    default_dispatched: ClassVar[bool] = False

    def __init__(self, message: str = "", *, by: str, dispatched: bool | None = None) -> None:
        super().__init__(message, dispatched=dispatched)
        self.by = by


class ActionTimeout(DriverError):
    pass


class NavigationError(DriverError):
    pass


class ConnectionLost(DriverError):
    pass


class Unsupported(DriverError):
    """The driver has no handler for the command, or the handler does not accept the ref kind."""

    default_dispatched: ClassVar[bool] = False


class PolicyDenied(DriverError):
    """Raised by the security rail, not by drivers, before anything is sent."""

    default_dispatched: ClassVar[bool] = False
