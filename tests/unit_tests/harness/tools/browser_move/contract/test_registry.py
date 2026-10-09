# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``Registry`` resolves lazy import strings on first ``create`` and refuses duplicate or unknown names."""

from __future__ import annotations

import sys

import pytest

from openjiuwen.harness.tools.browser_move.contract import registry as registry_module
from openjiuwen.harness.tools.browser_move.contract.registry import (
    DRIVERS,
    PROVISIONERS,
    SURFACES,
    Registry,
    register_driver,
    register_provisioner,
    register_surface,
)

LAZY_MODULE = "tests.unit_tests.harness.tools.browser_move.contract._lazy_target"


def test_lazy_import_string_is_resolved_only_on_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, LAZY_MODULE, raising=False)
    registry: Registry[object] = Registry("thing")

    registry.register("lazy", f"{LAZY_MODULE}:make_thing")

    assert LAZY_MODULE not in sys.modules
    assert registry.names() == ("lazy",)
    assert registry.create("lazy", 1, key="v") == ("thing", (1,), {"key": "v"})
    assert LAZY_MODULE in sys.modules


def test_lazy_import_string_may_name_a_nested_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, LAZY_MODULE, raising=False)
    registry: Registry[object] = Registry("thing")
    registry.register("nested", f"{LAZY_MODULE}:Nested.factory")

    assert registry.create("nested") == "nested"


def test_callable_factory_is_called_with_the_given_arguments() -> None:
    registry: Registry[object] = Registry("thing")
    registry.register("direct", lambda device, settings: (device, settings))

    assert registry.create("direct", "dev", settings="s") == ("dev", "s")


def test_names_keep_registration_order() -> None:
    registry: Registry[object] = Registry("thing")
    for name in ("b", "a", "c"):
        registry.register(name, dict)

    assert registry.names() == ("b", "a", "c")


def test_duplicate_name_is_refused() -> None:
    registry: Registry[object] = Registry("driver")
    registry.register("x", dict)

    with pytest.raises(ValueError, match="driver 'x' is already registered"):
        registry.register("x", list)
    assert registry.create("x") == {}


def test_unknown_name_lists_the_registered_ones() -> None:
    registry: Registry[object] = Registry("surface")
    registry.register("known", dict)

    with pytest.raises(KeyError, match="unknown surface 'missing'; registered: known"):
        registry.create("missing")


@pytest.mark.parametrize("factory", ["no_colon", ":attr", "pkg.module:"])
def test_malformed_import_string_is_refused_at_register(factory: str) -> None:
    with pytest.raises(ValueError, match="pkg.module:attr"):
        Registry("driver").register("bad", factory)


def test_non_callable_factory_is_refused() -> None:
    with pytest.raises(TypeError, match="callable"):
        Registry("driver").register("bad", 42)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("registry", "kind"), [(DRIVERS, "driver"), (SURFACES, "surface"), (PROVISIONERS, "provisioner")]
)
def test_default_registries_name_their_kind(registry: Registry[object], kind: str) -> None:
    with pytest.raises(KeyError, match=f"unknown {kind} '__missing__'"):
        registry.create("__missing__")


@pytest.mark.parametrize(
    ("register", "attribute"),
    [(register_driver, "DRIVERS"), (register_surface, "SURFACES"), (register_provisioner, "PROVISIONERS")],
)
def test_register_functions_use_the_default_registries(
    monkeypatch: pytest.MonkeyPatch, register, attribute: str
) -> None:
    fresh: Registry[object] = Registry(attribute.lower())
    monkeypatch.setattr(registry_module, attribute, fresh)

    register("mine", f"{LAZY_MODULE}:make_thing")

    assert fresh.names() == ("mine",)
