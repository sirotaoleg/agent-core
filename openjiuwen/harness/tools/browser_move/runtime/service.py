# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The backend service: the driver session's lifecycle, with sticky sessions and guardrails."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

from .assembly import open_session
from .config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
    CoreSettings,
    SurfaceSettings,
    browser_surface_settings,
    process_environ,
)
from .driver_session import DriverSession
from .page_state import BrowserPageState
from .service_registry import BROWSER_SERVICE_REGISTRY, BrowserServiceIdentity
from .worker import BrowserTaskProgressState, _WorkerLoop


class BrowserService(_WorkerLoop):
    """Backend service with sticky logical sessions; holds the driver session the worker runs on."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
        cancel_store: Optional[BaseKVStore] = None,
        instance: Optional[BrowserInstanceConfig] = None,
        allowed_tool_names: Optional[Iterable[str]] = None,
        browser_settings: Optional[SurfaceSettings] = None,
        core_settings: Optional[CoreSettings] = None,
        page_state: Optional[BrowserPageState] = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.mcp_cfg = mcp_cfg
        self.guardrails = guardrails
        self._instance = instance
        self._settings = browser_settings or browser_surface_settings()
        self._core_settings = core_settings
        self._core: Optional[CoreSettings] = None
        self._allowed_tool_names = (
            None
            if allowed_tool_names is None
            else tuple(dict.fromkeys(allowed_tool_names))
        )
        self._cancel_store: BaseKVStore = cancel_store or InMemoryKVStore()
        self._page_state = page_state or BrowserPageState()
        self._session: Optional[DriverSession] = None

        self.started = False
        self._browser_agent: Optional[ReActAgent] = None
        self._locks: Dict[str, asyncio.Lock] = {}
        self._sessions: set[str] = set()
        self._inflight_tasks: Dict[str, set[asyncio.Task[Any]]] = {}
        self._screenshot_subdir = "screenshots"
        self._artifacts_subdir = "artifacts"
        self._mcp_cwd = self._resolve_mcp_cwd()
        self._screenshots_dir = self._mcp_cwd / self._screenshot_subdir
        self._artifacts_dir = self._mcp_cwd / self._artifacts_subdir
        self._profile_name = self._resolve_profile_name()
        self._driver_mode = self._resolve_driver_mode()
        self._failure_context_by_session: Dict[str, str] = {}
        self._progress_by_session: Dict[str, BrowserTaskProgressState] = {}
        self._lifecycle_identity = self._build_lifecycle_identity()
        self._registry_acquired = False
        self._task_binding_ref_count = 0

    @property
    def browser_agent(self) -> Optional[ReActAgent]:
        return self._browser_agent

    @browser_agent.setter
    def browser_agent(self, value: Optional[ReActAgent]) -> None:
        self._browser_agent = value

    @property
    def allowed_tool_names(self) -> Optional[tuple[str, ...]]:
        """Return the normalized task-scoped Playwright tool allowlist."""
        return self._allowed_tool_names

    @property
    def screenshot_subdir(self) -> str:
        return self._screenshot_subdir

    @property
    def artifacts_subdir(self) -> str:
        return self._artifacts_subdir

    @property
    def session(self) -> Optional[DriverSession]:
        return self._session

    @property
    def core(self) -> CoreSettings:
        """The core settings the session is built from; the instance's driver, when set, wins."""
        if self._core is None:
            core = self._core_settings or CoreSettings.from_process_env()
            driver = (self._instance.driver if self._instance else "").strip()
            self._core = replace(core, driver=driver) if driver else core
        return self._core

    def _resolve_profile_name(self) -> str:
        """Resolve the active profile name (per-instance key wins over env).

        Distinct instance keys map to distinct profile names so the managed
        user-data-dir and persisted port never collide across agents.
        """
        instance = self._instance
        name = ""
        if instance:
            name = (instance.profile_name or instance.sanitized_key() or "").strip()
        return name or self._settings.profile_name

    def _resolve_driver_mode(self) -> str:
        explicit = (self._instance.driver_mode or "").strip().lower() if self._instance else ""
        if not explicit:
            return self._settings.provisioning
        if explicit not in {"remote", "managed", "extension"}:
            raise ValueError(
                "BrowserInstanceConfig.provisioning (driver_mode) must be one of: remote, managed, extension "
                f"(got {explicit!r}); leave it empty to use BROWSER_PROVISIONING"
            )
        return explicit

    def _build_lifecycle_identity(self) -> BrowserServiceIdentity:
        """Build the process resource identity for this instance."""
        instance = self._instance
        browser_key = instance.sanitized_key() if instance else ""
        managed_args = self._settings.managed_args
        headless = any(
            arg == "--headless" or arg.startswith("--headless=")
            for arg in managed_args
        )
        if self._driver_mode == "managed":
            display_mode = "headless" if headless else "headed"
        else:
            display_mode = self._driver_mode

        explicit_user_data_dir = str(
            (instance.user_data_dir if instance else "") or self._settings.user_data_dir
        ).strip()
        normalized_user_data_dir = (
            os.path.normcase(
                os.path.normpath(str(Path(explicit_user_data_dir).expanduser()))
            )
            if explicit_user_data_dir
            else f"profile:{self._profile_name}"
        )
        server_id = (self.mcp_cfg.server_id or "").strip() or self.mcp_cfg.server_name
        return BrowserServiceIdentity(
            browser_key=browser_key,
            profile_name=self._profile_name,
            driver_mode=self._driver_mode,
            display_mode=display_mode,
            managed_args=managed_args,
            browser_binary=self.normalize_browser_binary(
                self._configured_browser_binary()
            ),
            user_data_dir=normalized_user_data_dir,
            cdp_endpoint=self._configured_cdp_endpoint(),
            server_id=server_id,
        )

    @property
    def lifecycle_identity(self) -> BrowserServiceIdentity:
        return self._lifecycle_identity

    def _acquire_registry_lease(self) -> None:
        if self._registry_acquired:
            return
        BROWSER_SERVICE_REGISTRY.acquire(self._lifecycle_identity, self)
        self._registry_acquired = True

    def acquire_task_binding(self) -> None:
        """Acquire one task reference to the shared process resources."""
        self._task_binding_ref_count += 1
        self._acquire_registry_lease()
        if self._session is not None:
            self._session.begin_task()

    @staticmethod
    def _cancel_key(session_id: str, request_id: Optional[str] = None) -> str:
        rid = (request_id or "").strip() or "*"
        return f"playwright_runtime:cancel:{session_id}:{rid}"

    @staticmethod
    def _inflight_key(session_id: str, request_id: Optional[str] = None) -> str:
        rid = (request_id or "").strip()
        return f"{session_id}:{rid}" if rid else session_id

    def _register_inflight_task(self, session_id: str, request_id: str, task: asyncio.Task[Any]) -> None:
        keys = (self._inflight_key(session_id), self._inflight_key(session_id, request_id))
        for key in keys:
            self._inflight_tasks.setdefault(key, set()).add(task)

    def _unregister_inflight_task(self, session_id: str, request_id: str, task: asyncio.Task[Any]) -> None:
        keys = (self._inflight_key(session_id), self._inflight_key(session_id, request_id))
        for key in keys:
            tasks = self._inflight_tasks.get(key)
            if not tasks:
                continue
            tasks.discard(task)
            if not tasks:
                self._inflight_tasks.pop(key, None)

    def _resolve_mcp_cwd(self) -> Path:
        params = getattr(self.mcp_cfg, "params", {}) or {}
        raw = str(params.get("cwd", "")).strip()
        if raw:
            return Path(raw).expanduser()
        return self._settings.mcp_cwd

    def _configured_cdp_endpoint(self) -> str:
        params = getattr(self.mcp_cfg, "params", {}) or {}
        env_map = dict(params.get("env", {}) or {})
        return str(env_map.get("PLAYWRIGHT_MCP_CDP_ENDPOINT") or "").strip()

    def _configured_browser_binary(self) -> str:
        instance_binary = self._instance.browser_binary if self._instance else ""
        return str(instance_binary or self._settings.browser_binary).strip()

    @staticmethod
    def normalize_browser_binary(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return os.path.normcase(os.path.normpath(str(Path(raw).expanduser())))

    def _ensure_screenshots_dir(self) -> None:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_local_screenshot_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend(
                [
                    self._mcp_cwd / path,
                    Path.cwd() / path,
                    path,
                ]
            )

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return path

    def _ensure_screenshot_in_folder(self, source_path: Path) -> Path:
        if not source_path.exists() or not source_path.is_file():
            return source_path

        self._ensure_screenshots_dir()
        try:
            source_resolved = source_path.resolve()
            target_dir_resolved = self._screenshots_dir.resolve()
            try:
                source_resolved.relative_to(target_dir_resolved)
                return source_resolved
            except ValueError:
                pass

            target_path = self._screenshots_dir / source_path.name
            if target_path.exists():
                target_resolved = target_path.resolve()
                if target_resolved != source_resolved:
                    target_path = self._screenshots_dir / (
                        f"{source_path.stem}-{uuid.uuid4().hex[:8]}{source_path.suffix}"
                    )

            shutil.copy2(source_path, target_path)
            return target_path
        except Exception:
            return source_path

    def _normalize_screenshot_value(self, screenshot: Any) -> Any:
        """Normalize screenshot for downstream multimodal APIs.

        - Keep remote URLs and existing data URLs as-is.
        - Ensure local screenshots are copied into screenshots/ folder.
        - Convert local image file paths to data URLs.
        """
        if screenshot is None or not isinstance(screenshot, str):
            return screenshot

        raw = screenshot.strip()
        if not raw:
            return None

        lowered = raw.lower()
        if lowered.startswith(("http://", "https://", "data:image/")):
            return raw

        local_path_str = raw[7:] if lowered.startswith("file://") else raw
        local_path = self._resolve_local_screenshot_path(local_path_str)
        if not local_path.exists() or not local_path.is_file():
            return raw
        local_path = self._ensure_screenshot_in_folder(local_path)

        mime_type, _ = mimetypes.guess_type(str(local_path))
        if not mime_type or not mime_type.startswith("image/"):
            return raw

        try:
            encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
        except Exception:
            return raw
        return f"data:{mime_type};base64,{encoded}"

    async def request_cancel(self, session_id: str, request_id: Optional[str] = None) -> None:
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required for cancellation")
        await self._cancel_store.set(self._cancel_key(sid, request_id), "1")

        request_id_clean = (request_id or "").strip()
        if request_id_clean:
            keys = [self._inflight_key(sid, request_id_clean)]
        else:
            keys = [self._inflight_key(sid)]

        for key in keys:
            for task in list(self._inflight_tasks.get(key, set())):
                if not task.done():
                    task.cancel()

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        if request_id:
            await self._cancel_store.delete(self._cancel_key(sid, request_id))
            return
        await self._cancel_store.delete(self._cancel_key(sid, "*"))

    async def is_cancelled(self, session_id: str, request_id: Optional[str] = None) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        if request_id:
            exact = await self._cancel_store.get(self._cancel_key(sid, request_id))
            if exact is not None:
                return True
        wildcard = await self._cancel_store.get(self._cancel_key(sid, "*"))
        return wildcard is not None

    def session_new(self, session_id: Optional[str] = None) -> str:
        sid = (session_id or "").strip() or f"browser-{uuid.uuid4().hex}"
        self._sessions.add(sid)
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        return sid

    async def _open_session(self) -> DriverSession:
        settings = replace(self._settings, mcp_server=self.mcp_cfg, mcp_cwd=self._mcp_cwd)
        session = await open_session(
            self.core, process_environ(), page_state=self._page_state, instance=self._instance, settings=settings
        )
        BROWSER_SERVICE_REGISTRY.register_managed_driver(self._lifecycle_identity, self, session.assembly.provisioner)
        return session

    async def ensure_runtime_ready(self) -> None:
        """Open the driver session once; after a task release, start its stack again on the kept device."""
        if self._task_binding_ref_count <= 0:
            # Standalone BrowserAgentRuntime usage has no outer TaskTool hook.
            self.acquire_task_binding()
        self._acquire_registry_lease()
        if self.started:
            return
        if self._session is None:
            self._session = await self._open_session()
        else:
            await self._session.start()
        self._session.begin_task()
        self._ensure_screenshots_dir()
        self.started = True
        BROWSER_SERVICE_REGISTRY.activate_binding(self._lifecycle_identity, self)

    async def _restart(self) -> None:
        """Release the session after a dead transport; the next task's worker call rebuilds on a fresh one."""
        await self.reset()

    def _clear_task_scoped_state(self) -> None:
        """Drop observers and task/session state without touching the profile."""
        self._browser_agent = None
        for tasks in self._inflight_tasks.values():
            for task in tuple(tasks):
                if not task.done():
                    task.cancel()
        self._inflight_tasks.clear()
        self._locks.clear()
        self._sessions.clear()
        self._failure_context_by_session.clear()
        self._progress_by_session.clear()

    async def release_task_binding(self) -> bool:
        """Release the task's resources: the last binding stops the driver stack and keeps the device."""
        if self._task_binding_ref_count > 0:
            self._task_binding_ref_count -= 1
        if self._task_binding_ref_count > 0:
            return False
        if self._registry_acquired:
            BROWSER_SERVICE_REGISTRY.release(self._lifecycle_identity, self)
            self._registry_acquired = False
        if self.started and self._session is not None:
            await self._session.stop()
        self.started = False
        self._clear_task_scoped_state()
        return True

    async def detach_lifecycle_resources(self) -> Optional[Any]:
        """Close this service's session during a coordinated reset; returns the provisioner of its device."""
        session, self._session = self._session, None
        self.started = False
        self._browser_agent = None
        if session is None:
            return None
        await session.close()
        return session.assembly.provisioner

    @classmethod
    async def _reset_lifecycle_resources(
        cls,
        identity: BrowserServiceIdentity,
        fallback_service: Optional["BrowserService"] = None,
    ) -> bool:
        """Stop all process resources associated with one lifecycle identity."""
        snapshot = BROWSER_SERVICE_REGISTRY.begin_reset(identity)
        services = snapshot.services
        if not services and fallback_service is not None:
            services = (fallback_service,)
        drivers = list(snapshot.managed_drivers)
        for service in services:
            driver = await service.detach_lifecycle_resources()
            if driver is not None and all(existing is not driver for existing in drivers):
                drivers.append(driver)

        for driver in drivers:
            try:
                await asyncio.to_thread(driver.stop)
            except Exception:
                pass
            finally:
                BROWSER_SERVICE_REGISTRY.unregister_managed_driver(
                    identity,
                    driver,
                )
        return bool(services or drivers)

    async def reset(self) -> None:
        """Reset one shared lifecycle identity and restart lazily on next use."""
        await self._reset_lifecycle_resources(
            self._lifecycle_identity,
            fallback_service=self,
        )

    @classmethod
    async def reset_registered_managed_browser(
        cls,
        *,
        browser_key: str,
        profile_name: str,
        display_mode: str,
        browser_binary: str,
    ) -> int:
        """Stop a preserved managed device matching one logical identity."""
        identities = BROWSER_SERVICE_REGISTRY.find_managed_driver_identities(
            browser_key=str(browser_key or "").strip(),
            profile_name=str(profile_name or "").strip(),
            display_mode=str(display_mode or "").strip().lower(),
            browser_binary=cls.normalize_browser_binary(browser_binary),
        )
        reset_count = 0
        for identity in identities:
            if await cls._reset_lifecycle_resources(identity):
                reset_count += 1
        return reset_count

    async def shutdown(self) -> None:
        try:
            await self.reset()
        finally:
            self._task_binding_ref_count = 0
            await self.release_task_binding()
            BROWSER_SERVICE_REGISTRY.discard_idle(self._lifecycle_identity)
