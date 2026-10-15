# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The backend service: the driver session's lifecycle, with sticky sessions and guardrails."""

from __future__ import annotations

import asyncio
import base64
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

from ..contract import SurfaceBundle
from .assembly import new_page_state, open_session
from .config import CoreSettings, RunGuardrails, process_environ
from .driver_session import DriverSession
from .page_state import PageState
from .service_registry import SERVICE_REGISTRY
from .worker import TaskProgressState, _WorkerLoop


def _image_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class AgentService(_WorkerLoop):
    """Backend service with sticky logical sessions; holds the driver session the worker runs on."""

    def __init__(
        self,
        core: CoreSettings,
        surface: SurfaceBundle,
        *,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        guardrails: RunGuardrails,
        allowed_tool_names: Optional[Iterable[str]] = None,
        output_dir: Path,
        identity: str,
        cancel_store: Optional[BaseKVStore] = None,
        page_state: Optional[PageState] = None,
    ) -> None:
        self.core = core
        self.surface = surface
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.guardrails = guardrails
        self.identity = identity
        self._allowed_tool_names = None if allowed_tool_names is None else tuple(dict.fromkeys(allowed_tool_names))
        self._cancel_store: BaseKVStore = cancel_store or InMemoryKVStore()
        self.page_state: PageState = page_state if page_state is not None else new_page_state(surface.profile)
        self._session: Optional[DriverSession] = None

        self.started = False
        self.agent: Optional[ReActAgent] = None
        self._locks: Dict[str, asyncio.Lock] = {}
        self._sessions: set[str] = set()
        self._inflight_tasks: Dict[str, set[asyncio.Task[Any]]] = {}
        self._screenshot_subdir = "screenshots"
        self._artifacts_subdir = "artifacts"
        self._output_dir = output_dir
        self._screenshots_dir = output_dir / self._screenshot_subdir
        self._artifacts_dir = output_dir / self._artifacts_subdir
        self._failure_context_by_session: Dict[str, str] = {}
        self._progress_by_session: Dict[str, TaskProgressState] = {}
        self._registry_acquired = False
        self._task_binding_ref_count = 0

    @property
    def allowed_tool_names(self) -> Optional[tuple[str, ...]]:
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

    def _acquire_registry_lease(self) -> None:
        if self._registry_acquired:
            return
        SERVICE_REGISTRY.acquire(self.identity, self)
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
                    self._output_dir / path,
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

    def _normalize_screenshot_value(self, screenshot: Any) -> str | None:
        """The answer's screenshot as an http(s) link or an image data link, else ``None``."""
        if not isinstance(screenshot, str) or not screenshot.strip():
            return None
        raw = screenshot.strip()
        lowered = raw.lower()
        if lowered.startswith(("http://", "https://")):
            return raw
        if lowered.startswith("data:image/"):
            _, _, encoded = raw.partition(",")
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError:
                return None
            return raw if _image_mime_type(data) else None

        local_path = self._resolve_local_screenshot_path(raw[7:] if lowered.startswith("file://") else raw)
        if not local_path.is_file():
            return None
        local_path = self._ensure_screenshot_in_folder(local_path)
        try:
            data = local_path.read_bytes()
        except OSError:
            return None
        mime_type = _image_mime_type(data)
        return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}" if mime_type else None

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
        sid = (session_id or "").strip() or f"session-{uuid.uuid4().hex}"
        self._sessions.add(sid)
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        return sid

    async def _open_session(self) -> DriverSession:
        session = await open_session(
            self.core, process_environ(), page_state=self.page_state, settings=self.surface.settings
        )
        SERVICE_REGISTRY.register_managed_driver(self.identity, self, session.assembly.provisioner)
        return session

    async def ensure_runtime_ready(self) -> None:
        """Open the driver session once; after a task release, start its stack again on the kept device."""
        if self._task_binding_ref_count <= 0:
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
        SERVICE_REGISTRY.activate_binding(self.identity, self)

    async def _restart(self) -> None:
        """Release the session after a dead transport; the next task's worker call rebuilds on a fresh one."""
        await self.reset()

    def _clear_task_scoped_state(self) -> None:
        """Drop observers and task/session state without touching the profile."""
        self.agent = None
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
            SERVICE_REGISTRY.release(self.identity, self)
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
        self.agent = None
        if session is None:
            return None
        await session.close()
        return session.assembly.provisioner

    @classmethod
    async def reset_lifecycle_resources(cls, identity: str, fallback_service: Optional["AgentService"] = None) -> bool:
        """Stop all process resources associated with one identity."""
        snapshot = SERVICE_REGISTRY.begin_reset(identity)
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
                SERVICE_REGISTRY.unregister_managed_driver(identity, driver)
        return bool(services or drivers)

    async def reset(self) -> None:
        """Reset one shared identity and restart lazily on next use."""
        await self.reset_lifecycle_resources(self.identity, fallback_service=self)

    async def shutdown(self) -> None:
        try:
            await self.reset()
        finally:
            self._task_binding_ref_count = 0
            await self.release_task_binding()
            SERVICE_REGISTRY.discard_idle(self.identity)
