# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime-native custom action controller for Playwright runtime.
It exposes a lightweight action registry consumed by the server entry point.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import inspect
from typing import Any, Awaitable, Callable, Protocol

from openjiuwen.core.common.logging import logger

from .base import BaseController

ActionResult = dict[str, Any]
ActionHandler = Callable[..., Awaitable[Any] | Any]
CodeExecutor = Callable[[str], Awaitable[Any]]


class RuntimeRunner(Protocol):
    async def __call__(
        self,
        *,
        task: str,
        session_id: str | None = None,
        request_id: str | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]: ...


_ACTIONS: dict[str, ActionHandler] = {}
_ACTION_SPECS: dict[str, dict[str, Any]] = {}
_RUNTIME_RUNNER: RuntimeRunner | None = None
_CODE_EXECUTOR: CodeExecutor | None = None
_LOCK = asyncio.Lock()
_ctx_browser_worker_action: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "playwright_runtime_browser_worker_action",
    default=False,
)
_RECURSIVE_BROWSER_ACTIONS = frozenset({"browser_task", "run_browser_task"})


@contextlib.contextmanager
def browser_worker_action_context():
    token = _ctx_browser_worker_action.set(True)
    try:
        yield
    finally:
        _ctx_browser_worker_action.reset(token)


class ActionController(BaseController):
    """Instance-scoped registry and dispatcher for custom actions."""

    def __init__(
        self,
        *,
        actions: dict[str, ActionHandler] | None = None,
        action_specs: dict[str, dict[str, Any]] | None = None,
        runtime_runner: RuntimeRunner | None = None,
        code_executor: CodeExecutor | None = None,
        lock: asyncio.Lock | None = None,
    ) -> None:
        self._actions: dict[str, ActionHandler] = actions if actions is not None else {}
        self._action_specs: dict[str, dict[str, Any]] = action_specs if action_specs is not None else {}
        self._runtime_runner: RuntimeRunner | None = runtime_runner
        self._code_executor: CodeExecutor | None = code_executor
        self._lock: asyncio.Lock = lock if lock is not None else asyncio.Lock()

    @property
    def runtime_runner(self) -> RuntimeRunner | None:
        return self._runtime_runner

    @property
    def code_executor(self) -> CodeExecutor | None:
        return self._code_executor

    def bind_runtime(self, runtime: Any) -> None:
        run_browser_task = getattr(runtime, "run_browser_task", None)
        if run_browser_task is None or not callable(run_browser_task):
            raise ValueError("runtime must expose an async run_browser_task(...) method")

        async def _runner(
            *,
            task: str,
            session_id: str | None = None,
            request_id: str | None = None,
            timeout_s: int | None = None,
        ) -> dict[str, Any]:
            return await run_browser_task(
                task=task,
                session_id=session_id,
                request_id=request_id,
                timeout_s=timeout_s,
            )

        self.bind_runtime_runner(_runner)

    def bind_runtime_runner(self, runner: RuntimeRunner | None) -> None:
        self._runtime_runner = runner

    def clear_runtime_runner(self) -> None:
        self.bind_runtime_runner(None)

    def bind_code_executor(self, executor: CodeExecutor | None) -> None:
        self._code_executor = executor

    def clear_code_executor(self) -> None:
        self._code_executor = None

    def register_action(self, name: str, handler: ActionHandler, *, overwrite: bool = True) -> None:
        action_name = _normalize_action_name(name)
        if not action_name:
            raise ValueError("action name must be non-empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not overwrite and action_name in self._actions:
            raise ValueError(f"action already exists: {action_name}")
        self._actions[action_name] = handler

    def register_action_spec(
        self,
        name: str,
        *,
        summary: str = "",
        when_to_use: str = "",
        params: dict[str, str] | None = None,
    ) -> None:
        action_name = _normalize_action_name(name)
        if not action_name:
            raise ValueError("action name must be non-empty")
        self._action_specs[action_name] = {
            "summary": (summary or "").strip(),
            "when_to_use": (when_to_use or "").strip(),
            "params": dict(params or {}),
        }

    def list_actions(self) -> list[str]:
        return sorted(self._actions.keys())

    def describe_actions(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name in sorted(self._actions.keys()):
            spec = self._action_specs.get(name, {})
            result[name] = {
                "summary": str(spec.get("summary", "") or ""),
                "when_to_use": str(spec.get("when_to_use", "") or ""),
                "params": dict(spec.get("params", {}) or {}),
            }
        return result

    async def run_action(
        self,
        action: str,
        session_id: str = "",
        request_id: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        action_name = _normalize_action_name(action)
        sid = (session_id or "").strip()
        rid = (request_id or "").strip()
        param_keys = ",".join(sorted(str(key) for key in kwargs.keys()))
        logger.info(
            f"CONTROLLER_ACTION start action={action_name} session_id={sid or '-'} "
            f"request_id={rid or '-'} param_keys={param_keys or '-'}"
        )

        if _ctx_browser_worker_action.get() and action_name in _RECURSIVE_BROWSER_ACTIONS:
            error = (
                "recursive_browser_task_blocked: browser workers must not invoke "
                "browser_task/run_browser_task via browser_custom_action; return a JSON error instead"
            )
            logger.warning(
                f"CONTROLLER_ACTION blocked action={action_name} session_id={sid or '-'} "
                f"request_id={rid or '-'} error={error}"
            )
            return {
                "ok": False,
                "action": action_name,
                "session_id": sid,
                "request_id": rid,
                "error": error,
            }

        handler = self._actions.get(action_name)
        if handler is None:
            logger.warning(
                f"CONTROLLER_ACTION unknown action={action_name} session_id={sid or '-'} request_id={rid or '-'}"
            )
            return {
                "ok": False,
                "action": action_name,
                "session_id": sid,
                "request_id": rid,
                "error": f"unknown action: {action_name}",
            }

        try:
            async with self._lock:
                raw = await _maybe_await(handler(session_id=sid, request_id=rid, **kwargs))

            response = dict(raw) if isinstance(raw, dict) else {"result": raw}
            response.setdefault("ok", True)
            response.setdefault("action", action_name)
            response.setdefault("session_id", sid)
            response.setdefault("request_id", rid)
            response.setdefault("error", None)
            _ok = bool(response.get("ok", False))
            _err = response.get("error") if not _ok else None
            logger.info(
                f"CONTROLLER_ACTION end action={action_name} session_id={sid or '-'} "
                f"request_id={rid or '-'} ok={_ok}" + (f" error={_err!r}" if _err else "")
            )
            return response
        except Exception as exc:
            logger.error(
                f"CONTROLLER_ACTION error action={action_name} session_id={sid or '-'} "
                f"request_id={rid or '-'} error={exc}"
            )
            return {
                "ok": False,
                "action": action_name,
                "session_id": sid,
                "request_id": rid,
                "error": str(exc),
            }

    def register_builtin_actions(self) -> None:
        register_builtin_actions(controller=self)

    def register_example_actions(self) -> None:
        register_builtin_actions(controller=self)

    def snapshot(self) -> dict[str, Any]:
        return {
            "actions": dict(self._actions),
            "action_specs": copy.deepcopy(self._action_specs),
            "runtime_runner": self._runtime_runner,
            "code_executor": self._code_executor,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self._actions.clear()
        self._actions.update(dict(snapshot.get("actions", {})))
        self._action_specs.clear()
        self._action_specs.update(copy.deepcopy(dict(snapshot.get("action_specs", {}))))
        self._runtime_runner = snapshot.get("runtime_runner")
        self._code_executor = snapshot.get("code_executor")


def _normalize_action_name(name: str) -> str:
    return (name or "").strip().lower()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


_DEFAULT_CONTROLLER = ActionController(
    actions=_ACTIONS,
    action_specs=_ACTION_SPECS,
    runtime_runner=_RUNTIME_RUNNER,
    lock=_LOCK,
)


def get_default_controller() -> ActionController:
    return _DEFAULT_CONTROLLER


def bind_runtime(runtime: Any) -> None:
    _DEFAULT_CONTROLLER.bind_runtime(runtime)
    global _RUNTIME_RUNNER
    _RUNTIME_RUNNER = _DEFAULT_CONTROLLER.runtime_runner


def bind_runtime_runner(runner: RuntimeRunner | None) -> None:
    _DEFAULT_CONTROLLER.bind_runtime_runner(runner)
    global _RUNTIME_RUNNER
    _RUNTIME_RUNNER = runner


def clear_runtime_runner() -> None:
    bind_runtime_runner(None)


def bind_code_executor(executor: CodeExecutor | None) -> None:
    _DEFAULT_CONTROLLER.bind_code_executor(executor)
    global _CODE_EXECUTOR
    _CODE_EXECUTOR = executor


def clear_code_executor() -> None:
    bind_code_executor(None)


def register_action(name: str, handler: ActionHandler, *, overwrite: bool = True) -> None:
    _DEFAULT_CONTROLLER.register_action(name=name, handler=handler, overwrite=overwrite)


def register_action_spec(
    name: str,
    *,
    summary: str = "",
    when_to_use: str = "",
    params: dict[str, str] | None = None,
) -> None:
    _DEFAULT_CONTROLLER.register_action_spec(
        name=name,
        summary=summary,
        when_to_use=when_to_use,
        params=params,
    )


def list_actions() -> list[str]:
    return _DEFAULT_CONTROLLER.list_actions()


def describe_actions() -> dict[str, dict[str, Any]]:
    return _DEFAULT_CONTROLLER.describe_actions()


async def run_action(
    action: str,
    session_id: str = "",
    request_id: str = "",
    **kwargs: Any,
) -> ActionResult:
    return await _DEFAULT_CONTROLLER.run_action(
        action=action,
        session_id=session_id,
        request_id=request_id,
        **kwargs,
    )


def register_builtin_actions(controller: ActionController | None = None) -> None:
    ctl = controller or _DEFAULT_CONTROLLER

    async def ping(session_id: str = "", request_id: str = "", **kwargs: Any) -> ActionResult:
        return {
            "ok": True,
            "pong": True,
            "session_id": session_id,
            "request_id": request_id,
            "meta": kwargs,
        }

    async def echo(
        session_id: str = "",
        request_id: str = "",
        text: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        return {
            "ok": True,
            "text": text,
            "session_id": session_id,
            "request_id": request_id,
            "meta": kwargs,
        }

    async def browser_task(
        session_id: str = "",
        request_id: str = "",
        task: str = "",
        timeout_s: int | None = None,
        **kwargs: Any,
    ) -> ActionResult:
        runner = ctl.runtime_runner
        if runner is None:
            return {
                "ok": False,
                "error": "runtime_not_bound: call bind_runtime(...) before browser_task",
                "session_id": session_id,
                "request_id": request_id,
            }

        task_text = (task or "").strip()
        if not task_text:
            return {
                "ok": False,
                "error": "missing required parameter: task",
                "session_id": session_id,
                "request_id": request_id,
            }

        effective_timeout: int | None = None
        if timeout_s is not None:
            try:
                parsed = int(timeout_s)
                if parsed > 0:
                    effective_timeout = parsed
            except Exception:
                effective_timeout = None

        return await runner(
            task=task_text,
            session_id=session_id or None,
            request_id=request_id or None,
            timeout_s=effective_timeout,
        )

    ctl.register_action("ping", ping, overwrite=True)
    ctl.register_action_spec(
        "ping",
        summary="Health check action.",
        when_to_use="Use to verify controller dispatch and session/request threading.",
    )
    ctl.register_action("echo", echo, overwrite=True)
    ctl.register_action_spec(
        "echo",
        summary="Echoes provided text and metadata.",
        when_to_use="Use for debugging payload passthrough through browser_custom_action.",
        params={"text": "string: text to echo back"},
    )
    ctl.register_action("browser_task", browser_task, overwrite=True)
    ctl.register_action_spec(
        "browser_task",
        summary="Runs a free-form browser task through runtime.run_browser_task.",
        when_to_use="Use for generic website tasks when no specialized custom action applies.",
        params={
            "task": "string: required task prompt for the browser worker",
            "timeout_s": "int: optional positive timeout override",
        },
    )
    ctl.register_action("run_browser_task", browser_task, overwrite=True)
    ctl.register_action_spec(
        "run_browser_task",
        summary="Alias of browser_task.",
        when_to_use="Same behavior as browser_task.",
        params={
            "task": "string: required task prompt for the browser worker",
            "timeout_s": "int: optional positive timeout override",
        },
    )


def register_example_actions(controller: ActionController | None = None) -> None:
    register_builtin_actions(controller=controller)


__all__ = [
    "BaseController",
    "ActionController",
    "get_default_controller",
    "bind_runtime",
    "bind_runtime_runner",
    "clear_runtime_runner",
    "bind_code_executor",
    "clear_code_executor",
    "register_action",
    "register_action_spec",
    "register_builtin_actions",
    "register_example_actions",
    "browser_worker_action_context",
    "list_actions",
    "describe_actions",
    "run_action",
]
