#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Environment variables are read only in the config modules (plan section 3, invariant 9).

Allowed: ``playwright_runtime/config.py`` (``runtime/config.py`` after Phase 8) and
``surfaces/<name>/config.py``. Every other module of the package is scanned with
``ast`` for three kinds of read (decision D2):

1. direct: ``os.getenv`` and any use of ``os.environ``;
2. helpers: every call to a function defined in ``utils/env.py``, also when it is
   reached through a re-export (``utils/__init__.py``, ``playwright_runtime/config.py``);
3. key tuples: any other call whose arguments (or a tuple/list/set literal among
   them) contain a known environment variable name as a string literal, such as
   ``_resolve_sampling_params(temperature_keys=("BROWSER_WORKER_TEMPERATURE", ...))``.

Each detected read is recorded as a descriptor (``os.getenv(BROWSER_DRIVER)``,
``utils.env.resolve_upload_root()``, ``_env_first(A,B)``; ``?`` marks a key that is
not a literal) and counted per file.

The rule spans the whole package, not only the core folders (``runtime/`` plus
today's ``playwright_runtime/``, ``controllers/``, ``utils/`` and ``clients/``, see
``_scan.py``): drivers and provisioners receive settings objects from Phase 3 on.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from functools import lru_cache

from . import _scan

ENV_MODULE = ("utils", "env")
UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

# Environment variables read only through key tuples or local helpers, so they never
# appear as a literal key of a direct read or a utils.env call. Names that do are
# collected from the code at test time and need not be listed here.
KEY_TUPLE_ENV_NAMES = frozenset(
    {
        "BROWSER_MODEL_TEMPERATURE",
        "BROWSER_MODEL_TOP_P",
        "BROWSER_RUNTIME_MCP_ARGS",
        "BROWSER_RUNTIME_MCP_CLIENT_TYPE",
        "BROWSER_RUNTIME_MCP_COMMAND",
        "BROWSER_RUNTIME_MCP_ENABLED",
        "BROWSER_RUNTIME_MCP_HOST",
        "BROWSER_RUNTIME_MCP_PATH",
        "BROWSER_RUNTIME_MCP_PORT",
        "BROWSER_RUNTIME_MCP_SERVER_ID",
        "BROWSER_RUNTIME_MCP_SERVER_NAME",
        "BROWSER_RUNTIME_MCP_SERVER_PATH",
        "BROWSER_RUNTIME_MCP_TIMEOUT_S",
        "BROWSER_WORKER_TEMPERATURE",
        "BROWSER_WORKER_TOP_P",
        "MODEL_TEMPERATURE",
        "MODEL_TOP_P",
        "OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW",
        "OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON",
        "PLAYWRIGHT_RUNTIME_MCP_ARGS",
        "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE",
        "PLAYWRIGHT_RUNTIME_MCP_COMMAND",
        "PLAYWRIGHT_RUNTIME_MCP_ENABLED",
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_ID",
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_NAME",
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH",
        "PLAYWRIGHT_RUNTIME_MCP_TIMEOUT_S",
    }
)

# file relative to the package -> {descriptor: count}.
KNOWN_ENV_READS: dict[str, dict[str, int]] = {
    # all removed in Phase 3 (reads move into the config modules; settings objects are passed in)
    "drivers/browser_use/sidecar/session_adapter.py": {
        "os.environ.get(BROWSER_UPLOAD_ROOT)": 1,
    },
    "drivers/browser_use/transport.py": {
        "os.environ.get(?)": 3,
        "os.environ.get(BROWSER_UPLOAD_ROOT)": 1,
        "os.environ.items(?)": 1,
        "os.getenv(BROWSER_USE_SIDECAR_PYTHON)": 1,
    },
    "controllers/action.py": {
        "utils.env.resolve_upload_root()": 1,
    },
    "provisioning/managed_browser.py": {
        "os.getenv(LOCALAPPDATA)": 2,
        "os.getenv(ProgramFiles(x86))": 1,
        "os.getenv(ProgramFiles)": 1,
        "os.getenv(ProgramW6432)": 1,
    },
    "playwright_runtime/agents.py": {
        (
            "_resolve_sampling_params(BROWSER_WORKER_TEMPERATURE,BROWSER_MODEL_TEMPERATURE,MODEL_TEMPERATURE,"
            "BROWSER_WORKER_TOP_P,BROWSER_MODEL_TOP_P,MODEL_TOP_P)"
        ): 1,
        "os.getenv(?)": 1,
        "os.getenv(BROWSER_TIMEOUT_S)": 1,
        "os.getenv(PLAYWRIGHT_MCP_TIMEOUT_S)": 1,
        "os.getenv(PLAYWRIGHT_TOOL_TIMEOUT_S)": 1,
    },
    "playwright_runtime/browser_logging.py": {
        "_env_bool(OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW)": 1,
        "_env_bool(OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON)": 1,
        "os.getenv(?)": 1,
        "os.getenv(OPENJIUWEN_BROWSER_AGENT_LOG_FILE)": 1,
        "os.getenv(OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL)": 1,
    },
    "playwright_runtime/browser_tools.py": {
        "_env_bool(PLAYWRIGHT_RUNTIME_MCP_ENABLED,BROWSER_RUNTIME_MCP_ENABLED)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_LOG_DIR,BROWSER_RUNTIME_LOG_DIR)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_ARGS,BROWSER_RUNTIME_MCP_ARGS)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE,BROWSER_RUNTIME_MCP_CLIENT_TYPE)": 2,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_COMMAND,BROWSER_RUNTIME_MCP_COMMAND)": 2,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_HOST,BROWSER_RUNTIME_MCP_HOST)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_PATH,BROWSER_RUNTIME_MCP_PATH)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_PORT,BROWSER_RUNTIME_MCP_PORT)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_SERVER_ID,BROWSER_RUNTIME_MCP_SERVER_ID)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_SERVER_NAME,BROWSER_RUNTIME_MCP_SERVER_NAME)": 1,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH,BROWSER_RUNTIME_MCP_SERVER_PATH)": 3,
        "_env_first(PLAYWRIGHT_RUNTIME_MCP_TIMEOUT_S,BROWSER_RUNTIME_MCP_TIMEOUT_S)": 1,
        "env.get(OPENAI_API_KEY)": 1,
        "env.get(OPENAI_BASE_URL)": 1,
        "env.get(OPENROUTER_API_KEY)": 1,
        "env.get(OPENROUTER_BASE_URL)": 1,
        "os.environ": 1,
        "os.getenv(?)": 2,
        "os.getenv(API_BASE)": 1,
        "os.getenv(API_KEY)": 1,
        "os.getenv(MODEL_PROVIDER)": 1,
    },
    "playwright_runtime/main.py": {
        "os.getenv(AGENT_QUERY)": 1,
        "os.getenv(AGENT_SESSION_ID)": 1,
        "utils.env.load_repo_dotenv()": 1,
    },
    "playwright_runtime/service.py": {
        "env_map.get(PLAYWRIGHT_MCP_CDP_ENDPOINT)": 1,
        "env_map.pop(PLAYWRIGHT_MCP_DEVICE)": 1,
        "env_map.setdefault(PLAYWRIGHT_MCP_BROWSER)": 1,
        "os.getenv(BROWSER_DRIVER)": 1,
        "os.getenv(BROWSER_MANAGED_ARGS)": 4,
        "os.getenv(BROWSER_MANAGED_BINARY)": 1,
        "os.getenv(BROWSER_MANAGED_HOST)": 1,
        "os.getenv(BROWSER_MANAGED_KILL_EXISTING)": 2,
        "os.getenv(BROWSER_MANAGED_PORT)": 1,
        "os.getenv(BROWSER_MANAGED_USER_DATA_DIR)": 2,
        "os.getenv(BROWSER_PROFILE_NAME)": 1,
        "os.getenv(BROWSER_PROFILE_STORE_PATH)": 2,
        "utils.env.parse_command_args()": 4,
    },
    "playwright_runtime/site_profiles.py": {
        "os.environ.get(OPENJIUWEN_BROWSER_SELECTOR_CACHE)": 1,
    },
    "playwright_runtime/status_logging.py": {
        "os.getenv(BROWSER_SUBAGENT_STATUS_LOG)": 1,
    },
    "playwright_runtime_mcp_server.py": {
        "os.environ.setdefault(BROWSER_TIMEOUT_S)": 1,
        "os.environ.setdefault(PLAYWRIGHT_TOOL_TIMEOUT_S)": 1,
        "os.getenv(BROWSER_RUNTIME_LOG_DIR)": 1,
        "os.getenv(BROWSER_TIMEOUT_S)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_LOG_DIR)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_MCP_HOST)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_MCP_LOG_LEVEL)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_MCP_PATH)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_MCP_PORT)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_MCP_STATELESS_HTTP)": 1,
        "os.getenv(PLAYWRIGHT_RUNTIME_MCP_TRANSPORT)": 1,
        "utils.env.load_repo_dotenv()": 2,
    },
    "utils/env.py": {
        "os.getenv(?)": 3,
        "utils.env.first_non_empty_env(API_BASE,MODEL_API_BASE)": 1,
        "utils.env.first_non_empty_env(API_BASE,MODEL_API_BASE,DASHSCOPE_BASE_URL,DASHSCOPE_API_BASE)": 1,
        "utils.env.first_non_empty_env(API_BASE,MODEL_API_BASE,OPENAI_BASE_URL,OPENAI_API_BASE)": 1,
        "utils.env.first_non_empty_env(API_BASE,MODEL_API_BASE,OPENROUTER_BASE_URL,OPENROUTER_API_BASE)": 1,
        "utils.env.first_non_empty_env(API_BASE,MODEL_API_BASE,SILICONFLOW_BASE_URL,SILICONFLOW_API_BASE)": 1,
        "utils.env.first_non_empty_env(API_KEY,MODEL_API_KEY,DASHSCOPE_API_KEY,OPENAI_API_KEY,OPENROUTER_API_KEY)": 1,
        "utils.env.first_non_empty_env(API_KEY,MODEL_API_KEY,OPENAI_API_KEY,OPENROUTER_API_KEY)": 1,
        "utils.env.first_non_empty_env(API_KEY,MODEL_API_KEY,OPENROUTER_API_KEY,OPENAI_API_KEY)": 1,
        "utils.env.first_non_empty_env(API_KEY,MODEL_API_KEY,SILICONFLOW_API_KEY,OPENAI_API_KEY,OPENROUTER_API_KEY)": 1,
        "utils.env.first_non_empty_env(BROWSER_UPLOAD_ROOT)": 1,
        "utils.env.first_non_empty_env(DASHSCOPE_API_KEY)": 1,
        "utils.env.first_non_empty_env(MODEL_NAME)": 1,
        "utils.env.first_non_empty_env(MODEL_PROVIDER,MODEL_CLIENT_PROVIDER)": 1,
        "utils.env.first_non_empty_env(OPENROUTER_API_KEY)": 1,
        (
            "utils.env.first_non_empty_env(OPENROUTER_BASE_URL,OPENROUTER_API_BASE,SILICONFLOW_BASE_URL,"
            "SILICONFLOW_API_BASE,DASHSCOPE_BASE_URL,DASHSCOPE_API_BASE,OPENAI_BASE_URL,OPENAI_API_BASE)"
        ): 1,
        "utils.env.first_non_empty_env(SILICONFLOW_API_KEY)": 1,
        "utils.env.infer_provider_from_api_base()": 1,
        "utils.env.is_falsy_env()": 1,
        "utils.env.is_truthy_env()": 1,
        "utils.env.normalize_provider()": 1,
        "utils.env.resolve_int_env(BROWSER_TIMEOUT_S,PLAYWRIGHT_TOOL_TIMEOUT_S)": 1,
        "utils.env.resolve_repo_dotenv_path()": 1,
    },
    "utils/upload_paths.py": {
        "utils.env.resolve_upload_root()": 1,
    },
}


def _module_file(parts: tuple[str, ...]) -> str | None:
    for candidate in ("/".join(parts) + ".py", "/".join(parts + ("__init__",)) + ".py"):
        if candidate in _scan.pkg_files():
            return candidate
    return None


@lru_cache(maxsize=None)
def _env_functions() -> frozenset[str]:
    tree = _scan.parse(_module_file(ENV_MODULE))
    return frozenset(n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


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
    """Kinds 1 and 2. Returns descriptors, ids of the calls they cover, literal keys."""
    tree = _scan.parse(relpath)
    parents = _parents(tree)
    found: Counter = Counter()
    covered: set[int] = set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or isinstance(parents.get(node), ast.Attribute):
            continue  # only the outermost name of an attribute chain
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
    names = set(KEY_TUPLE_ENV_NAMES)
    for relpath in _scan.pkg_files():
        names |= _direct_and_helper_reads(relpath)[2]
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
