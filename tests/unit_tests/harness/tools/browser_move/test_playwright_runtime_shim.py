# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The deprecated ``playwright_runtime`` package is the ``runtime`` package under its old name."""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest

PKG = "openjiuwen.harness.tools.browser_move"
OLD = f"{PKG}.playwright_runtime"
NEW = f"{PKG}.runtime"


def _import_fresh() -> tuple[object, list[warnings.WarningMessage]]:
    for name in [n for n in sys.modules if n == OLD or n.startswith(OLD + ".")]:
        del sys.modules[name]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module(OLD)
    return module, _shim_warnings(caught)


def _shim_warnings(caught: list[warnings.WarningMessage]) -> list[warnings.WarningMessage]:
    return [w for w in caught if w.category is DeprecationWarning and OLD in str(w.message)]


@pytest.mark.parametrize(
    "suffix",
    ["session", "config", "assembly", "controllers", "controllers.action", "utils.env"],
)
def test_old_module_is_the_new_module(suffix: str) -> None:
    assert importlib.import_module(f"{OLD}.{suffix}") is importlib.import_module(f"{NEW}.{suffix}")


def test_every_module_under_runtime_is_aliased() -> None:
    shim = importlib.import_module(OLD)
    new_modules = {n for n in sys.modules if n.startswith(NEW + ".")}
    aliased = {n.replace(OLD, NEW, 1) for n in sys.modules if n.startswith(OLD + ".")}
    assert aliased - {f"{NEW}.{old}" for old in shim._MOVED} == new_modules
    assert {f"{NEW}.controllers.action", f"{NEW}.utils.parsing", f"{NEW}.main"} <= aliased


@pytest.mark.parametrize(
    "suffix, target",
    [
        ("browser_capabilities", "surfaces.browser.capabilities"),
        ("profiles", "provisioning.profiles"),
        ("runtime", "runtime.session"),
        ("probes", "surfaces.browser.probes"),
        ("probe_semantics", "surfaces.browser.probes.semantics"),
        ("semantic_state", "surfaces.browser.semantic.state"),
        ("browser_working_context", "surfaces.browser.working_context.store"),
        ("browser_working_context_processor", "surfaces.browser.working_context.processor"),
        ("browser_working_context_rail", "surfaces.browser.rails.working_context"),
        ("browser_state_context_processor", "surfaces.browser.state_processor"),
        ("site_profiles", "surfaces.browser.site_profiles"),
        ("status_logging", "surfaces.browser.status_logging"),
    ],
)
def test_moved_module_keeps_its_old_name(suffix: str, target: str) -> None:
    assert importlib.import_module(f"{OLD}.{suffix}") is importlib.import_module(f"{PKG}.{target}")


def test_the_old_probes_module_still_offers_its_script_builders() -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
        build_browser_state_metadata_js,
        build_card_probe_js,
        build_interactive_probe_js,
    )

    assert all(callable(f) for f in (build_browser_state_metadata_js, build_card_probe_js, build_interactive_probe_js))


def test_moved_names_are_served() -> None:
    old = importlib.import_module(OLD)
    rail = importlib.import_module(f"{PKG}.surfaces.browser.rails.working_context")
    processor = importlib.import_module(f"{PKG}.surfaces.browser.state_processor")
    assert old.BrowserWorkingContextRail is rail.BrowserWorkingContextRail
    assert old.BrowserStateContextProcessorConfig is processor.BrowserStateContextProcessorConfig
    assert "BrowserWorkingContextRail" not in old.__all__


def test_classes_are_identical() -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime as old_runtime

    from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime as new_runtime

    assert old_runtime is new_runtime


def test_reexports_runtime_all() -> None:
    old = importlib.import_module(OLD)
    new = importlib.import_module(NEW)
    assert old.__all__ == new.__all__
    for name in new.__all__:
        assert getattr(old, name) is getattr(new, name), name
    with pytest.raises(AttributeError):
        old.no_such_name


def test_warns_once_per_process() -> None:
    module, first = _import_fresh()
    assert len(first) == 1
    assert NEW in str(first[0].message)
    with warnings.catch_warnings(record=True) as again:
        warnings.simplefilter("always")
        assert importlib.import_module(OLD) is module
        importlib.reload(module)
    assert _shim_warnings(again) == []


def test_reload_keeps_the_aliases() -> None:
    module = importlib.import_module(OLD)
    importlib.reload(module)
    assert importlib.import_module(f"{OLD}.runtime") is importlib.import_module(f"{NEW}.session")
    assert importlib.import_module(f"{OLD}.utils.env") is importlib.import_module(f"{NEW}.utils.env")
