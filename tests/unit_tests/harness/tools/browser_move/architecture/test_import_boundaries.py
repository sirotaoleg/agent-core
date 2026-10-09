#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Import boundaries of browser_move (plan section 3, invariants 1 and 2).

- ``contract/`` imports only the standard library and itself.
- Core folders never import a concrete driver, a concrete surface or a
  provisioning module; only the composition root (``runtime/assembly.py``) may,
  and only lazily, inside a function.
- ``openjiuwen/core`` never imports ``openjiuwen.harness``.

Core folders are ``runtime/`` plus today's ``playwright_runtime/``,
``controllers/``, ``utils/`` and ``clients/`` until Phase 8/9 moves them (see
``_scan.py``). Imports are found with ``ast``; nothing is executed.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter

from . import _scan

# (file relative to the package, imported module relative to the package) -> count.
KNOWN_CORE_IMPORTS_OF_CONCRETE_MODULES: dict[tuple[str, str], int] = {
    # removed in Phase 7 (service.py stops starting Chrome itself in Phase 3)
    ("playwright_runtime/service.py", "provisioning.managed_browser"): 2,
}

# (file relative to openjiuwen/core, imported module) -> count.
KNOWN_CORE_TO_HARNESS_IMPORTS: dict[tuple[str, str], int] = {
    # removed in Phase 7
    (
        "single_agent/agents/react_agent.py",
        "openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging",
    ): 1,
    # Outside browser_move: no phase of the driver plan removes these (open question in the P1-T1 report).
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

_STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}


def _concrete_kind(parts: tuple[str, ...]) -> str | None:
    """Classify a package module path as a concrete driver, surface or provisioner."""
    if len(parts) < 2:
        return None
    head, name = parts[0], parts[1]
    if head == "drivers" and name != "middleware":
        return "driver"
    if head == "backends" and name == "browser_use":
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
        # ``from ..drivers import x`` reaches the module ``drivers.x`` only if it exists.
        targets.append(sub if alias.name != "*" and _scan.is_package_module(sub) else base)
    return targets


def _collect_core_concrete_imports() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(_scan.is_core):
        lazy_allowed = relpath in _scan.ASSEMBLY_MODULES
        for node, lazy in _scan.iter_imports(_scan.parse(relpath)):
            for target in _import_targets(relpath, node):
                if _concrete_kind(target) and not (lazy_allowed and lazy):
                    found[(relpath, ".".join(target))] += 1
    return found


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
                if name.split(".")[0] not in _STDLIB or target is not None:
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

    def test_openjiuwen_core_never_imports_harness(self):
        problems = _scan.compare_with_allowlist(
            _collect_core_to_harness_imports(),
            KNOWN_CORE_TO_HARNESS_IMPORTS,
            "openjiuwen.core import of openjiuwen.harness",
        )
        assert not problems, "\n".join(problems)
