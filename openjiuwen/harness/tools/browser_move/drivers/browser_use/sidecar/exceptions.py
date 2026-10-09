# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Sidecar-local exception classes.

STDLIB ONLY. The sidecar cannot import the ``openjiuwen`` package (that would
pull the main env's dependencies, transitively ``anthropic>=0.120.2``, back into
the sidecar closure -- exactly the conflict the sidecar exists to avoid).

These classes are named IDENTICALLY to the wire error codes in
``wire.ERROR_CODES``. ``main.py`` maps a raised exception to a wire error
code purely by ``type(exc).__name__``; in the agent-core process,
``transport.py`` raises the same classes for the codes it receives, and the
driver maps them to the contract's typed errors by name. ``wire.py`` is the
source of truth for the code strings; a unit test in the main env asserts
every code has a class here.
"""

from __future__ import annotations


class DriverError(Exception):
    """Generic fallback error."""


class DriverNotConnected(DriverError):
    """A method was invoked before the session was connected."""


class DriverConnectionError(DriverError):
    """Attaching to the browser over CDP failed."""


class DriverConnectionLost(DriverError):
    """The CDP connection dropped mid-flight."""


class ElementNotFound(DriverError):
    """The referenced element does not resolve to any current DOM node."""


class AmbiguousElement(DriverError):
    """The referenced element matches more than one current DOM node."""


class StaleIndexError(DriverError):
    """A selector-map index no longer identifies the same node."""


class StaleNodeError(DriverError):
    """A backend node id no longer resolves."""


class ActionTimeout(DriverError):
    """A hands action did not complete within its timeout."""


class NavigationError(DriverError):
    """A navigation action failed."""


class ObserveError(DriverError):
    """Capturing a page state failed."""


class EvaluateError(DriverError):
    """In-page JavaScript evaluation failed."""

    def __init__(self, message: str, *, js_message: str = "", js_stack: str = "") -> None:
        super().__init__(message)
        self.js_message = js_message
        self.js_stack = js_stack


class DriverUnsupported(DriverError):
    """The requested capability is not available."""


class ActionIntercepted(DriverError):
    """Another element would receive the input; ``by`` describes it. Raised before any input is sent."""

    def __init__(self, message: str, *, by: str = "") -> None:
        super().__init__(message)
        self.by = by


__all__ = [
    "ActionIntercepted",
    "ActionTimeout",
    "AmbiguousElement",
    "DriverConnectionError",
    "DriverConnectionLost",
    "DriverError",
    "DriverNotConnected",
    "DriverUnsupported",
    "ElementNotFound",
    "EvaluateError",
    "NavigationError",
    "ObserveError",
    "StaleIndexError",
    "StaleNodeError",
]
