#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Import boundaries between the contract, drivers, provisioning, surfaces and the core."""

from __future__ import annotations

import ast
import sys
from collections import Counter

from . import _scan

REGISTRY_IMPORTERS: dict[str, frozenset[str]] = {
    "drivers": frozenset({"runtime/assembly.py"}),
    "provisioning": frozenset({"runtime/assembly.py"}),
    "surfaces": frozenset({"runtime/assembly.py", "runtime/config.py"}),
}

KNOWN_CORE_IMPORTS_OF_CONCRETE_MODULES: dict[tuple[str, str], int] = {
    ("runtime/agents.py", "surfaces.browser.runner.logging"): 1,
    ("runtime/session.py", "surfaces.browser.runner.actions"): 2,
    ("runtime/session.py", "surfaces.browser.runner.logging"): 1,
    ("runtime/session.py", "surfaces.browser.runner.settings"): 2,
    ("runtime/service.py", "surfaces.browser.runner.settings"): 2,
    ("runtime/session.py", "surfaces.browser.runner.tools"): 2,
}

KNOWN_CORE_DETOURS_INTO_CONCRETE_MODULES: dict[tuple[str, str], int] = {
    ("runtime/topology.py", "openjiuwen.harness.subagents.browser_agent"): 1,
}

KNOWN_CORE_TO_HARNESS_IMPORTS: dict[tuple[str, str], int] = {
    ("context_engine/context/session_memory_manager.py", "openjiuwen.harness.tools.filesystem"): 2,
    ("context_engine/processor/forked/compressor/session_memory_agent.py", "openjiuwen.harness.tools.filesystem"): 1,
    ("memory/lite/coding_memory_tool_ops.py", "openjiuwen.harness.workspace.workspace"): 1,
    ("memory/lite/coding_memory_tools.py", "openjiuwen.harness.workspace.workspace"): 1,
    ("memory/lite/internal.py", "openjiuwen.harness.workspace.workspace"): 1,
    ("memory/lite/manager.py", "openjiuwen.harness.workspace.workspace"): 1,
    ("memory/lite/memory_tool_context_base.py", "openjiuwen.harness.workspace.workspace"): 1,
    ("memory/lite/memory_tool_ops.py", "openjiuwen.harness.workspace.workspace"): 1,
    ("memory/lite/memory_tools.py", "openjiuwen.harness.workspace.workspace"): 1,
}

KNOWN_DRIVER_IMPORTS_OUTSIDE_THEIR_LAYER: dict[tuple[str, str], int] = {}
DRIVER_LAYER_IMPORTERS = frozenset({"runtime/assembly.py"})
LAZY_DRIVER_LAYER_IMPORTERS = _scan.SETTINGS_FACADE_MODULES
ROOT_MODULES = frozenset({"__init__.py", "playwright_runtime_mcp_server.py"})
SURFACE_MODULES_A_DRIVER_MAY_IMPORT = frozenset({"commands", "context"})

_STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}
_CONTRACT_TESTING_EXTRA = frozenset({"pytest"})


def _concrete_kind(parts: tuple[str, ...]) -> str | None:
    """Classify a package module path as a concrete driver, surface or provisioner."""
    if len(parts) < 2:
        return None
    head, name = parts[0], parts[1]
    if head == "drivers" and name != "middleware":
        return "driver"
    if head == "surfaces":
        return "surface"
    if head == "provisioning":
        return "provisioning"
    return None


def _import_targets(relpath: str, node: ast.stmt) -> list[tuple[str, ...]]:
    """Package-relative module paths an import statement reaches (outside ones dropped)."""
    if isinstance(node, ast.Import):
        targets = [_scan.resolve_import(relpath, alias.name, 0) for alias in node.names]
        return [t for t in targets if t is not None]
    base = _scan.resolve_import(relpath, node.module, node.level)
    if base is None:
        return []
    targets = []
    for alias in node.names:
        sub = base + (alias.name,)
        targets.append(sub if alias.name != "*" and _scan.is_package_module(sub) else base)
    return targets


def _collect_core_concrete_imports() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(_scan.is_core):
        lazy_allowed = relpath in _scan.ASSEMBLY_MODULES | _scan.SETTINGS_FACADE_MODULES
        for node, lazy in _scan.iter_imports(_scan.parse(relpath)):
            for target in _import_targets(relpath, node):
                if _concrete_kind(target) and not (lazy_allowed and lazy):
                    found[(relpath, ".".join(target))] += 1
    return found


def _collect_core_imports_of_registries() -> list[str]:
    bad = []
    for relpath in _scan.files_where(_scan.is_core):
        for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
            for target in _import_targets(relpath, node):
                allowed = REGISTRY_IMPORTERS.get(target[0]) if len(target) == 1 else None
                if allowed is not None and relpath not in allowed:
                    bad.append(f"{relpath}:{node.lineno} imports the {target[0]} package")
    return bad


def _collect_surface_imports_of_drivers_or_provisioners() -> list[str]:
    bad = []
    for relpath in _scan.files_where(_scan.is_surface):
        for node, lazy in _scan.iter_imports(_scan.parse(relpath)):
            for target in _import_targets(relpath, node):
                if lazy and relpath in _scan.SETTINGS_FACADE_MODULES:
                    continue
                if _concrete_kind(target) in ("driver", "provisioning"):
                    bad.append(f"{relpath}:{node.lineno} imports {'.'.join(target)}")
    return bad


def _collect_contract_imports() -> list[str]:
    bad = []
    for relpath in _scan.files_where(_scan.is_contract):
        for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
            if isinstance(node, ast.ImportFrom) and node.level:
                target = _scan.resolve_import(relpath, node.module, node.level)
                if target is not None and target[:1] == (_scan.CONTRACT_FOLDER,):
                    continue
                bad.append(f"{relpath}:{node.lineno} relative import outside contract/")
                continue
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                target = _scan.resolve_import(relpath, name, 0)
                if target is not None and target[:1] == (_scan.CONTRACT_FOLDER,):
                    continue
                allowed = _STDLIB | (
                    _CONTRACT_TESTING_EXTRA if relpath.startswith("contract/testing/") else frozenset()
                )
                if name.split(".")[0] not in allowed or target is not None:
                    bad.append(f"{relpath}:{node.lineno} imports {name}")
    return bad


def _collect_core_to_harness_imports() -> Counter:
    found: Counter = Counter()
    for path in sorted(_scan.CORE_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "harness" not in text:
            continue
        relpath = path.relative_to(_scan.CORE_DIR).as_posix()
        package = ("openjiuwen", "core") + tuple(path.relative_to(_scan.CORE_DIR).parent.parts)
        for node, _lazy in _scan.iter_imports(_scan.parse_source(text, str(path))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif node.level:
                base = package[: len(package) - (node.level - 1)]
                modules = [".".join(base + ((node.module,) if node.module else ()))]
            else:
                modules = [node.module or ""]
            for module in modules:
                if module == "openjiuwen.harness" or module.startswith("openjiuwen.harness."):
                    found[(relpath, module)] += 1
    return found


def _outside_modules(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module or ""] if not node.level else []


def _imports_harness_outside_core(module: str) -> bool:
    return module.startswith("openjiuwen.") and not module.startswith(("openjiuwen.core.", _scan.PKG_DOTTED))


def _driver_may_import(driver: str, target: tuple[str, ...]) -> bool:
    if target[:1] == (_scan.CONTRACT_FOLDER,) or target[:2] == ("drivers", driver):
        return True
    if target == ("drivers", "middleware"):
        return True
    return (
        target[:1] == (_scan.SURFACES_FOLDER,) and len(target) >= 3 and target[2] in SURFACE_MODULES_A_DRIVER_MAY_IMPORT
    )


def _collect_driver_imports_outside_their_layer() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(lambda f: f.startswith("drivers/") and f.count("/") >= 2):
        driver = relpath.split("/")[1]
        for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
            for target in _import_targets(relpath, node):
                if not _driver_may_import(driver, target):
                    found[(relpath, ".".join(target))] += 1
            for module in _outside_modules(node):
                if _imports_harness_outside_core(module):
                    found[(relpath, module)] += 1
    return found


def _collect_provisioning_imports_outside_the_contract() -> list[str]:
    bad = []
    for relpath in _scan.files_where(lambda f: _scan.in_folder(f, "provisioning")):
        for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
            for target in _import_targets(relpath, node):
                if target[:1] not in ((_scan.CONTRACT_FOLDER,), ("provisioning",)):
                    bad.append(f"{relpath}:{node.lineno} imports {'.'.join(target)}")
            for module in _outside_modules(node):
                if _imports_harness_outside_core(module):
                    bad.append(f"{relpath}:{node.lineno} imports {module}")
    return bad


def _absolute_modules(node: ast.stmt, package: tuple[str, ...]) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not node.level:
        return [node.module or ""]
    base = package[: len(package) - (node.level - 1)]
    return [".".join(base + ((node.module,) if node.module else ()))]


def _concrete_modules_imported_by(module: str) -> list[str]:
    """Concrete driver, surface or provisioner modules that a module outside the package imports itself."""
    path = _scan.REPO_ROOT.joinpath(*module.split("."))
    source = path.with_suffix(".py") if path.with_suffix(".py").is_file() else path / "__init__.py"
    if not source.is_file():
        return []
    package = tuple(module.split(".")) if source.name == "__init__.py" else tuple(module.split(".")[:-1])
    reached = []
    for node, _lazy in _scan.iter_imports(_scan.parse_source(source.read_text(encoding="utf-8"), str(source))):
        for name in _absolute_modules(node, package):
            target = _scan.resolve_import("", name, 0)
            if target is not None and _concrete_kind(target):
                reached.append(".".join(target))
    return reached


def _collect_core_detours_into_concrete_modules() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(_scan.is_core):
        for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
            for module in _outside_modules(node):
                if module.startswith("openjiuwen.") and _scan.resolve_import(relpath, module, 0) is None:
                    if _concrete_modules_imported_by(module):
                        found[(relpath, module)] += 1
    return found


def _collect_imports_of_the_driver_layer() -> list[str]:
    bad = []
    for relpath in _scan.files_where(lambda f: _scan.in_folder(f, "runtime") or _scan.is_surface(f)):
        if relpath in DRIVER_LAYER_IMPORTERS:
            continue
        for node, lazy in _scan.iter_imports(_scan.parse(relpath)):
            if lazy and relpath in LAZY_DRIVER_LAYER_IMPORTERS:
                continue
            for target in _import_targets(relpath, node):
                if target[:1] == ("drivers",):
                    bad.append(f"{relpath}:{node.lineno} imports {'.'.join(target)}")
    return bad


class TestImportBoundaries:
    def test_contract_imports_only_stdlib_and_itself(self):
        bad = _collect_contract_imports()
        assert not bad, "contract/ must import only the standard library and contract/:\n" + "\n".join(bad)

    def test_core_never_imports_concrete_driver_surface_or_provisioner(self):
        problems = _scan.compare_with_allowlist(
            _collect_core_concrete_imports(),
            KNOWN_CORE_IMPORTS_OF_CONCRETE_MODULES,
            "core import of a concrete driver/surface/provisioner",
        )
        assert not problems, "\n".join(problems)

    def test_core_never_reaches_a_concrete_module_through_a_module_outside_the_package(self):
        problems = _scan.compare_with_allowlist(
            _collect_core_detours_into_concrete_modules(),
            KNOWN_CORE_DETOURS_INTO_CONCRETE_MODULES,
            "core import of an outside module that imports a concrete driver/surface/provisioner",
        )
        assert not problems, "\n".join(problems)

    def test_core_reaches_the_registries_from_the_composition_root_only(self):
        bad = _collect_core_imports_of_registries()
        message = "the registries are imported by runtime/assembly.py (surfaces also by config.py) only:\n"
        assert not bad, message + "\n".join(bad)

    def test_surfaces_never_import_a_driver_or_provisioner(self):
        bad = _collect_surface_imports_of_drivers_or_provisioners()
        assert not bad, (
            "surfaces/ may import contract/ and runtime/ only (the settings facade a driver config, lazily):\n"
            + "\n".join(bad)
        )

    def test_openjiuwen_core_never_imports_harness(self):
        problems = _scan.compare_with_allowlist(
            _collect_core_to_harness_imports(),
            KNOWN_CORE_TO_HARNESS_IMPORTS,
            "openjiuwen.core import of openjiuwen.harness",
        )
        assert not problems, "\n".join(problems)

    def test_drivers_import_only_the_contract_their_package_and_surface_commands_and_contexts(self):
        problems = _scan.compare_with_allowlist(
            _collect_driver_imports_outside_their_layer(),
            KNOWN_DRIVER_IMPORTS_OUTSIDE_THEIR_LAYER,
            "driver import outside contract/, its own package and surface commands/contexts",
        )
        assert not problems, "\n".join(problems)

    def test_provisioning_imports_only_the_contract(self):
        bad = _collect_provisioning_imports_outside_the_contract()
        assert not bad, "provisioning/ may import contract/ only:\n" + "\n".join(bad)

    def test_runtime_and_surfaces_reach_drivers_from_the_composition_root_only(self):
        bad = _collect_imports_of_the_driver_layer()
        message = "only runtime/assembly.py imports drivers/ (the settings facade lazily, for the driver config):\n"
        assert not bad, message + "\n".join(bad)

    def test_every_module_at_the_package_root_is_known(self):
        stray = sorted(f for f in _scan.pkg_files() if "/" not in f and f not in ROOT_MODULES)
        assert not stray, f"modules at the package root belong to no layer; move them: {stray}"
