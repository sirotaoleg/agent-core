#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Static-analysis helpers shared by the browser_move architecture tests.

Nothing here imports the package under test: files are read as text and parsed
with ``ast``.

Folder roles used by the rules (plan section 3/4, Phase 1 item 1):

- ``contract/``: the driver contract (lands in Phase 2).
- core: ``runtime/`` (the plan's name) plus today's ``playwright_runtime/``,
  ``controllers/``, ``utils/`` and ``clients/``. The last four count as core until
  Phase 8/9 moves them (``playwright_runtime`` -> ``runtime``, ``controllers`` and
  ``utils`` under ``runtime/``, ``clients`` into ``drivers/playwright_mcp/``).
- ``surfaces/``: one sub-package per kind of screen (lands in Phase 2).
- concrete drivers: ``drivers/<name>``.
- ``provisioning/<module>``: provisioners (``managed_browser`` moved there in Phase 2;
  the rest land in Phase 3).

Folders that do not exist yet simply contribute no files, so every rule over them
passes vacuously until they appear.
"""

from __future__ import annotations

import ast
import re
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[6]
PKG_DOTTED = "openjiuwen.harness.tools.browser_move"
PKG_DIR = REPO_ROOT.joinpath(*PKG_DOTTED.split("."))
CORE_DIR = REPO_ROOT / "openjiuwen" / "core"

CORE_FOLDERS = ("runtime", "playwright_runtime", "controllers", "utils", "clients")
CONTRACT_FOLDER = "contract"
SURFACES_FOLDER = "surfaces"
# Modules allowed to parse settings (and, for the driver-name rule, name drivers).
CONFIG_MODULE_RE = re.compile(r"^(?:runtime/config\.py|playwright_runtime/config\.py|surfaces/[^/]+/config\.py)$")
# The composition root; until Phase 8 it lives under playwright_runtime/.
ASSEMBLY_MODULES = frozenset({"runtime/assembly.py", "playwright_runtime/assembly.py"})


def rel(path: Path) -> str:
    return path.relative_to(PKG_DIR).as_posix()


@lru_cache(maxsize=None)
def pkg_files() -> tuple[str, ...]:
    """All ``.py`` files of the package, as posix paths relative to it."""
    return tuple(sorted(rel(p) for p in PKG_DIR.rglob("*.py")))


def in_folder(relpath: str, folder: str) -> bool:
    return relpath.startswith(folder + "/")


def is_core(relpath: str) -> bool:
    return any(in_folder(relpath, folder) for folder in CORE_FOLDERS)


def is_contract(relpath: str) -> bool:
    return in_folder(relpath, CONTRACT_FOLDER)


def is_surface(relpath: str) -> bool:
    return in_folder(relpath, SURFACES_FOLDER)


def is_config_module(relpath: str) -> bool:
    return bool(CONFIG_MODULE_RE.match(relpath))


def files_where(predicate) -> list[str]:
    return [f for f in pkg_files() if predicate(f)]


@lru_cache(maxsize=None)
def read_text(relpath: str) -> str:
    return (PKG_DIR / relpath).read_text(encoding="utf-8")


def parse_source(text: str, filename: str) -> ast.Module:
    with warnings.catch_warnings():
        # Invalid escape sequences in the scanned code are not this test's business.
        warnings.simplefilter("ignore", (SyntaxWarning, DeprecationWarning))
        return ast.parse(text, filename=filename)


@lru_cache(maxsize=None)
def parse(relpath: str) -> ast.Module:
    return parse_source(read_text(relpath), relpath)


def module_parts(relpath: str) -> tuple[str, ...]:
    """Dotted module path of a package file, relative to the package root."""
    parts = relpath[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return tuple(parts)


@lru_cache(maxsize=None)
def _top_level_names() -> frozenset[str]:
    return frozenset(p.name[:-3] if p.suffix == ".py" else p.name for p in PKG_DIR.iterdir() if p.name != "__pycache__")


def resolve_import(relpath: str, module: str | None, level: int) -> tuple[str, ...] | None:
    """Resolve an import to a module path relative to the package root.

    Returns ``None`` for modules outside the package. Absolute imports whose first
    component is a top-level entry of the package are resolved too: the stand-alone
    MCP server puts the package directory on ``sys.path`` and imports
    ``playwright_runtime.config`` and the like, and some modules fall back to
    ``from utils.env import ...``.
    """
    if level:
        base = module_parts(relpath)
        if not relpath.endswith("__init__.py"):
            base = base[:-1]
        if level - 1 > len(base):
            return None
        base = base[: len(base) - (level - 1)]
        return base + (tuple(module.split(".")) if module else ())
    if not module:
        return None
    if module == PKG_DOTTED or module.startswith(PKG_DOTTED + "."):
        return tuple(module[len(PKG_DOTTED) + 1 :].split(".")) if module != PKG_DOTTED else ()
    first = module.split(".")[0]
    if first in _top_level_names():
        return tuple(module.split("."))
    return None


def is_package_module(parts: tuple[str, ...]) -> bool:
    """True when ``parts`` names a module or package that exists in the package."""
    if not parts:
        return True
    path = PKG_DIR.joinpath(*parts)
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def iter_imports(tree: ast.AST) -> Iterable[tuple[ast.stmt, bool]]:
    """Yield ``(import node, lazy)``; lazy means inside a function body."""

    def walk(node: ast.AST, lazy: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                yield child, lazy
            yield from walk(child, lazy or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))

    yield from walk(tree, False)


def compare_with_allowlist(actual: Mapping, known: Mapping, label: str) -> list[str]:
    """Ratchet check: new keys and growth fail, and so do entries that no longer occur.

    ``actual`` and ``known`` map a key to a count. A count that dropped but is still
    above zero passes (the entry may be lowered later); zero means the entry is stale.
    """
    problems: list[str] = []
    for key in sorted(set(actual) | set(known), key=repr):
        have = actual.get(key, 0)
        allowed = known.get(key)
        if allowed is None:
            problems.append(f"new {label}: {key!r} (count {have}); fix it, do not allowlist it")
        elif have > allowed:
            problems.append(f"{label} grew: {key!r} has {have}, allowlist says {allowed}")
        elif have == 0:
            problems.append(f"stale entry — remove it: {key!r} no longer has any {label}")
    return problems
