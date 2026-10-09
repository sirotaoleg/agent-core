#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wire contract tests for the browser-use sidecar protocol."""

from __future__ import annotations

import inspect

from openjiuwen.harness.tools.browser_move.contract import DriverRef
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import _ref_to_wire
from openjiuwen.harness.tools.browser_move.drivers.browser_use.sidecar import exceptions as driver_errors
from openjiuwen.harness.tools.browser_move.drivers.browser_use.sidecar import wire


def _adapter_public_method_names() -> set[str]:
    from openjiuwen.harness.tools.browser_move.drivers.browser_use.sidecar.session_adapter import SessionAdapter

    names: set[str] = set()
    for name, member in inspect.getmembers(SessionAdapter):
        if name.startswith("_"):
            continue
        if callable(member):
            names.add(name)
    return names


def test_wire_method_names_match_the_sidecar_adapter_plus_control() -> None:
    expected = _adapter_public_method_names() | set(wire.CONTROL_METHOD_NAMES)
    assert wire.WIRE_METHOD_NAMES == frozenset(expected)


def test_driver_method_names_are_subset_of_wire_methods_and_driver_ref_round_trips() -> None:
    """Wire-shape coverage for the element ref that crosses the process
    boundary: the driver method names must stay a subset of the
    control-augmented wire surface, and the contract's ``DriverRef`` must
    round-trip through ``_ref_to_wire`` and the sidecar's own minting
    convention.
    """
    assert set(wire.DRIVER_METHOD_NAMES) <= wire.WIRE_METHOD_NAMES

    from openjiuwen.harness.tools.browser_move.drivers.browser_use.sidecar.session_adapter import (
        _mint_driver_ref,
        _parse_driver_ref_handle,
    )

    minted = _mint_driver_ref(backend_node_id=42, driver_generation=3)
    driver_ref = DriverRef(ref=minted["handle"], generation="g1")
    driver_wire = _ref_to_wire(driver_ref, minted["driver_generation"])
    assert driver_wire == {"kind": "driver", "handle": "bnid:42", "driver_generation": 3}
    assert _parse_driver_ref_handle(driver_wire["handle"]) == 42


def test_every_wire_error_code_maps_to_driver_error_subclass() -> None:
    for code in wire.ERROR_CODES:
        cls = getattr(driver_errors, code, None)
        assert cls is not None, f"missing errors.{code}"
        assert issubclass(cls, driver_errors.DriverError), f"errors.{code} is not a DriverError subclass"
