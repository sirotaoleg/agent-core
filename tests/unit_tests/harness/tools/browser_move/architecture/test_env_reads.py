#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Environment variables are read only in the config modules."""

from __future__ import annotations

import ast
import re
from collections import Counter
from functools import lru_cache

from . import _scan

ENV_MODULE = ("runtime", "utils", "env")
UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

KNOWN_ENV_READS: dict[str, dict[str, int]] = {
    "surfaces/browser/runner/facade.py": {
        "env_map.get(PLAYWRIGHT_MCP_CDP_ENDPOINT)": 1,
    },
}


def _module_file(parts: tuple[str, ...]) -> str | None:
    for candidate in ("/".join(parts) + ".py", "/".join(parts + ("__init__",)) + ".py"):
        if candidate in _scan.pkg_files():
            return candidate
    return None


def _reads_process_env(function: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr in ("getenv", "environ")
        for node in ast.walk(function)
    )


@lru_cache(maxsize=None)
def _env_functions() -> frozenset[str]:
    """``runtime/utils/env.py`` functions reading the process environment; pure helpers over a mapping do not count."""
    tree = _scan.parse(_module_file(ENV_MODULE))
    return frozenset(
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _reads_process_env(n)
    )


@lru_cache(maxsize=None)
def _bindings(relpath: str) -> dict[str, tuple[str, ...]]:
    """Local names bound by imports: name -> package path or ``("<os>", attr)``."""
    bound: dict[str, tuple[str, ...]] = {}
    for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    bound[alias.asname or "os"] = ("<os>",)
                    continue
                target = _scan.resolve_import(relpath, alias.name, 0)
                if target is not None and alias.asname:
                    bound[alias.asname] = target
        elif node.module == "os" and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = ("<os>", alias.name)
        else:
            base = _scan.resolve_import(relpath, node.module, node.level)
            if base is None:
                continue
            for alias in node.names:
                if alias.name != "*":
                    bound[alias.asname or alias.name] = base + (alias.name,)
    if relpath == _module_file(ENV_MODULE):
        for name in _env_functions():
            bound.setdefault(name, ENV_MODULE + (name,))
    return bound


def _origin(target: tuple[str, ...], depth: int = 0) -> tuple[str, ...]:
    """Follow re-exports until the defining module (or give up after a few hops)."""
    if depth > 8 or len(target) < 2 or target[0] == "<os>" or target[:-1] == ENV_MODULE:
        return target
    owner = _module_file(target[:-1])
    if owner is None:
        return target
    rebound = _bindings(owner).get(target[-1])
    return _origin(rebound, depth + 1) if rebound and rebound != target else target


def _resolve(relpath: str, expr: ast.expr) -> tuple[str, ...] | None:
    attrs: list[str] = []
    while isinstance(expr, ast.Attribute):
        attrs.insert(0, expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    root = _bindings(relpath).get(expr.id)
    if root is None:
        return None
    return _origin(root + tuple(attrs))


def _literal_args(call: ast.Call) -> list[str]:
    values: list[str] = []
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        items = arg.elts if isinstance(arg, (ast.Tuple, ast.List, ast.Set)) else [arg]
        values += [i.value for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)]
    return values


def _key(call: ast.Call | None) -> str:
    if call is None or not call.args:
        return "?"
    first = call.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else "?"


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


@lru_cache(maxsize=None)
def _direct_and_helper_reads(relpath: str) -> tuple[Counter, set[int], set[str]]:
    """Direct and helper reads: descriptors, ids of the calls they cover, literal keys."""
    tree = _scan.parse(relpath)
    parents = _parents(tree)
    found: Counter = Counter()
    covered: set[int] = set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or isinstance(parents.get(node), ast.Attribute):
            continue
        target = _resolve(relpath, node)
        if target is None:
            continue
        parent = parents.get(node)
        if target[:1] == ("<os>",) and len(target) >= 2 and target[1] in ("getenv", "environ"):
            call = parent if isinstance(parent, ast.Call) and parent.func is node else None
            if target[1] == "getenv" or len(target) > 2:
                label = "os.getenv" if target[1] == "getenv" else f"os.environ.{target[2]}"
                found[f"{label}({_key(call)})"] += 1
            elif isinstance(parent, ast.Subscript) and isinstance(parent.slice, ast.Constant):
                found[f"os.environ[{parent.slice.value}]"] += 1
                keys.add(str(parent.slice.value))
            else:
                found["os.environ"] += 1
            if call is not None:
                covered.add(id(call))
                keys.update(_literal_args(call)[:1])
        elif target[:-1] == ENV_MODULE and target[-1] in _env_functions():
            if isinstance(parent, ast.Call) and parent.func is node:
                names = [v for v in _literal_args(parent) if UPPER_SNAKE_RE.match(v)]
                found[f"utils.env.{target[-1]}({','.join(names)})"] += 1
                covered.add(id(parent))
                keys.update(names)
    return found, covered, keys


@lru_cache(maxsize=None)
def _known_env_names() -> frozenset[str]:
    names: set[str] = set()
    for relpath in _scan.pkg_files():
        names |= _direct_and_helper_reads(relpath)[2]
        if _scan.is_config_module(relpath):
            tree = _scan.parse(relpath)
            names |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return frozenset(n for n in names if UPPER_SNAKE_RE.match(n))


def _collect_env_reads() -> dict[str, Counter]:
    known = _known_env_names()
    result: dict[str, Counter] = {}
    for relpath in _scan.files_where(lambda f: not _scan.is_config_module(f)):
        direct, covered, _keys = _direct_and_helper_reads(relpath)
        found = Counter(direct)
        for node in ast.walk(_scan.parse(relpath)):
            if isinstance(node, ast.Call) and id(node) not in covered:
                names = [v for v in _literal_args(node) if v in known]
                if names:
                    found[f"{ast.unparse(node.func)}({','.join(names)})"] += 1
        if found:
            result[relpath] = found
    return result


class TestEnvReads:
    def test_env_is_read_only_in_config_modules(self):
        actual = _collect_env_reads()
        flat_actual = {(f, d): n for f, reads in actual.items() for d, n in reads.items()}
        flat_known = {(f, d): n for f, reads in KNOWN_ENV_READS.items() for d, n in reads.items()}
        problems = _scan.compare_with_allowlist(flat_actual, flat_known, "env read outside the config modules")
        assert not problems, "\n".join(problems)
