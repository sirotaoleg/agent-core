#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""The runtime over one driver session: the service, the factory tools and the topology that runs a task."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent.rail.base import AgentRail

from ..contract import Action, Effect, Health, SurfaceBundle
from .assembly import AgentParts, build_agent_parts, core_rails
from .config import CoreSettings, RunGuardrails
from .driver_session import DriverSession
from .page_state import PageState
from .service import AgentService
from .tool_factory import Builder, ToolContext, ToolFlags
from .topology import Topology, topology_for


@dataclass(frozen=True)
class ToolKind:
    """What the rail knows of one tool call: the command decoded from its arguments, or a helper's declared flags."""

    action: Action | None = None
    effect: Effect = Effect.ACT
    exempt_from_budget: bool = False
    progress: bool = False
    known: bool = False
    helper: bool = False
    fields: frozenset[str] = frozenset()

    @property
    def command(self) -> type[Action] | None:
        return None if self.action is None else type(self.action)

    @property
    def read_only(self) -> bool:
        return self.effect is Effect.READ


def _tool_args(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class AgentRuntime:
    """One session, the parts built on it, the rails' live builders, and the topology that runs each task."""

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
        workspace: Any = None,
        language: str = "cn",
        output_dir: Path,
        identity: str,
        cancel_store: Optional[BaseKVStore] = None,
        page_state: Optional[PageState] = None,
    ) -> None:
        self._core = core
        self._surface = surface
        self._workspace = workspace
        self._language = language
        self._allowed_tool_names = None if allowed_tool_names is None else tuple(dict.fromkeys(allowed_tool_names))
        self._capabilities: tuple[str, ...] | None = None
        self._service = AgentService(
            core,
            surface,
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            guardrails=guardrails,
            allowed_tool_names=self._allowed_tool_names,
            output_dir=output_dir,
            identity=identity,
            cancel_store=cancel_store,
            page_state=page_state,
        )
        self._topology: Topology | None = None
        self._dedicated_log = True
        self._session: DriverSession | None = None
        self._context: ToolContext | None = None
        self._parts: dict[tuple[bool, str], AgentParts] = {}
        self._builders: dict[str, Builder] = {}
        self._fields: dict[str, frozenset[str]] = {}
        self._flags: dict[str, ToolFlags] = {}

    @property
    def service(self) -> AgentService:
        return self._service

    @property
    def core(self) -> CoreSettings:
        return self._core

    @property
    def surface(self) -> SurfaceBundle:
        return self._surface

    @property
    def dedicated_log(self) -> bool:
        """Whether the agent's invokes write to the dedicated log file; the runner's own agent uses the process log."""
        return self._dedicated_log

    @dedicated_log.setter
    def dedicated_log(self, value: bool) -> None:
        self._dedicated_log = value

    @property
    def workspace(self) -> Any:
        return self._workspace

    @property
    def language(self) -> str:
        return self._language

    @property
    def capabilities(self) -> tuple[str, ...]:
        """The catalog entries whose tools are all allowed; the default entry alone when no allowlist was given."""
        if self._capabilities is None:
            catalog = self._surface.profile.capabilities
            allowed = set(catalog[0].tool_names if self._allowed_tool_names is None else self._allowed_tool_names)
            self._capabilities = tuple(entry.name for entry in catalog if set(entry.tool_names) <= allowed)
        return self._capabilities

    @property
    def builders(self) -> Mapping[str, Builder]:
        """The decoders of every factory tool built so far; the policy rail reads it live."""
        return self._builders

    def core_rails(self) -> tuple[AgentRail, ...]:
        return core_rails(self._surface.profile, self._builders, self.page_state)

    @property
    def page_state(self) -> PageState:
        return self._service.page_state

    @property
    def session(self) -> DriverSession | None:
        return self._session

    @property
    def context(self) -> ToolContext | None:
        """The tool context of the started session, for the runtime's own calls."""
        return self._context

    @property
    def topology(self) -> Topology | None:
        return self._topology

    async def metadata(self) -> tuple[Mapping[str, Any], str | None]:
        """The profile's metadata of the current context, ``({}, None)`` for a surface without the hook."""
        hook = self._surface.profile.page_metadata
        return ({}, None) if hook is None else await hook(self._context)

    def tool_kind(self, tool_name: str, tool_args: Any) -> ToolKind:
        """The command ``tool_args`` decode to on a factory tool, or a helper's flags; unknown tools act."""
        build = self._builders.get(tool_name)
        if build is not None:
            fields = self._fields.get(tool_name, frozenset())
            try:
                action = build(_tool_args(tool_args), self.page_state)
            except ValueError:
                return ToolKind(progress=True, known=True, fields=fields)
            return ToolKind(action=action, effect=action.effect, progress=True, known=True, fields=fields)
        flags = self._flags.get(tool_name)
        if flags is None:
            return ToolKind()
        return ToolKind(
            effect=flags.effect,
            exempt_from_budget=flags.exempt_from_budget,
            progress=flags.progress,
            known=True,
            helper=True,
        )

    async def cancel_run(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.request_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.clear_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def ensure_runtime_ready(self) -> None:
        """Make sure the service's driver session runs; a new session gets a fresh tool context and parts."""
        await self._service.ensure_runtime_ready()
        session = self._service.session
        if session is self._session:
            return
        self._drop_session()
        profile = session.assembly.surface.profile
        self._context = ToolContext(
            session,
            self.page_state,
            profile.renderer,
            session.assembly.stack.info,
            profile.settings,
            False,
            self._workspace,
            self._language,
        )
        self._session = session

    def agent_parts(self, *, image_support: bool, language: str) -> AgentParts:
        """The factory tools of the started session in the receiving agent's language, once per key."""
        parts = self._parts.get((image_support, language))
        if parts is None:
            session = self._session
            parts = build_agent_parts(
                session,
                session.assembly.surface,
                capabilities=self.capabilities,
                image_support=image_support,
                language=language,
                workspace=self._workspace,
            )
            self._parts[(image_support, language)] = parts
            self._builders.update(parts.builders)
            for tool in parts.tools:
                flags = getattr(tool, "flags", None)
                if isinstance(flags, ToolFlags):
                    self._flags[tool.card.name] = flags
                elif tool.card.name in parts.builders:
                    properties = (tool.card.input_params or {}).get("properties") or {}
                    self._fields[tool.card.name] = frozenset(properties)
        return parts

    def _drop_session(self) -> None:
        self._session = None
        self._context = None
        self._parts = {}

    async def acquire_task_resources(self) -> None:
        """Acquire one task reference before invoking a reusable subagent."""
        self._service.acquire_task_binding()

    async def ensure_started(self) -> None:
        """The runner's agent, by the topology: the worker through the service, or the main agent on this runtime."""
        if self._topology is None:
            self._topology = topology_for(self._core)
        await self._topology.start(self)

    async def run_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run one task through the topology; the answer carries the context the task ended in."""
        await self.ensure_started()
        answer = await self._topology.run(self, task, session_id, request_id, timeout_s)
        answer["context"] = await self._answer_context(bool(answer.get("ok")))
        return answer

    async def _answer_context(self, ok: bool) -> Dict[str, Any]:
        """After a successful run the profile's metadata, then the surface identity of the current context."""
        metadata: Mapping[str, Any] = {}
        if ok and self._context is not None:
            metadata, _ = await self.metadata()
        context = self.page_state.surface_context
        return {"identity": "" if context is None else context.identity, "metadata": dict(metadata)}

    def main_agent_answer(
        self, session_id: str, request_id: str, *, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None
    ) -> Dict[str, Any]:
        """The runner's answer dict from a main-agent run: the output, and status when the rail reported one."""
        result = result or {}
        payload = result.get("authoritative_browser_result")
        progress = result.get("progress_state")
        error = error if error is not None else result.get("error")
        answer: Dict[str, Any] = {
            "ok": not error and result.get("result_type") != "error",
            "session_id": session_id,
            "request_id": request_id,
            "final": str(result.get("output") or ""),
            "screenshot": None,
            "error": error or None,
        }
        status = (payload or {}).get("status") or (progress or {}).get("status")
        if status:
            answer["status"] = status
        if progress:
            answer["progress"] = progress
        return answer

    async def runtime_health(self) -> Dict[str, Any]:
        session = self._service.session
        if session is not None and self._service.started:
            health = await session.health()
        else:
            health = Health(ok=False, detail="not started")
        return {
            "ok": health.ok,
            "started": bool(self._service.started),
            "detail": health.detail,
            "provider": self._service.provider,
            "api_base": self._service.api_base,
            "model_name": self._service.model_name,
        }

    async def shutdown(self) -> None:
        try:
            await self._service.shutdown()
        finally:
            self._drop_session()
            if self._topology is not None:
                self._topology.drop()
                self._topology = None
            await OpenAIModelClient.aclose()

    async def release_task_resources(self) -> bool:
        """Release one task binding; the last one stops the driver stack, keeps the device and resets the state."""
        fully_released = await self._service.release_task_binding()
        if fully_released:
            self.page_state.reset()
        return fully_released

    async def reset(self) -> None:
        """Release the current session and restart lazily on the next task."""
        try:
            await self._service.reset()
        finally:
            self._drop_session()
            self.page_state.reset()
