# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Driver errors carry ``dispatched``; an unknown outcome counts as dispatched."""

from __future__ import annotations

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    ActionIntercepted,
    ActionTimeout,
    AmbiguousElement,
    ConnectionLost,
    DriverError,
    ElementNotFound,
    NavigationError,
    PolicyDenied,
    StaleRef,
    Unsupported,
)

DEFAULT_DISPATCHED = {
    DriverError: True,
    StaleRef: False,
    ElementNotFound: True,
    AmbiguousElement: True,
    ActionTimeout: True,
    NavigationError: True,
    ConnectionLost: True,
    Unsupported: False,
    PolicyDenied: True,
}


@pytest.mark.parametrize("error_type", list(DEFAULT_DISPATCHED), ids=lambda t: t.__name__)
def test_dispatched_default(error_type: type[DriverError]) -> None:
    error = error_type("boom")

    assert isinstance(error, DriverError)
    assert error.dispatched is DEFAULT_DISPATCHED[error_type]
    assert str(error) == "boom"


@pytest.mark.parametrize("error_type", list(DEFAULT_DISPATCHED), ids=lambda t: t.__name__)
@pytest.mark.parametrize("dispatched", [True, False])
def test_dispatched_can_be_set_explicitly(error_type: type[DriverError], dispatched: bool) -> None:
    assert error_type("boom", dispatched=dispatched).dispatched is dispatched


def test_action_intercepted_names_the_blocker() -> None:
    error = ActionIntercepted("click not delivered", by="<div class=overlay>")

    assert error.by == "<div class=overlay>"
    assert error.dispatched is True
    assert ActionIntercepted(by="x", dispatched=False).dispatched is False
