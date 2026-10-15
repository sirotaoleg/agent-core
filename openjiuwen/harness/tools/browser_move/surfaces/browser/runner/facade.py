# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The browser facade over the core runtime: the browser settings, the lifecycle identity and the browser methods."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from weakref import WeakSet

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.harness.tools.browser_move.contract import DriverError, Observe, SurfaceSettings
from openjiuwen.harness.tools.browser_move.runtime.assembly import assemble_surface
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings, RunGuardrails, process_environ
from openjiuwen.harness.tools.browser_move.runtime.page_state import Target
from openjiuwen.harness.tools.browser_move.runtime.service import AgentService
from openjiuwen.harness.tools.browser_move.runtime.service_registry import SERVICE_REGISTRY
from openjiuwen.harness.tools.browser_move.runtime.session import AgentRuntime, _tool_args
from openjiuwen.harness.tools.browser_move.runtime.topology import DelegatedWorker

from .actions import ActionController, BaseController
from .logging import configure_browser_agent_logging, write_browser_agent_audit_artifact
from .settings import BrowserInstanceConfig, browser_surface_settings

_ACTIVE_BROWSER_RUNTIMES: WeakSet[Any] = WeakSet()
_PROVISIONINGS = ("remote", "managed", "extension")


@dataclass(frozen=True)
class BrowserServiceIdentity:
    """Stable process-level identity for one logical browser service; ``key`` is the registry's string for it."""

    browser_key: str
    profile_name: str
    driver_mode: str
    display_mode: str
    managed_args: tuple[str, ...]
    browser_binary: str
    user_data_dir: str
    cdp_endpoint: str
    server_id: str

    @property
    def key(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True)

    @classmethod
    def from_key(cls, key: str) -> BrowserServiceIdentity:
        fields = json.loads(key)
        return cls(**{**fields, "managed_args": tuple(fields["managed_args"])})


def normalize_browser_binary(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.normcase(os.path.normpath(str(Path(raw).expanduser())))


def _with_workspace(settings: Any, workspace: Any) -> Any:
    if workspace is None or not hasattr(settings, "workspace_root"):
        return settings
    root = getattr(workspace, "root_path", workspace)
    return replace(settings, workspace_root=Path(root).expanduser().resolve())


def _driver_mode(instance: BrowserInstanceConfig | None, settings: Any) -> str:
    explicit = (instance.driver_mode or "").strip().lower() if instance else ""
    if not explicit:
        return settings.provisioning
    if explicit not in _PROVISIONINGS:
        raise ValueError(
            "BrowserInstanceConfig.provisioning (driver_mode) must be one of: remote, managed, extension "
            f"(got {explicit!r}); leave it empty to use BROWSER_PROVISIONING"
        )
    return explicit


def _output_dir(mcp_cfg: McpServerConfig, settings: Any) -> Path:
    params = getattr(mcp_cfg, "params", {}) or {}
    raw = str(params.get("cwd", "")).strip()
    return Path(raw).expanduser() if raw else settings.mcp_cwd


def _identity(
    instance: BrowserInstanceConfig | None, settings: Any, mcp_cfg: McpServerConfig, driver_mode: str
) -> BrowserServiceIdentity:
    """The identity after the instance is applied to the settings: profile, user data dir and binary are theirs."""
    headless = any(arg == "--headless" or arg.startswith("--headless=") for arg in settings.managed_args)
    display_mode = ("headless" if headless else "headed") if driver_mode == "managed" else driver_mode
    user_data_dir = str(settings.user_data_dir).strip()
    params = getattr(mcp_cfg, "params", {}) or {}
    env_map = dict(params.get("env", {}) or {})
    return BrowserServiceIdentity(
        browser_key=instance.sanitized_key() if instance else "",
        profile_name=settings.profile_name,
        driver_mode=driver_mode,
        display_mode=display_mode,
        managed_args=settings.managed_args,
        browser_binary=normalize_browser_binary(settings.browser_binary),
        user_data_dir=(
            os.path.normcase(os.path.normpath(str(Path(user_data_dir).expanduser())))
            if user_data_dir
            else f"profile:{settings.profile_name}"
        ),
        cdp_endpoint=str(env_map.get("PLAYWRIGHT_MCP_CDP_ENDPOINT") or "").strip(),
        server_id=(mcp_cfg.server_id or "").strip() or mcp_cfg.server_name,
    )


class BrowserAgentRuntime(AgentRuntime):
    """The browser runner: the core runtime over the browser surface, plus the browser views its rails read."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: RunGuardrails,
        instance: Optional[BrowserInstanceConfig] = None,
        allowed_tool_names: Optional[Iterable[str]] = None,
        *,
        core_settings: Optional[CoreSettings] = None,
        browser_settings: Optional[SurfaceSettings] = None,
        workspace: Any = None,
        language: str = "cn",
    ) -> None:
        core = core_settings or CoreSettings.from_process_env()
        driver = (instance.driver if instance else "").strip()
        if driver:
            core = replace(core, driver=driver)
        settings = _with_workspace(browser_settings or browser_surface_settings(), workspace)
        driver_mode = _driver_mode(instance, settings)
        if instance is not None:
            settings = settings.with_instance(instance)
        output_dir = _output_dir(mcp_cfg, settings)
        settings = replace(settings, mcp_server=mcp_cfg, mcp_cwd=output_dir)
        self._lifecycle_identity = _identity(instance, settings, mcp_cfg, driver_mode)
        configure_browser_agent_logging(core)
        super().__init__(
            core,
            assemble_surface(core, process_environ(), settings=settings),
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            guardrails=guardrails,
            allowed_tool_names=allowed_tool_names,
            workspace=workspace,
            language=language,
            output_dir=output_dir,
            identity=self._lifecycle_identity.key,
        )
        self.mcp_cfg = mcp_cfg
        self._custom_action_tool = None
        self._list_actions_tool = None
        self._controller: BaseController = ActionController()
        self._bound_session: Any = None
        self._tracker: Any = None
        self._semantic_mark: tuple[int, Any] | None = None
        self.observations_reused = 0
        _ACTIVE_BROWSER_RUNTIMES.add(self)

    @property
    def core_settings(self) -> CoreSettings:
        return self.core

    @property
    def lifecycle_identity(self) -> BrowserServiceIdentity:
        return self._lifecycle_identity

    @property
    def controller(self) -> BaseController:
        return self._controller

    @property
    def page_id(self) -> str:
        return self.page_state.page_id

    @property
    def generation_id(self) -> str:
        return self.page_state.generation_id

    def export_page_state(self) -> Dict[str, Any]:
        return self.page_state.export()

    @property
    def _semantic_state_tracker(self) -> Any:
        if self._tracker is None:
            factory = self.surface.profile.semantic_tracker
            self._tracker = _NoSemanticState() if factory is None else factory()
        return self._tracker

    def _semantic_changed(self) -> bool:
        """Whether the generation or the last observation moved since the previous semantic observation."""
        mark = (self.page_state.generation, self.page_state.last_observation)
        previous, self._semantic_mark = self._semantic_mark, mark
        return previous is not None and mark != previous

    def set_task_requested_fields(self, fields: Iterable[str]) -> None:
        """Keep PageState projection scoped to fields requested by this task."""

        self.page_state.set_requested_fields(fields)

    def resolve_model_target_id(self, target_id: str) -> Target:
        """Resolve or safely refresh a runtime-owned model target."""

        page_state = self.page_state
        normalized = str(target_id or "").strip()
        target = page_state.get_target(normalized)
        if target is None:
            raise ValueError(
                f"Unknown PageState target_id {normalized}; current generation is {page_state.generation_id}."
            )
        if target.generation != page_state.generation:
            target = page_state.get_target(page_state.refresh_target_id(normalized))
        if target is None:
            raise ValueError(f"PageState target_id {normalized} could not be refreshed in {page_state.generation_id}.")
        return target

    def target_recovery_state(self, target_id: str) -> Dict[str, Any]:
        """Return bounded current-generation recovery details for one target."""

        page_state = self.page_state
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
        page_state = self.page_state
        for key in ("selector", "target"):
            selector = str(parsed.get(key) or "").strip()
            generation_and_href = page_state.selector_primary_links.get(selector)
            if generation_and_href and generation_and_href[0] == page_state.generation:
                return generation_and_href[1]
        return ""

    def register_runtime_tools(self) -> None:
        """The two custom-action tools in ``Runner.resource_mgr``, once."""
        if self._custom_action_tool is not None:
            return
        from .tools import BrowserCustomActionTool, BrowserListActionsTool

        self._custom_action_tool = BrowserCustomActionTool(self)
        self._list_actions_tool = BrowserListActionsTool(self)
        self._register_runtime_tool(self._custom_action_tool, tool_name="browser_custom_action")
        self._register_runtime_tool(self._list_actions_tool, tool_name="browser_list_custom_actions")

    @staticmethod
    def _register_runtime_tool(tool_obj: Any, *, tool_name: str) -> None:
        add_result = Runner.resource_mgr.add_tool(tool_obj, tag="agent.playwright.runtime")
        if add_result is None or getattr(add_result, "is_ok", lambda: False)():
            return
        error_value = getattr(add_result, "value", add_result)
        if "already exist" in str(error_value):
            return
        raise RuntimeError(f"Failed to register {tool_name} tool: {error_value}")

    async def ensure_runtime_ready(self) -> None:
        """The core's session, with the custom actions registered on the controller for a new session."""
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        await super().ensure_runtime_ready()
        if self.session is self._bound_session:
            return
        self._bound_session = self.session
        self._controller.register_builtin_actions()
        custom_actions = self.surface.profile.custom_actions
        if custom_actions is not None:
            custom_actions(self._controller, self.context)

    async def acquire_task_resources(self) -> None:
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        await super().acquire_task_resources()

    async def ensure_started(self) -> None:
        """The core's start; the delegated worker's host gets the two custom-action tools."""
        await super().ensure_started()
        if isinstance(self.topology, DelegatedWorker):
            self.register_runtime_tools()

    async def run_browser_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self.run_task(task, session_id, request_id, timeout_s)

    def main_agent_answer(
        self, session_id: str, request_id: str, *, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None
    ) -> Dict[str, Any]:
        """The core's answer plus ``page``: the location from the browser context of the state."""
        answer = super().main_agent_answer(session_id, request_id, result=result, error=error)
        surface = self.page_state.surface_context
        answer["page"] = {"url": surface.url if surface else "", "title": surface.title if surface else ""}
        return answer

    async def capture_browser_state(self, *, action_group_id: str = "") -> Dict[str, Any]:
        """The observation the next model call reads: the last one while it is current, else a fresh one."""
        await self.ensure_runtime_ready()
        page_state = self.page_state
        observation = page_state.fresh_observation
        dom_error = None
        snapshot_audit: Dict[str, Any] = {}
        if observation is None:
            try:
                observation = await self.context.observe(Observe())
            except DriverError as exc:
                dom_error = f"page observation failed: {exc}"
                logger.warning("[BrowserAgentRuntime] observation failed: %s", exc, exc_info=True)
        else:
            self.observations_reused += 1
        if observation is not None:
            snapshot_audit = write_browser_agent_audit_artifact("ax_snapshot", observation.native_text or "")

        metadata, metadata_error = await self.metadata()
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
                changed=self._semantic_changed(),
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
        """Reconcile an ambiguous mutation from the surface metadata alone, without observing."""

        await self.ensure_runtime_ready()
        metadata, metadata_error = await self.metadata()
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
            semantic_progress = tracker.observe(
                semantic_state, action_group_id=action_group_id, changed=self._semantic_changed()
            )
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
        """Merge completed read-only observations without another driver round trip."""
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
            changed=self._semantic_changed(),
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
        """Run a policy-owned probe script and register its elements as PageState targets."""
        await self.ensure_runtime_ready()
        probe = self.surface.profile.policy_probe
        if probe is None:
            error = f"surface {self.surface.profile.name!r} has no policy probe"
            return {"ok": False, "error": error, "elements": [], "page_state": self.export_page_state()}
        return await probe(self.context, source, params)

    async def activate_page(self, url: str) -> bool:
        """Bring the context at ``url`` (else the current one) to the front so its timers run and its menus render."""
        await self.ensure_runtime_ready()
        activate = self.surface.profile.activate
        return False if activate is None else await activate(self.context, url)

    async def list_actions(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "actions": self._controller.list_actions(),
            "details": self._controller.describe_actions(),
        }

    async def shutdown(self) -> None:
        try:
            await super().shutdown()
        finally:
            _ACTIVE_BROWSER_RUNTIMES.discard(self)
            self._bound_session = None

    async def release_task_resources(self) -> bool:
        """Release task bindings while preserving Chrome and its profile."""
        fully_released = await super().release_task_resources()
        if fully_released:
            self._semantic_state_tracker.reset()
        return fully_released

    async def reset(self) -> None:
        try:
            await super().reset()
        finally:
            self._bound_session = None
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


class _NoSemanticState:
    """The tracker of a surface without semantic state: progress is never observable."""

    @property
    def latest(self) -> Dict[str, Any]:
        return {}

    @property
    def current_state(self) -> Dict[str, Any]:
        return {}

    def observe(self, state: Mapping[str, Any], *, action_group_id: str, changed: bool) -> Dict[str, Any]:
        return {"progress": "unknown", "observable_progress": False}

    def reset(self) -> None:
        pass

    def acknowledge_replan(self) -> None:
        pass


async def reset_active_browser_runtimes() -> int:
    """Reset every live runtime owned by this process."""
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
                "Failed to reset active runtime %s: %s",
                id(runtime),
                result,
            )
            continue
        reset_count += 1
    return reset_count


async def reset_registered_managed_browser(
    *,
    browser_key: str,
    profile_name: str,
    display_mode: str,
    browser_binary: str,
) -> int:
    """Stop a preserved managed device matching one logical identity."""
    wanted = (
        str(browser_key or "").strip(),
        str(profile_name or "").strip(),
        str(display_mode or "").strip().lower(),
        normalize_browser_binary(browser_binary),
    )
    reset_count = 0
    for key in SERVICE_REGISTRY.managed_driver_identities():
        identity = BrowserServiceIdentity.from_key(key)
        actual = (identity.browser_key, identity.profile_name, identity.display_mode, identity.browser_binary)
        if identity.driver_mode == "managed" and actual == wanted and await AgentService.reset_lifecycle_resources(key):
            reset_count += 1
    return reset_count


async def reset_managed_browser_runtime(
    *,
    browser_key: str = "",
    profile_name: str = "jiuwenclaw",
    display_mode: str,
    browser_binary: str = "",
) -> int:
    """Reset only the configured managed device, including its idle handle."""
    normalized_binary = normalize_browser_binary(browser_binary)
    normalized_key = str(browser_key or "").strip()
    normalized_profile = str(profile_name or "").strip()
    normalized_mode = str(display_mode or "").strip().lower()

    reset_count = 0
    for runtime in list(_ACTIVE_BROWSER_RUNTIMES):
        identity = runtime.lifecycle_identity
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
                "Failed to reset the managed runtime %s: %s",
                id(runtime),
                exc,
            )
        else:
            reset_count += 1

    reset_count += await reset_registered_managed_browser(
        browser_key=normalized_key,
        profile_name=normalized_profile,
        display_mode=normalized_mode,
        browser_binary=normalized_binary,
    )
    return reset_count
