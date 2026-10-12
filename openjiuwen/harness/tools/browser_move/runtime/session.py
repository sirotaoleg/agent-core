#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""The browser agent's runtime: the driver session and the factory tools."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional
from weakref import WeakSet

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail

from ..contract import Action, DriverError, Effect, Health, Observe, SurfaceBundle
from ..surfaces.browser.capabilities import CORE_BROWSER_TOOL_NAMES, capabilities_of
from ..surfaces.browser.helpers import activate_page, page_metadata, probe_for_policy, register_custom_actions
from ..surfaces.browser.semantic.state import SemanticStateTracker
from .assembly import AgentParts, assemble_surface, build_agent_parts, core_rails
from .browser_logging import write_browser_agent_audit_artifact
from .config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
    CoreSettings,
    SurfaceSettings,
    browser_surface_settings,
    process_environ,
)
from .controllers import ActionController, BaseController
from .driver_session import DriverSession
from .page_state import BrowserPageState, BrowserTarget
from .service import BrowserService
from .tool_factory import Builder, ToolContext, ToolFlags
from .topology import Topology, topology_for

_ACTIVE_BROWSER_RUNTIMES: WeakSet[Any] = WeakSet()


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


class BrowserAgentRuntime:
    """Runtime kernel for browser lifecycle and deterministic helper actions."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
        instance: Optional[BrowserInstanceConfig] = None,
        allowed_tool_names: Optional[Iterable[str]] = None,
        *,
        core_settings: Optional[CoreSettings] = None,
        browser_settings: Optional[SurfaceSettings] = None,
        workspace: Any = None,
        language: str = "cn",
    ) -> None:
        self._instance = instance
        self._core_settings = core_settings
        self._browser_settings = browser_settings or browser_surface_settings()
        self._workspace = workspace
        self._language = language
        resolved_allowed_tool_names = (
            CORE_BROWSER_TOOL_NAMES if allowed_tool_names is None else tuple(dict.fromkeys(allowed_tool_names))
        )
        self._capabilities = capabilities_of(resolved_allowed_tool_names)
        self._page_state = BrowserPageState()
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
            instance=instance,
            allowed_tool_names=resolved_allowed_tool_names,
            browser_settings=self._browser_settings,
            core_settings=core_settings,
            page_state=self._page_state,
        )
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._controller: BaseController = ActionController()
        self._topology: Topology | None = None
        self._surface: SurfaceBundle | None = None
        self._dedicated_log = True
        self._session: DriverSession | None = None
        self._context: ToolContext | None = None
        self._parts: dict[tuple[bool, str], AgentParts] = {}
        self._builders: dict[str, Builder] = {}
        self._fields: dict[str, frozenset[str]] = {}
        self._flags: dict[str, ToolFlags] = {}
        self._semantic_state_tracker = SemanticStateTracker()
        self.observations_reused = 0
        _ACTIVE_BROWSER_RUNTIMES.add(self)

    @property
    def service(self) -> BrowserService:
        return self._service

    @property
    def core_settings(self) -> Optional[CoreSettings]:
        return self._core_settings

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
    def capabilities(self) -> tuple[str, ...]:
        return self._capabilities

    @property
    def surface(self) -> SurfaceBundle:
        """The surface bundle of the core settings; built once, before any driver, for the rails' profile constants."""
        if self._surface is None:
            self._surface = assemble_surface(
                self._service.core, process_environ(), instance=self._instance, settings=self._browser_settings
            )
        return self._surface

    @property
    def builders(self) -> Mapping[str, Builder]:
        """The decoders of every factory tool built so far; the policy rail reads it live."""
        return self._builders

    def core_rails(self) -> tuple[AgentRail, ...]:
        return core_rails(self.surface.profile, self._builders, self._page_state)

    @property
    def controller(self) -> BaseController:
        return self._controller

    @property
    def page_state(self) -> BrowserPageState:
        return self._page_state

    @property
    def session(self) -> DriverSession | None:
        return self._session

    @property
    def page_id(self) -> str:
        return self._page_state.page_id

    @property
    def generation_id(self) -> str:
        return self._page_state.generation_id

    def export_page_state(self) -> Dict[str, Any]:
        return self._page_state.export()

    def set_task_requested_fields(self, fields: Iterable[str]) -> None:
        """Keep PageState projection scoped to fields requested by this task."""

        self._page_state.set_requested_fields(fields)

    def resolve_model_target_id(self, target_id: str) -> BrowserTarget:
        """Resolve or safely refresh a runtime-owned model target."""

        page_state = self._page_state
        normalized = str(target_id or "").strip()
        target = page_state.get_target(normalized)
        if target is None:
            raise ValueError(
                f"Unknown PageState target_id {normalized}; current generation is "
                f"{page_state.generation_id}."
            )
        if target.generation != page_state.generation:
            target = page_state.get_target(page_state.refresh_target_id(normalized))
        if target is None:
            raise ValueError(
                f"PageState target_id {normalized} could not be refreshed in "
                f"{page_state.generation_id}."
            )
        return target

    def target_recovery_state(self, target_id: str) -> Dict[str, Any]:
        """Return bounded current-generation recovery details for one target."""

        page_state = self._page_state
        return {
            "current_generation": page_state.generation_id,
            "candidate_fresh_targets": page_state.recovery_candidates(target_id, limit=5),
        }

    def resolve_primary_link(self, tool_args: Any) -> str:
        parsed = _tool_args(tool_args)
        explicit = str(parsed.get("primary_link") or parsed.get("href") or parsed.get("url") or "").strip()
        if explicit:
            return explicit
        target_id = str(parsed.get("target_id") or parsed.get("target") or "").strip()
        if target_id.startswith("t_g"):
            try:
                target = self.resolve_model_target_id(target_id)
            except ValueError:
                target = None
            if target is not None and target.href:
                return target.href
        page_state = self._page_state
        for key in ("selector", "target"):
            selector = str(parsed.get(key) or "").strip()
            generation_and_href = page_state.selector_primary_links.get(selector)
            if generation_and_href and generation_and_href[0] == page_state.generation:
                return generation_and_href[1]
        return ""

    def tool_kind(self, tool_name: str, tool_args: Any) -> ToolKind:
        """The command ``tool_args`` decode to on a factory tool, or a helper's flags; unknown tools act."""
        build = self._builders.get(tool_name)
        if build is not None:
            fields = self._fields.get(tool_name, frozenset())
            try:
                action = build(_tool_args(tool_args), self._page_state)
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

    def register_runtime_tools(self) -> None:
        """The two custom-action tools in ``Runner.resource_mgr``, once."""
        if self._browser_custom_action_tool is not None:
            return
        from .runtime_tools import BrowserCustomActionTool, BrowserListActionsTool

        self._browser_custom_action_tool = BrowserCustomActionTool(self)
        self._browser_list_actions_tool = BrowserListActionsTool(self)
        self._register_runtime_tool(self._browser_custom_action_tool, tool_name="browser_custom_action")
        self._register_runtime_tool(self._browser_list_actions_tool, tool_name="browser_list_custom_actions")

    @staticmethod
    def _register_runtime_tool(tool_obj: Any, *, tool_name: str) -> None:
        add_result = Runner.resource_mgr.add_tool(tool_obj, tag="agent.playwright.runtime")
        if add_result is None or getattr(add_result, "is_ok", lambda: False)():
            return
        error_value = getattr(add_result, "value", add_result)
        if "already exist" in str(error_value):
            return
        raise RuntimeError(f"Failed to register {tool_name} tool: {error_value}")

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
        """Make sure the service's driver session runs; the custom actions run on it from then on."""
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        await self._service.ensure_runtime_ready()
        session = self._service.session
        if session is self._session:
            return
        self._drop_session()
        profile = session.assembly.surface.profile
        self._context = ToolContext(
            session,
            self._page_state,
            profile.renderer,
            session.assembly.stack.info,
            profile.settings,
            False,
            self._workspace,
            self._language,
        )
        self._session = session
        self._controller.register_builtin_actions()
        register_custom_actions(self._controller, self._context, upload_root=self._browser_settings.upload_root)

    def agent_parts(self, *, image_support: bool, language: str) -> AgentParts:
        """The factory tools of the started session in the receiving agent's language, once per key."""
        parts = self._parts.get((image_support, language))
        if parts is None:
            session = self._session
            parts = build_agent_parts(
                session,
                session.assembly.surface,
                capabilities=self._capabilities,
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

    async def capture_browser_state(self, *, action_group_id: str = "") -> Dict[str, Any]:
        """The observation the next model call reads: the last one while it is current, else a fresh one."""
        await self.ensure_runtime_ready()
        page_state = self._page_state
        observation = page_state.fresh_observation
        dom_error = None
        snapshot_audit: Dict[str, Any] = {}
        if observation is None:
            try:
                observation = await self._context.observe(Observe())
            except DriverError as exc:
                dom_error = f"page observation failed: {exc}"
                logger.warning("[BrowserAgentRuntime] page observation failed: %s", exc, exc_info=True)
        else:
            self.observations_reused += 1
        if observation is not None:
            snapshot_audit = write_browser_agent_audit_artifact("ax_snapshot", observation.native_text or "")

        metadata, metadata_error = await page_metadata(self._context)
        exported = page_state.export()

        errors = [error for error in (dom_error, metadata_error) if error]
        semantic_state = metadata.get("semantic_state")
        if not isinstance(semantic_state, dict):
            semantic_state = {}
        semantic_state.update(
            {
                "url": metadata.get("url") or exported["url"],
                "field_coverage": sorted(page_state.field_coverage),
            }
        )
        semantic_tracker = self._semantic_state_tracker
        if metadata_error:
            semantic_progress = semantic_tracker.latest
            semantic_progress.update(
                {
                    "progress": "unknown",
                    "observable_progress": False,
                    "capture_error": metadata_error,
                }
            )
        else:
            semantic_progress = semantic_tracker.observe(
                semantic_state,
                action_group_id=action_group_id,
            )
        semantic_state = self._with_semantic_provenance(
            semantic_progress,
            fallback_state=semantic_state,
        )
        return {
            "ok": not errors,
            "error": "; ".join(errors) or None,
            "url": metadata.get("url") or exported["url"],
            "title": metadata.get("title") or exported["title"],
            "tabs": metadata.get("tabs") or [],
            "page_position": metadata.get("page_position") or {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state.get("field_coverage") or [],
            "page_state": exported,
            "dom": "",
            "dom_error": dom_error,
            "audit": {"ax_snapshot": snapshot_audit} if snapshot_audit else {},
        }

    async def capture_reconciliation_browser_state(self, *, action_group_id: str) -> Dict[str, Any]:
        """Reconcile an ambiguous mutation from the page metadata alone, without observing."""

        await self.ensure_runtime_ready()
        metadata, metadata_error = await page_metadata(self._context)
        page_state = self.export_page_state()
        semantic_state = metadata.get("semantic_state")
        if not isinstance(semantic_state, dict):
            semantic_state = {}
        semantic_state.update(
            {
                "url": metadata.get("url") or page_state.get("url") or "",
                "field_coverage": page_state.get("field_coverage") or [],
            }
        )
        tracker = self._semantic_state_tracker
        if metadata_error:
            semantic_progress = tracker.latest
            semantic_progress.update(
                {
                    "progress": "unknown",
                    "observable_progress": False,
                    "capture_error": metadata_error,
                }
            )
        else:
            semantic_progress = tracker.observe(semantic_state, action_group_id=action_group_id)
        semantic_state = self._with_semantic_provenance(
            semantic_progress,
            fallback_state=semantic_state,
        )
        return {
            "ok": not metadata_error,
            "error": metadata_error,
            "url": metadata.get("url") or page_state.get("url") or "",
            "title": metadata.get("title") or page_state.get("title") or "",
            "tabs": metadata.get("tabs") or [],
            "page_position": metadata.get("page_position") or {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state.get("field_coverage") or [],
            "page_state": page_state,
            "dom": "",
            "dom_error": None,
            "reconciliation_only": True,
        }

    async def capture_compact_browser_state(self, *, action_group_id: str) -> Dict[str, Any]:
        """Merge completed read-only observations without another browser round trip."""
        page_state = self.export_page_state()
        semantic_tracker = self._semantic_state_tracker
        semantic_state = semantic_tracker.current_state
        semantic_state.update(
            {
                "url": page_state.get("url") or semantic_state.get("url") or "",
                "field_coverage": page_state.get("field_coverage") or semantic_state.get("field_coverage") or [],
            }
        )
        semantic_progress = semantic_tracker.observe(
            semantic_state,
            action_group_id=action_group_id,
        )
        semantic_state = self._with_semantic_provenance(
            semantic_progress,
            fallback_state=semantic_state,
        )
        return {
            "ok": True,
            "error": None,
            "url": page_state.get("url") or "",
            "title": page_state.get("title") or "",
            "tabs": [],
            "page_position": {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state["field_coverage"],
            "page_state": page_state,
            "dom": "",
            "dom_error": None,
        }

    def _with_semantic_provenance(
        self,
        semantic_progress: Dict[str, Any],
        *,
        fallback_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        tracked_state = semantic_progress.get("semantic_state")
        semantic_state = dict(tracked_state) if isinstance(tracked_state, dict) else dict(fallback_state)
        semantic_state["generation_id"] = self.generation_id
        semantic_progress["semantic_state"] = semantic_state
        return semantic_state

    async def acquire_task_resources(self) -> None:
        """Acquire one task reference before invoking a reusable subagent."""
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        self._service.acquire_task_binding()

    async def ensure_started(self) -> None:
        """The runner's agent, by the topology: the worker through the service, or the main agent on this facade."""
        if self._topology is None:
            self._topology = topology_for(self._service.core)
        await self._topology.start(self)

    async def run_browser_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        return await self._topology.run(self, task, session_id, request_id, timeout_s)

    def main_agent_answer(
        self, session_id: str, request_id: str, *, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None
    ) -> Dict[str, Any]:
        """The runner's answer dict from a main-agent run: the page from the page state, status when reported."""
        result = result or {}
        surface = self._page_state.surface_context
        payload = result.get("authoritative_browser_result")
        progress = result.get("progress_state")
        error = error if error is not None else result.get("error")
        answer: Dict[str, Any] = {
            "ok": not error and result.get("result_type") != "error",
            "session_id": session_id,
            "request_id": request_id,
            "final": str(result.get("output") or ""),
            "page": {"url": surface.url if surface else "", "title": surface.title if surface else ""},
            "screenshot": None,
            "error": error or None,
        }
        status = (payload or {}).get("status") or (progress or {}).get("status")
        if status:
            answer["status"] = status
        if progress:
            answer["progress"] = progress
        return answer

    async def run_custom_action(
        self,
        *,
        action: str,
        session_id: str = "",
        request_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_runtime_ready()
        self._controller.bind_runtime(self)
        return await self._controller.run_action(
            action=action,
            session_id=session_id,
            request_id=request_id,
            **(params or {}),
        )

    async def probe_for_policy(self, source: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run a policy-owned page function and register its elements as PageState targets."""
        await self.ensure_runtime_ready()
        return await probe_for_policy(self._context, source, params)

    async def activate_page(self, url: str) -> bool:
        """Bring the page at ``url`` (else the current one) to the front so its timers run and its menus render."""
        await self.ensure_runtime_ready()
        return await activate_page(self._context, url)

    async def list_actions(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "actions": self._controller.list_actions(),
            "details": self._controller.describe_actions(),
        }

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
            _ACTIVE_BROWSER_RUNTIMES.discard(self)
            self._drop_session()
            if self._topology is not None:
                self._topology.drop()
                self._topology = None
            # The next asyncio.run loop must not find clients bound to this one (the runner runs one loop per task).
            await OpenAIModelClient.aclose()

    async def release_task_resources(self) -> None:
        """Release task bindings while preserving Chrome and its profile."""
        fully_released = await self._service.release_task_binding()
        if fully_released:
            self._page_state.reset()
            self._semantic_state_tracker.reset()

    async def reset(self) -> None:
        """Release the current browser and restart lazily on the next task."""
        try:
            await self._service.reset()
        finally:
            self._drop_session()
            self._page_state.reset()
            self._semantic_state_tracker.reset()

    @property
    def semantic_progress(self) -> Dict[str, Any]:
        """Return the latest task-local semantic progress observation."""
        return self._semantic_state_tracker.latest

    def acknowledge_semantic_replan(self) -> None:
        """Acknowledge one materially different action selected after replanning."""
        self._semantic_state_tracker.acknowledge_replan()

    def reset_semantic_task(self) -> None:
        """Start semantic loop tracking for a new user task without resetting Chrome."""
        self._semantic_state_tracker.reset()


async def reset_active_browser_runtimes() -> int:
    """Reset every live browser runtime owned by this process."""
    runtimes = list(_ACTIVE_BROWSER_RUNTIMES)
    if not runtimes:
        return 0

    results = await asyncio.gather(
        *(runtime.reset() for runtime in runtimes),
        return_exceptions=True,
    )
    reset_count = 0
    for runtime, result in zip(runtimes, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Failed to reset active browser runtime %s: %s",
                id(runtime),
                result,
            )
            continue
        reset_count += 1
    return reset_count


async def reset_managed_browser_runtime(
    *,
    browser_key: str = "",
    profile_name: str = "jiuwenclaw",
    display_mode: str,
    browser_binary: str = "",
) -> int:
    """Reset only the configured managed browser, including its idle handle."""
    normalized_binary = BrowserService.normalize_browser_binary(browser_binary)
    normalized_key = str(browser_key or "").strip()
    normalized_profile = str(profile_name or "").strip()
    normalized_mode = str(display_mode or "").strip().lower()

    reset_count = 0
    for runtime in list(_ACTIVE_BROWSER_RUNTIMES):
        identity = runtime.service.lifecycle_identity
        matches_browser = identity.browser_key == normalized_key
        matches_profile = identity.profile_name == normalized_profile
        matches_mode = identity.display_mode == normalized_mode
        matches_binary = identity.browser_binary == normalized_binary
        if identity.driver_mode != "managed":
            continue
        if not matches_browser or not matches_profile:
            continue
        if not matches_mode or not matches_binary:
            continue
        try:
            await runtime.reset()
        except Exception as exc:
            logger.warning(
                "Failed to reset managed browser runtime %s: %s",
                id(runtime),
                exc,
            )
        else:
            reset_count += 1

    reset_count += await BrowserService.reset_registered_managed_browser(
        browser_key=normalized_key,
        profile_name=normalized_profile,
        display_mode=normalized_mode,
        browser_binary=normalized_binary,
    )
    return reset_count
