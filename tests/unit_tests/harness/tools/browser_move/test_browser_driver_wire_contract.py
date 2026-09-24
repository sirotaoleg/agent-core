#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""B1 wire contract tests for BrowserDriver sidecar protocol."""

from __future__ import annotations

import inspect

from openjiuwen.harness.tools.browser_move.backends.contract import errors as driver_errors
from openjiuwen.harness.tools.browser_move.backends.contract.base import BrowserDriver, DriverRef, NodeRef
from openjiuwen.harness.tools.browser_move.backends.browser_use.driver import _ref_to_wire
from openjiuwen.harness.tools.browser_move.backends.browser_use.sidecar import wire


def _browser_driver_public_method_names() -> set[str]:
    names: set[str] = set()
    for name, member in inspect.getmembers(BrowserDriver):
        if name.startswith("_"):
            continue
        if callable(member):
            names.add(name)
    return names


def test_wire_method_names_match_protocol_plus_control() -> None:
    expected = _browser_driver_public_method_names() | set(wire.CONTROL_METHOD_NAMES)
    assert wire.WIRE_METHOD_NAMES == frozenset(expected)


def test_driver_method_names_are_subset_of_wire_methods_and_driver_ref_round_trips() -> None:
    """Wire-shape coverage for the two element-ref encodings that cross the
    process boundary: the driver method names must stay a subset of the
    control-augmented wire surface, and the new ``DriverRef`` kind (see
    ``backends/contract/base.py``) must round-trip through ``_ref_to_wire``
    and the sidecar's own minting convention exactly like the pre-existing
    ``NodeRef`` kind does.
    """
    assert set(wire.DRIVER_METHOD_NAMES) <= wire.WIRE_METHOD_NAMES

    from openjiuwen.harness.tools.browser_move.backends.browser_use.sidecar.session_adapter import (
        _mint_driver_ref,
        _parse_driver_ref_handle,
    )

    node_ref = NodeRef(backend_node_id=42, frame_id="frame-1")
    node_wire = _ref_to_wire(node_ref)
    assert node_wire == {"kind": "node", "backend_node_id": 42, "frame_id": "frame-1"}

    minted = _mint_driver_ref(backend_node_id=42, driver_generation=3)
    driver_ref = DriverRef(handle=minted["handle"], driver_generation=minted["driver_generation"])
    driver_wire = _ref_to_wire(driver_ref)
    assert driver_wire == {"kind": "driver", "handle": "bnid:42", "driver_generation": 3}
    assert _parse_driver_ref_handle(driver_wire["handle"]) == 42


def test_every_wire_error_code_maps_to_driver_error_subclass() -> None:
    for code in wire.ERROR_CODES:
        cls = getattr(driver_errors, code, None)
        assert cls is not None, f"missing errors.{code}"
        assert issubclass(cls, driver_errors.DriverError), f"errors.{code} is not a DriverError subclass"
