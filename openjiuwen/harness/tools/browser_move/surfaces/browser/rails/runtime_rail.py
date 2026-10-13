# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The rail that keeps a browser task on course: the hooks, the lifecycle and the target normalisation."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import time
from typing import Any, Dict, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging.browser_context import (
    reset_browser_agent_log_context,
    set_browser_agent_log_context,
)
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails._multimodal import should_enable_read_image_multimodal
from openjiuwen.harness.tools.browser_move.contract import Click, Effect, PolicyDenied
from openjiuwen.harness.tools.browser_move.runtime.browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
)
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime, ToolKind, _tool_args
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import CommandRecord, command_record
from openjiuwen.harness.tools.browser_move.runtime.worker import BrowserTaskProgressState
from openjiuwen.harness.tools.browser_move.surfaces.browser.probes.results import _ProbeResultsMixin
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.keys import (
    _BROWSER_FIELD_ALIASES,
    _BROWSER_PHASE_STATE_KEY,
    _BROWSER_TASK_DEADLINE_KEY,
    _BROWSER_TERMINAL_STATUSES,
    _BROWSER_TERMINAL_SYNTHESIS_KEY,
    _BROWSER_TOOL_RUNTIME_STATE_KEY,
    _contains_any_token,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.results import _ResultsMixin
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.terminal import _TerminalMixin
from openjiuwen.harness.tools.browser_move.surfaces.browser.semantic.evidence import _EvidenceMixin
from openjiuwen.harness.tools.browser_move.surfaces.browser.semantic.phases import _PhaseMixin
from openjiuwen.harness.tools.browser_move.surfaces.browser.status_logging import (
    BrowserSubagentStatusLogger,
    is_browser_subagent_status_log_enabled,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import (
    _BROWSER_DATE_PICKER_GUIDANCE,
    _BROWSER_DATE_PICKER_SECTION_NAME,
    _BROWSER_IMAGE_CAPABILITY_GUIDANCE,
    _BROWSER_IMAGE_CAPABILITY_SECTION_NAME,
    _BROWSER_TASK_RULES_SECTION_NAME,
    browser_task_rules,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.working_context.store import latest_browser_user_request

_BROWSER_PROGRESS_STATE_KEY = "__browser_subagent_progress_state__"
_BROWSER_PROGRESS_TASK_KEY = "__browser_subagent_last_task__"

_BROWSER_LOG_CONTEXT_TOKEN_KEY = "__browser_agent_log_context_token__"
_BROWSER_ACTION_GROUP_BY_CALL_KEY = "__browser_action_group_by_call__"
_BROWSER_ACTION_GROUP_RESULTS_KEY = "__browser_action_group_results__"
_BROWSER_SKIP_TOOL_CALLS_KEY = "_skip_tool_calls"
_BROWSER_INVOCATION_DEADLINE_KEY = "__browser_invocation_deadline__"
_BROWSER_SIMPLE_TASK_DEADLINE_S = 240.0
_BROWSER_COMPLEX_TASK_DEADLINE_S = 600.0
_BROWSER_MODEL_RETRY_LIMIT = 1
_BROWSER_EXPLICIT_REF_RE = re.compile(
    r"""^\s*\[?\s*ref\s*=\s*["']?([^"'\]\s]+)["']?\s*\]?\s*$""",
    re.IGNORECASE,
)

_BROWSER_REF_TARGET_KEYS = frozenset(
    {
        "target",
        "startTarget",
        "endTarget",
        "ref",
        "startRef",
        "endRef",
    }
)
_BROWSER_REF_ALIAS_KEYS = {
    "ref": "target",
    "startRef": "startTarget",
    "endRef": "endTarget",
}
_BROWSER_TARGET_FIELDS = frozenset(_BROWSER_REF_ALIAS_KEYS.values())
_BROWSER_RUNTIME_ARG_KEYS = frozenset({"field", "fields", "element"})


class BrowserRuntimeRail(_TerminalMixin, _ResultsMixin, _ProbeResultsMixin, _PhaseMixin, _EvidenceMixin, AgentRail):
    """Rail that makes direct browser sessions resumable and completion-aware."""

    def __init__(self, runtime: BrowserAgentRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._agent: Any = None
        self._status_logger = (
            BrowserSubagentStatusLogger(command_of=self._command_of)
            if is_browser_subagent_status_log_enabled(runtime.core_settings)
            else None
        )
        browser_agent_log_info(
            "[BROWSER_SUBAGENT_BOOT] enabled=%s logger=%s",
            self._status_logger is not None,
            type(self._status_logger).__name__ if self._status_logger is not None else None,
        )

    def _command_of(self, tool_name: str, tool_args: Any) -> str:
        return self._command_name(self._runtime.tool_kind(tool_name, tool_args))

    def _emit_status(self, method_name: str, ctx: AgentCallbackContext) -> None:
        if self._status_logger is None:
            return
        method = getattr(self._status_logger, method_name, None)
        if not callable(method):
            browser_agent_log_warning(
                "[BROWSER_SUBAGENT_ERROR] missing_status_method=%s",
                method_name,
            )
            return
        try:
            method(ctx)
        except Exception as exc:
            browser_agent_log_warning("[BROWSER_SUBAGENT_ERROR] method=%s error=%s", method_name, exc)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._agent = ctx.agent
        if isinstance(getattr(ctx, "extra", None), dict):
            if self._runtime.dedicated_log:
                ctx.extra[_BROWSER_LOG_CONTEXT_TOKEN_KEY] = set_browser_agent_log_context(True)
            try:
                timeout_s = max(1, int(self._runtime.service.guardrails.timeout_s))
            except (TypeError, ValueError, AttributeError):
                timeout_s = 600
            ctx.extra.setdefault(_BROWSER_TASK_DEADLINE_KEY, time.monotonic() + timeout_s)
            run_context = self._browser_run_context(ctx)
            if run_context.get("browser_resume") is True:
                ctx.extra["_browser_resume_requested"] = True
        if ctx.steering_queue is None:
            ctx.bind_steering_queue(asyncio.Queue())
        self._emit_status("before_invoke", ctx)
        try:
            await self._runtime.ensure_runtime_ready()
        except Exception as exc:
            raise AbortError(str(exc), cause=exc) from exc
        await self._add_agent_parts(ctx)
        session = getattr(ctx, "session", None)
        if session is None:
            return
        self._hydrate_service_progress_from_session(session)
        query = getattr(getattr(ctx, "inputs", None), "query", None)
        task_text = str(query or "").strip()
        if task_text:
            state = self._ensure_task_state(
                session,
                task_text,
                resume=bool(ctx.extra.get("_browser_resume_requested")),
            )
            self._bind_shared_task_deadline(ctx, session, state)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_model_call", ctx)
        if self._browser_run_context(ctx).get("browser_resume") is True:
            ctx.extra["_browser_resume_requested"] = True
        session = getattr(ctx, "session", None)
        state: Dict[str, Any] = {}
        if session is not None:
            messages = getattr(getattr(ctx, "inputs", None), "messages", None) or []
            task_text = latest_browser_user_request(messages)
            if task_text:
                state = self._ensure_task_state(
                    session,
                    task_text,
                    resume=bool(ctx.extra.get("_browser_resume_requested")),
                )
                self._bind_shared_task_deadline(ctx, session, state)
            self._sync_semantic_progress(session)
            loaded_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
            state = loaded_state if isinstance(loaded_state, dict) else {}

        if self._finish_if_task_deadline_exhausted(ctx, session, state):
            return

        if self._prepare_terminal_synthesis(ctx, session, state):
            return

        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is None:
            return
        if str(state.get("status") or "").strip().lower() not in _BROWSER_TERMINAL_STATUSES:
            builder.remove_section("browser_terminal_synthesis")

        image_input_supported = self._image_input_supported(ctx.agent)
        builder.add_section(
            PromptSection(
                name=_BROWSER_IMAGE_CAPABILITY_SECTION_NAME,
                content=_BROWSER_IMAGE_CAPABILITY_GUIDANCE[image_input_supported],
                priority=85,
            )
        )
        builder.add_section(
            PromptSection(
                name=_BROWSER_DATE_PICKER_SECTION_NAME,
                content=_BROWSER_DATE_PICKER_GUIDANCE,
                priority=85,
            )
        )
        builder.add_section(
            PromptSection(name=_BROWSER_TASK_RULES_SECTION_NAME, content=browser_task_rules(), priority=85)
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_model_call", ctx)
        response = getattr(getattr(ctx, "inputs", None), "response", None)
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if (
            tool_calls
            and isinstance(state, dict)
            and str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES
        ):
            ctx.request_force_finish(self._structured_terminal_result(state))
            return
        response_text = str(getattr(response, "content", "") or "")
        if self._is_unfinished_model_protocol_response(state, tool_calls, response_text):
            if self._request_model_protocol_recovery(ctx, session, state):
                return
            self._finish_model_protocol_failure(ctx, session, state)
            return
        call_ids = [str(tool_call.id) for tool_call in tool_calls if getattr(tool_call, "id", None)]
        extra = getattr(ctx, "extra", None)
        if not call_ids or not isinstance(extra, dict):
            return
        action_group_id = hashlib.sha256("\x1f".join(call_ids).encode("utf-8")).hexdigest()[:16]
        group_by_call = extra.setdefault(_BROWSER_ACTION_GROUP_BY_CALL_KEY, {})
        for call_id in call_ids:
            group_by_call[call_id] = action_group_id
        extra.setdefault(_BROWSER_ACTION_GROUP_RESULTS_KEY, {})[action_group_id] = {
            "expected": call_ids,
            "completed": [],
            "read_only": all(
                self._is_read_only_recovery(self._runtime.tool_kind(call.name, call.arguments), call.arguments)
                for call in tool_calls
            ),
        }

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_model_exception", ctx)
        exception = getattr(ctx, "exception", None)
        if not self._is_retryable_model_exception(exception):
            return
        extra = getattr(ctx, "extra", None)
        deadline = float(extra.get(_BROWSER_TASK_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        remaining_s = deadline - time.monotonic() if deadline else 0.0
        if ctx.retry_attempt < _BROWSER_MODEL_RETRY_LIMIT and remaining_s > 5.0:
            ctx.request_retry(delay_seconds=min(0.5, max(0.0, remaining_s - 5.0)))
            return
        ctx.request_force_finish(self._structured_model_failure(ctx, exception))

    @staticmethod
    def _is_retryable_model_exception(exception: Optional[Exception]) -> bool:
        if isinstance(exception, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        message = str(exception or "").lower()
        retryable_tokens = (
            "timeout",
            "timed out",
            "429",
            "500",
            "502",
            "503",
            "504",
            "upstream",
            "rate limit",
            "service unavailable",
        )
        return _contains_any_token(message, retryable_tokens)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_tool_call", ctx)
        if self._handle_progress_tool_alias(ctx):
            return
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        self._bind_shared_task_deadline(ctx, session, state)
        if self._finish_if_task_deadline_exhausted(ctx, session, state):
            return
        try:
            self._prepare_tool_call(ctx)
        except (ValueError, BaseError) as exc:
            self._deny_tool_call(ctx, exc)

    def _handle_progress_tool_alias(self, ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "").strip().lower()
        if tool_name != "browser_progress" and not tool_name.endswith("_browser_progress"):
            return False
        session = getattr(ctx, "session", None)
        if session is None:
            self._deny_tool_call(ctx, ValueError("browser_progress requires an active browser task session"))
            return True
        payload = self._coerce_tool_args(getattr(inputs, "tool_args", None))
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        state = state if isinstance(state, dict) else {}
        reported_status = str(payload.get("status") or "").strip().lower()
        if not reported_status:
            missing = self._missing_completion_requirements(state)
            blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
            runtime_ready = bool(
                state.get("structured_evidence") or state.get("evidence_slots") or state.get("field_coverage")
            )
            if not blockers and not missing and runtime_ready:
                payload["status"] = "completed"
                reported_status = "completed"
            elif missing and not self._missing_evidence_slots(state) and self._unavailable_evidence_slots(state):
                payload["status"] = "partial"
                reported_status = "partial"
            else:
                self._deny_tool_call(
                    ctx,
                    ValueError(
                        "browser_progress did not include a terminal status and runtime evidence is "
                        "not yet complete. Continue this browser task and collect only the unresolved "
                        "requirements before finishing."
                    ),
                )
                return True
        if reported_status not in _BROWSER_TERMINAL_STATUSES:
            self._deny_tool_call(
                ctx,
                ValueError(
                    "<browser_progress> is a final text protocol. Continue with a real browser tool "
                    "until status is completed, partial, or blocked."
                ),
            )
            return True
        self._apply_worker_progress_to_task_state(session, payload, "")
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        state = state if isinstance(state, dict) else {}
        ctx.request_force_finish(self._structured_terminal_result(state))
        return True

    def _prepare_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        if tool_name.strip().lower() == "browser_progress":
            raise ValueError(
                "<browser_progress> is a text protocol appended to the final response, not a callable tool."
            )
        tool_args = getattr(inputs, "tool_args", None)
        kind = self._runtime.tool_kind(tool_name, tool_args)
        if kind.known and not kind.helper and kind.fields & _BROWSER_TARGET_FIELDS:
            normalized_args = self._normalize_target_args(tool_args, kind.fields)
            if normalized_args is not tool_args:
                inputs.tool_args = tool_args = normalized_args
                kind = self._runtime.tool_kind(tool_name, tool_args)
            tool_name, tool_args, kind = self._rewrite_primary_link_click(inputs, tool_name, tool_args, kind)
            self._reject_unresolved_targets(tool_args)
        evidence_fields = self._runtime_evidence_fields(tool_args)
        if kind.helper and not self._image_input_supported(getattr(ctx, "agent", None)):
            steps = _tool_args(tool_args).get("steps")
            if any(
                isinstance(step, dict) and str(step.get("op") or "").strip().lower() == "screenshot"
                for step in (steps if isinstance(steps, list) else [])
            ):
                raise ValueError(
                    "Screenshot input is unavailable for this browser agent. "
                    "Remove the screenshot batch step and use DOM probes or "
                    "structured extraction."
                )
        self._validate_model_tool_args(ctx, tool_name, tool_args)
        session = getattr(ctx, "session", None)
        self._sync_semantic_progress(session)
        group = self._tool_action_group(ctx)
        attempts = self._phase_attempts(session)
        action_class = self._consume_phase_budget(
            session,
            kind,
            tool_name,
            tool_args,
            current_page_state=self._runtime.export_page_state(),
            replan_group_admitted=bool(group.get("read_only") and group.get("trial_admitted")),
        )
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if isinstance(state, dict) and state.get("replan_trial_pending") and group.get("read_only"):
            group["trial_admitted"] = True
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            tool_call_id = self._tool_call_id(inputs)
            tool_runtime_state = extra.setdefault(_BROWSER_TOOL_RUNTIME_STATE_KEY, {})
            tool_runtime_state[tool_call_id] = {
                "started_at": time.perf_counter(),
                "action_class": action_class,
                "evidence_fields": evidence_fields,
                "generation": self._runtime.generation_id,
                "charged": self._phase_attempts(session) > attempts,
            }

    async def _add_agent_parts(self, ctx: AgentCallbackContext) -> None:
        agent = getattr(ctx, "agent", None)
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return
        parts = self._runtime.agent_parts(
            image_support=self._image_input_supported(agent), language=resolve_language(agent.deep_config.language)
        )
        for tool in parts.tools:
            ability_manager.add_ability(tool.card, tool)

    @staticmethod
    def _tool_action_group(ctx: AgentCallbackContext) -> Dict[str, Any]:
        extra = getattr(ctx, "extra", None) or {}
        call_id = BrowserRuntimeRail._tool_call_id(getattr(ctx, "inputs", None))
        group_id = (extra.get(_BROWSER_ACTION_GROUP_BY_CALL_KEY) or {}).get(call_id)
        return (extra.get(_BROWSER_ACTION_GROUP_RESULTS_KEY) or {}).get(group_id, {})

    @classmethod
    def _validate_model_tool_args(cls, ctx: AgentCallbackContext, tool_name: str, tool_args: Any) -> None:
        """Validate locally before reserving a browser strategy trial."""
        manager = getattr(getattr(ctx, "agent", None), "ability_manager", None)
        get_tool = getattr(manager, "get", None)
        tool = get_tool(tool_name) if callable(get_tool) else None
        if callable(get_tool) and tool is None:
            raise build_error(StatusCode.AGENT_TOOL_NOT_FOUND, error_msg=f"Tool {tool_name} is not registered")
        schema = getattr(tool, "input_params", None)
        if isinstance(schema, dict):
            SchemaUtils.validate_with_schema(cls._coerce_tool_args(tool_args), schema)

    def _runtime_evidence_fields(self, tool_args: Any) -> list[str]:
        args = self._coerce_tool_args(tool_args)
        fields: set[str] = set()
        for value in (args.get("field"), args.get("fields")):
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str):
                    canonical = self._canonical_field_name(item)
                    if canonical in _BROWSER_FIELD_ALIASES:
                        fields.add(canonical)

        target_id = str(args.get("target_id") or args.get("target") or "").strip()
        if target_id.startswith("t_g"):
            try:
                target = self._runtime.resolve_model_target_id(target_id)
            except ValueError:
                target = None
            if target is not None and target.field_name in _BROWSER_FIELD_ALIASES:
                fields.add(target.field_name)
        return sorted(fields)

    def _rewrite_primary_link_click(
        self,
        inputs: Any,
        tool_name: str,
        tool_args: Any,
        kind: ToolKind,
    ) -> tuple[str, Any, ToolKind]:
        if not isinstance(kind.action, Click):
            return tool_name, tool_args, kind
        primary_link = self._runtime.resolve_primary_link(tool_args)
        if not isinstance(primary_link, str) or not primary_link:
            return tool_name, tool_args, kind
        inputs.tool_name = re.sub(
            r"browser_click$",
            "browser_navigate",
            tool_name,
            flags=re.IGNORECASE,
        )
        inputs.tool_args = {"url": primary_link}
        return inputs.tool_name, inputs.tool_args, self._runtime.tool_kind(inputs.tool_name, inputs.tool_args)

    @staticmethod
    def _reject_unresolved_targets(tool_args: Any) -> None:
        for key in _BROWSER_REF_TARGET_KEYS:
            value = str(_tool_args(tool_args).get(key) or "").strip()
            if value.startswith("t_g"):
                raise ValueError(f"PageState target_id {value} has no executable locator.")

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_tool_exception", ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_tool_call", ctx)
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "").strip()
        tool_args = getattr(inputs, "tool_args", None)
        tool_result = getattr(inputs, "tool_result", None)
        if self._is_denied(tool_result):
            self._set_tool_message_outcome(
                inputs,
                success=False,
                executed=False,
                state_changed=False,
                denied=True,
            )
            return

        record = command_record(tool_result)
        kind = self._kind_after(self._runtime.tool_kind(tool_name, tool_args), record)
        result = self._record_view(kind, record, tool_result)
        success, executed, state_changed = self._outcome(kind, record, tool_result, result)
        session = getattr(ctx, "session", None)
        if success and "probe_cards" in tool_name:
            self._enrich_probe_result_contract(session, inputs, tool_name, result)
        self._set_tool_message_outcome(
            inputs,
            success=success,
            executed=executed,
            state_changed=state_changed,
            denied=False,
            effect=kind.effect.value if kind.known and not kind.exempt_from_budget else None,
        )
        if success or state_changed:
            self._attach_page_state(inputs, kind, result)
        if session is None:
            self._compact_large_observation_message(inputs, kind, tool_name, result)
            self._mark_action_group_call_completed(ctx, {})
            return
        evidence_args = self._tool_evidence_args(ctx, inputs)
        progress_delta = self._record_phase_result(
            session,
            kind,
            tool_name,
            evidence_args,
            result,
            progressed=self._progressed(ctx, inputs, record),
            charged=self._charged(ctx, inputs),
        )
        progress_delta.update(
            {
                "executed": executed,
                "denied": False,
                "state_changed": state_changed,
                "ambiguous": bool(not success and state_changed),
            }
        )
        action_class, elapsed_ms = self._consume_tool_call_timing(ctx, inputs)
        recovered = self._record_recent_action(
            session,
            kind=kind,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=result,
            action_class=str(action_class or ""),
            elapsed_ms=elapsed_ms,
            progress_delta=progress_delta,
        )
        if recovered:
            self._runtime.acknowledge_semantic_replan()
        self._compact_large_observation_message(inputs, kind, tool_name, result)
        if not kind.progress:
            self._mark_action_group_call_completed(ctx, progress_delta)
            return
        self._persist_service_progress_to_session(session)
        self._mark_action_group_call_completed(ctx, progress_delta)

    @staticmethod
    def _is_denied(tool_result: Any) -> bool:
        if isinstance(tool_result, PolicyDenied):
            return True
        return isinstance(tool_result, dict) and (
            tool_result.get("denied") is True or tool_result.get("executed") is False
        )

    @classmethod
    def _progressed(cls, ctx: AgentCallbackContext, inputs: Any, record: CommandRecord | None) -> bool:
        """An action whose record shows a new context, or a generation other than the one it started in."""
        if record is None or not record.ok or record.effect not in (Effect.ACT, Effect.NAVIGATE):
            return False
        extra = getattr(ctx, "extra", None)
        states = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY) if isinstance(extra, dict) else None
        call_state = states.get(cls._tool_call_id(inputs)) if isinstance(states, dict) else None
        before = call_state.get("generation") if isinstance(call_state, dict) else None
        return record.context_changed or (before is not None and record.generation != before)

    @classmethod
    def _charged(cls, ctx: AgentCallbackContext, inputs: Any) -> bool:
        """Whether the call cost a phase attempt; a repeated page script does not."""
        extra = getattr(ctx, "extra", None)
        states = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY) if isinstance(extra, dict) else None
        call_state = states.get(cls._tool_call_id(inputs)) if isinstance(states, dict) else None
        return call_state.get("charged", True) if isinstance(call_state, dict) else True

    @staticmethod
    def _kind_after(kind: ToolKind, record: CommandRecord | None) -> ToolKind:
        if record is None:
            return kind
        return dataclasses.replace(kind, action=record.action, effect=record.effect, known=True)

    def _consume_tool_call_timing(self, ctx: AgentCallbackContext, inputs: Any) -> tuple[str, int]:
        extra = getattr(ctx, "extra", None)
        tool_runtime_state = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY, {}) if isinstance(extra, dict) else {}
        call_state = (
            tool_runtime_state.pop(self._tool_call_id(inputs), {}) if isinstance(tool_runtime_state, dict) else {}
        )
        if isinstance(extra, dict) and not tool_runtime_state:
            extra.pop(_BROWSER_TOOL_RUNTIME_STATE_KEY, None)
        started_at = call_state.get("started_at") if isinstance(call_state, dict) else None
        action_class = str(call_state.get("action_class") or "") if isinstance(call_state, dict) else ""
        elapsed_ms = int(max(0.0, (time.perf_counter() - started_at) * 1000)) if started_at else 0
        return action_class, elapsed_ms

    @staticmethod
    def _tool_call_id(inputs: Any) -> str:
        tool_call = getattr(inputs, "tool_call", None)
        call_id = str(getattr(tool_call, "id", "") or "").strip()
        return call_id or f"tool-context-{id(inputs)}"

    @classmethod
    def _denial_code(cls, message: str) -> str:
        normalized = str(message or "").lower()
        if "already" in normalized and "browser task" in normalized:
            return "browser_task_terminal"
        if "semantic" in normalized or "replan" in normalized:
            return "browser_replan_required"
        if "budget exhausted" in normalized:
            return "browser_phase_budget_exhausted"
        if "browser_progress" in normalized:
            return "browser_progress_is_text_protocol"
        if "screenshot input" in normalized:
            return "browser_image_input_unavailable"
        if "known url" in normalized:
            return "browser_direct_navigation_required"
        return "browser_action_denied"

    def _deny_tool_call(self, ctx: AgentCallbackContext, exc: Exception) -> None:
        inputs = getattr(ctx, "inputs", None)
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        status = str(state.get("status") or "in_progress").strip().lower()
        payload = {
            "ok": False,
            "status": "denied",
            "executed": False,
            "state_changed": False,
            "denied": True,
            "error": {
                "code": self._denial_code(str(exc)),
                "message": str(exc),
                "terminal": status in _BROWSER_TERMINAL_STATUSES,
                "replan_required": bool(state.get("replan_required")),
            },
            "runtime_state": {
                "task_status": status,
                "current_phase": state.get("current_phase"),
                "next_action_class": state.get("next_action_class"),
                "blockers": list(state.get("blockers") or [])[:10],
            },
        }
        target_match = re.search(r"\bt_g\d+_\d+\b", str(exc))
        if target_match is not None:
            payload["runtime_state"].update(self._runtime.target_recovery_state(target_match.group(0)))
        call_id = self._tool_call_id(inputs)
        inputs.tool_result = payload
        inputs.tool_msg = ToolMessage(
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            tool_call_id=call_id,
            metadata={
                "executed": False,
                "state_changed": False,
                "denied": True,
            },
        )
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra.setdefault(_BROWSER_SKIP_TOOL_CALLS_KEY, {})[call_id] = True
        self._mark_action_group_call_completed(
            ctx,
            {
                "executed": False,
                "state_changed": False,
                "denied": True,
                "new_evidence_fields": [],
            },
        )

    def _mark_action_group_call_completed(
        self,
        ctx: AgentCallbackContext,
        progress_delta: Dict[str, Any],
    ) -> None:
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        call_id = self._tool_call_id(getattr(ctx, "inputs", None))
        group_by_call = extra.get(_BROWSER_ACTION_GROUP_BY_CALL_KEY)
        group_id = group_by_call.pop(call_id, "") if isinstance(group_by_call, dict) else ""
        groups = extra.get(_BROWSER_ACTION_GROUP_RESULTS_KEY)
        group = groups.get(group_id) if isinstance(groups, dict) and group_id else None
        if isinstance(group, dict):
            completed = group.setdefault("completed", [])
            if call_id not in completed:
                completed.append(call_id)
            if progress_delta.get("executed") is False:
                denied = group.setdefault("denied", [])
                if call_id not in denied:
                    denied.append(call_id)
            elif progress_delta.get("executed") is True:
                executed = group.setdefault("executed", [])
                if call_id not in executed:
                    executed.append(call_id)
            if progress_delta.get("state_changed") is True:
                group["state_changed"] = True
            evidence_fields = group.setdefault("evidence_fields", [])
            for field_name in progress_delta.get("new_evidence_fields") or []:
                if field_name not in evidence_fields:
                    evidence_fields.append(field_name)
            if set(completed) >= set(group.get("expected") or []):
                group["ready"] = True
                session = getattr(ctx, "session", None)
                state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
                if isinstance(state, dict):
                    state["last_action_group"] = {
                        "action_group_id": group_id,
                        "call_count": len(group.get("expected") or []),
                        "executed_count": len(group.get("executed") or []),
                        "denied_count": len(group.get("denied") or []),
                        "state_changed": bool(group.get("state_changed")),
                        "evidence_fields": evidence_fields[:32],
                    }
                    session.update_state({_BROWSER_PHASE_STATE_KEY: state})
                groups.pop(group_id, None)
                if not groups:
                    extra.pop(_BROWSER_ACTION_GROUP_RESULTS_KEY, None)
        if isinstance(group_by_call, dict) and not group_by_call:
            extra.pop(_BROWSER_ACTION_GROUP_BY_CALL_KEY, None)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            session = getattr(ctx, "session", None)
            result = getattr(getattr(ctx, "inputs", None), "result", None)
            if session is None or not isinstance(result, dict):
                return

            session_id = session.get_session_id()
            self._hydrate_service_progress_from_session(session)
            output_text = str(result.get("output") or "")
            clean_output, progress_payload = self._extract_progress_payload(output_text)
            if clean_output != output_text:
                result["output"] = clean_output

            if progress_payload is not None:
                self._apply_worker_progress_to_task_state(session, progress_payload, clean_output)
                if self._finalize_terminal_invoke(session, result, clean_output):
                    return
                progress_state = self._load_progress_state(session)
                parsed_progress = self._build_progress_result(progress_state, clean_output)
                self._runtime.service.set_progress_state(session_id, progress_state)
                exported = progress_state.to_dict()
                if self._runtime.service.should_treat_as_completed(parsed_progress) and clean_output.strip():
                    result["result_type"] = "answer"
                    result["progress_state"] = exported
                    self._clear_progress_state(session)
                    return

                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error=str(parsed_progress.get("error") or "browser_task_incomplete"),
                    page_url=progress_state.last_page_url if progress_state is not None else "",
                    page_title=progress_state.last_page_title if progress_state is not None else "",
                    final=clean_output,
                    screenshot=progress_state.last_screenshot if progress_state is not None else None,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["result_type"] = "error"
                result["failure_summary"] = failure_summary
                result["progress_state"] = exported
                result["output"] = failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
                self._persist_service_progress_to_session(session)
                return

            if self._is_max_iteration_result(result):
                if self._terminalize_invoke_failure(session, "max_iterations_reached"):
                    if self._finalize_terminal_invoke(session, result, clean_output):
                        return
                progress_state = self._load_progress_state(session)
                self._runtime.service.set_progress_state(session_id, progress_state)
                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error="max_iterations_reached",
                    page_url=progress_state.last_page_url if progress_state is not None else "",
                    page_title=progress_state.last_page_title if progress_state is not None else "",
                    final=clean_output or output_text,
                    screenshot=progress_state.last_screenshot if progress_state is not None else None,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["failure_summary"] = failure_summary
                result["progress_state"] = progress_state.to_dict()
                result["output"] = failure_summary
                self._persist_service_progress_to_session(session)
                return

            if str(result.get("result_type", "")).lower() == "answer":
                task_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
                if not isinstance(task_state, dict) or not task_state:
                    self._clear_progress_state(session)
                    return
                self._apply_worker_progress_to_task_state(
                    session,
                    {"status": "completed"},
                    clean_output,
                )
                if self._finalize_terminal_invoke(session, result, clean_output):
                    return
                progress_state = self._load_progress_state(session)
                exported = progress_state.to_dict()
                parsed_progress = self._build_progress_result(progress_state, clean_output)
                if self._runtime.service.should_treat_as_completed(parsed_progress) and clean_output.strip():
                    result["progress_state"] = exported
                    self._clear_progress_state(session)
                    return
                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error="empty_or_unverified_browser_result",
                    page_url=progress_state.last_page_url,
                    page_title=progress_state.last_page_title,
                    final=clean_output,
                    screenshot=progress_state.last_screenshot,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["result_type"] = "error"
                result["failure_summary"] = failure_summary
                result["progress_state"] = exported
                result["output"] = failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
                self._persist_service_progress_to_session(session)
                return

            progress_state = self._load_progress_state(session)
            if str(result.get("result_type") or "").lower() == "error":
                self._terminalize_invoke_failure(session, "browser_execution_error")
            if self._finalize_terminal_invoke(session, result, clean_output):
                return
            exported = progress_state.to_dict() if not progress_state.is_empty() else None
            if exported:
                self._runtime.service.set_progress_state(session_id, progress_state)
                result["progress_state"] = exported
                self._persist_service_progress_to_session(session)

        finally:
            self._emit_status("after_invoke", ctx)
            extra = getattr(ctx, "extra", None)
            if isinstance(extra, dict):
                token = extra.pop(_BROWSER_LOG_CONTEXT_TOKEN_KEY, None)
                if token is not None:
                    reset_browser_agent_log_context(token)

    def _finalize_terminal_invoke(
        self,
        session: Any,
        result: Dict[str, Any],
        model_summary: str,
    ) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if not isinstance(state, dict):
            return False
        status = str(state.get("status") or "").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            return False
        output, payload = self._render_authoritative_terminal_output(state, model_summary)
        progress_state = self._load_progress_state(session)
        result["output"] = output
        result["result_type"] = "answer" if status == "completed" else "error"
        result["error"] = None if status == "completed" else "browser_task_incomplete"
        result["progress_state"] = progress_state.to_dict()
        result["authoritative_browser_result"] = payload
        if status == "completed":
            self._clear_progress_state(session)
        else:
            result["failure_summary"] = self._runtime.service.build_failure_summary(
                task=self._load_task_text(session),
                error="browser_task_incomplete",
                page_url=progress_state.last_page_url,
                page_title=progress_state.last_page_title,
                final="",
                screenshot=progress_state.last_screenshot,
                attempt=1,
                progress_state=progress_state,
            )
            self._persist_service_progress_to_session(session)
        return True

    def _normalize_target_args(self, tool_args: Any, fields: frozenset[str] = frozenset()) -> Any:
        """Rewrite ref aliases, resolve PageState target ids and drop the runtime's own keys the tool does not take."""
        original_is_json = isinstance(tool_args, str)
        if original_is_json:
            try:
                parsed = json.loads(tool_args)
            except ValueError:
                return tool_args
        elif isinstance(tool_args, dict):
            parsed = dict(tool_args)
        else:
            return tool_args
        if not isinstance(parsed, dict):
            return tool_args

        parsed, changed = self._normalize_target_payload(parsed)
        for key in _BROWSER_RUNTIME_ARG_KEYS - fields if fields else ():
            if key in parsed:
                parsed.pop(key)
                changed = True
        if not changed:
            return tool_args
        return json.dumps(parsed, ensure_ascii=False) if original_is_json else parsed

    def _normalize_target_payload(
        self,
        tool_args: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool]:
        parsed = dict(tool_args)
        changed = self._normalize_target_aliases(parsed)
        for key in _BROWSER_REF_TARGET_KEYS:
            value = str(parsed.get(key) or "").strip()
            if not value.startswith("t_g"):
                continue
            target = self._runtime.resolve_model_target_id(value)
            executable_target = str(target.ref or target.selector or "").strip()
            if not executable_target:
                continue
            parsed[key] = executable_target
            element_key = {
                "target": "element",
                "startTarget": "startElement",
                "endTarget": "endElement",
            }.get(key)
            if element_key and not str(parsed.get(element_key) or "").strip():
                parsed[element_key] = str(target.text or target.kind or target.target_id)[:200]
            changed = True

        fields = parsed.get("fields")
        if isinstance(fields, list):
            normalized_fields = []
            for field in fields:
                if not isinstance(field, dict):
                    normalized_fields.append(field)
                    continue
                nested, nested_changed = self._normalize_target_payload(field)
                normalized_fields.append(nested)
                changed = changed or nested_changed
            parsed["fields"] = normalized_fields
        return parsed, changed

    @staticmethod
    def _normalize_target_aliases(parsed: Dict[str, Any]) -> bool:
        changed = False

        if "target_id" in parsed:
            parsed["target"] = parsed.pop("target_id")
            changed = True
        if "generation_id" in parsed:
            parsed.pop("generation_id", None)
            changed = True

        for alias, target_key in _BROWSER_REF_ALIAS_KEYS.items():
            if alias not in parsed:
                continue
            if target_key not in parsed:
                parsed[target_key] = parsed[alias]
            parsed.pop(alias, None)
            changed = True

        for key in _BROWSER_REF_TARGET_KEYS:
            value = parsed.get(key)
            if not isinstance(value, str):
                continue
            match = _BROWSER_EXPLICIT_REF_RE.fullmatch(value)
            if match is None:
                continue
            parsed[key] = match.group(1)
            changed = True
        return changed

    @staticmethod
    def _load_task_text(session: Any) -> str:
        if session is None:
            return ""
        return str(session.get_state(_BROWSER_PROGRESS_TASK_KEY) or "").strip()

    @staticmethod
    def _load_progress_state(session: Any) -> BrowserTaskProgressState:
        if session is None:
            return BrowserTaskProgressState()
        task_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(task_state, dict) and task_state:
            return BrowserTaskProgressState.from_task_state(task_state)
        return BrowserTaskProgressState.from_dict(session.get_state(_BROWSER_PROGRESS_STATE_KEY))

    def _hydrate_service_progress_from_session(self, session: Any) -> BrowserTaskProgressState:
        session_id = session.get_session_id()
        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            self._runtime.service.clear_progress_state(session_id)
            return progress_state
        self._runtime.service.set_progress_state(session_id, progress_state)
        return progress_state

    def _persist_service_progress_to_session(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            self._runtime.service.clear_progress_state(session_id)
            exported: Dict[str, Any] = {}
        else:
            self._runtime.service.set_progress_state(session_id, progress_state)
            exported = progress_state.to_dict()
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: exported,
            }
        )

    def _ensure_task_state(self, session: Any, task: str, *, resume: bool = False) -> Dict[str, Any]:
        normalized_task = str(task or "").strip()
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(state, dict) and str(state.get("task") or "").strip() == normalized_task:
            self._runtime.set_task_requested_fields(state.get("required_fields") or [])
            return state
        if isinstance(state, dict) and resume:
            resumed = self._resume_task_state(session, state, normalized_task)
            self._runtime.set_task_requested_fields(resumed.get("required_fields") or [])
            return resumed
        self._runtime.reset_semantic_task()
        self._runtime.service.clear_progress_state(session.get_session_id())
        state = self._build_phase_state(normalized_task)
        self._runtime.set_task_requested_fields(state.get("required_fields") or [])
        session.update_state(
            {
                _BROWSER_PROGRESS_TASK_KEY: normalized_task,
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PHASE_STATE_KEY: state,
            }
        )
        return state

    @staticmethod
    def _browser_run_context(ctx: AgentCallbackContext) -> Dict[str, Any]:
        context = getattr(getattr(ctx, "inputs", None), "run_context", None)
        if context is None:
            context = (getattr(ctx, "extra", None) or {}).get("run_context")
        if isinstance(context, dict):
            return context.get("extra", context)
        extra = getattr(context, "extra", None)
        return extra if isinstance(extra, dict) else {}

    @staticmethod
    def _bind_shared_task_deadline(
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> float:
        if session is None or not isinstance(state, dict) or not state:
            return 0.0
        extra = getattr(ctx, "extra", None)
        shared_context = BrowserRuntimeRail._browser_run_context(ctx)
        context_budget_s = float(shared_context.get("browser_query_budget_s") or 0.0)
        budget_s = float(
            state.get("deadline_budget_s")
            or context_budget_s
            or (
                _BROWSER_COMPLEX_TASK_DEADLINE_S
                if str(state.get("task_type") or "") == "complex"
                else _BROWSER_SIMPLE_TASK_DEADLINE_S
            )
        )
        now = time.time()
        context_started_at = float(shared_context.get("browser_query_started_at") or 0.0)
        context_deadline_at = float(shared_context.get("browser_query_deadline_at") or 0.0)
        started_at = float(state.get("deadline_started_at") or context_started_at or now)
        deadline_at = float(state.get("deadline_at") or context_deadline_at or started_at + budget_s)
        if context_deadline_at:
            deadline_at = min(deadline_at, context_deadline_at)
        query_id = str(shared_context.get("browser_query_id") or state.get("query_id") or "").strip()
        if query_id:
            state["query_id"] = query_id[:128]
        state["deadline_budget_s"] = budget_s
        state["deadline_started_at"] = started_at
        state["deadline_at"] = deadline_at
        remaining_s = max(0.0, deadline_at - now)
        state["deadline_remaining_s"] = round(remaining_s, 3)
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        if isinstance(extra, dict):
            invocation_deadline = float(extra.get(_BROWSER_INVOCATION_DEADLINE_KEY) or 0.0)
            if not invocation_deadline:
                invocation_deadline = time.monotonic() + remaining_s
                extra[_BROWSER_INVOCATION_DEADLINE_KEY] = invocation_deadline
            effective_remaining_s = min(
                remaining_s,
                max(0.0, invocation_deadline - time.monotonic()),
            )
            extra[_BROWSER_TASK_DEADLINE_KEY] = time.monotonic() + effective_remaining_s
            state["invocation_remaining_s"] = round(effective_remaining_s, 3)
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return effective_remaining_s
        return remaining_s

    @classmethod
    def _finish_if_task_deadline_exhausted(
        cls,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        if session is None or not isinstance(state, dict) or not state:
            return False
        deadline_at = float(state.get("deadline_at") or 0.0)
        shared_exhausted = bool(deadline_at and time.time() >= deadline_at)
        extra = getattr(ctx, "extra", None)
        invocation_deadline = (
            float(extra.get(_BROWSER_INVOCATION_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        )
        invocation_exhausted = bool(invocation_deadline and time.monotonic() >= invocation_deadline)
        if not shared_exhausted and not invocation_exhausted:
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            missing = cls._missing_completion_requirements(state)
            evidence_available = cls._has_task_evidence(state)
            if evidence_available and not missing and not state.get("blockers"):
                state["status"] = "completed"
                state["terminal_reason"] = "task_deadline_completed_from_evidence"
            else:
                state["status"] = "partial" if evidence_available else "blocked"
                blockers = list(state.get("blockers") or [])
                blocker = "task_deadline_exhausted" if shared_exhausted else "task_invocation_slice_exhausted"
                if blocker not in blockers:
                    blockers.append(blocker)
                state["blockers"] = blockers[:10]
                state["terminal_reason"] = blocker
            state["next_action_class"] = "finish"
            state["deadline_remaining_s"] = 0.0 if shared_exhausted else round(max(0.0, deadline_at - time.time()), 3)
            state["invocation_remaining_s"] = 0.0
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        ctx.request_force_finish(cls._structured_terminal_result(state))
        return True

    @staticmethod
    def _terminalize_invoke_failure(session: Any, reason: str) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if not isinstance(state, dict) or not state:
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            evidence_available = BrowserRuntimeRail._has_task_evidence(state)
            state["status"] = "partial" if evidence_available else "blocked"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = reason
            blockers = list(state.get("blockers") or [])
            if reason not in blockers:
                blockers.append(reason)
            state["blockers"] = blockers[:10]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return True

    def _resume_task_state(
        self,
        session: Any,
        state: Dict[str, Any],
        resume_instruction: str,
    ) -> Dict[str, Any]:
        if str(state.get("resume_instruction") or "").strip() == resume_instruction:
            return state
        missing = self._missing_completion_requirements(state)
        if not self._terminal_result_retryable(state, missing):
            return state

        state["resume_count"] = int(state.get("resume_count") or 0) + 1
        state["resume_instruction"] = resume_instruction[:2_000]
        state["status"] = "in_progress"
        state["next_action_class"] = "collect_missing_evidence" if missing else "materially_different_strategy"
        state["terminal_reason"] = ""
        state.pop(_BROWSER_TERMINAL_SYNTHESIS_KEY, None)
        state["replan_required"] = False
        state["replan_trial_pending"] = False
        state["replan_count"] = 0
        state["replan_denial_count"] = 0
        state["blocked_strategy"] = ""
        state["trial_strategy"] = ""
        state["model_protocol_retry_count"] = 0
        recoverable_prefixes = (
            "semantic_",
            "missing_required_field:",
            "missing_evidence_slot:",
            "model_provider_unavailable",
            "model_tool_protocol_error",
            "task_invocation_slice_exhausted",
        )
        state["blockers"] = [
            blocker
            for blocker in state.get("blockers") or []
            if not str(blocker).strip().lower().startswith(recoverable_prefixes)
        ][:10]
        known_urls = list(state.get("known_urls") or [])
        for url in re.findall(r"https?://[^\s<>\"]+", resume_instruction):
            if url not in known_urls:
                known_urls.append(url)
        state["known_urls"] = known_urls[:4]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        self._runtime.reset_semantic_task()
        return state

    @staticmethod
    def _coerce_tool_args(tool_args: Any) -> Dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _command_name(kind: ToolKind) -> str:
        return kind.command.__name__ if kind.command is not None else ""

    def _clear_progress_state(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        self._runtime.service.clear_progress_state(session_id)
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PROGRESS_TASK_KEY: "",
                _BROWSER_PHASE_STATE_KEY: {},
            }
        )

    def _image_input_supported(self, agent: Any) -> bool:
        return should_enable_read_image_multimodal(self._agent if self._agent is not None else agent)
