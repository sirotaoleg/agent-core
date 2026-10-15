# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-protocol recovery and the terminal result of a browser task."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, Optional

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.browser_move.runtime.worker import MAX_ITERATION_MESSAGE, TaskProgressState
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.keys import (
    _BROWSER_PHASE_STATE_KEY,
    _BROWSER_TASK_DEADLINE_KEY,
    _BROWSER_TERMINAL_STATUSES,
    _BROWSER_TERMINAL_SYNTHESIS_KEY,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.semantic.phases import _PhaseMixin

_BROWSER_MODEL_PROTOCOL_RETRY_LIMIT = 1
_BROWSER_TASK_RESUME_LIMIT = 1
_BROWSER_PROGRESS_TAG_RE = re.compile(
    r"<browser_progress>\s*(\{.*?\})\s*</browser_progress>",
    re.DOTALL | re.IGNORECASE,
)
_BROWSER_UNFINISHED_TOOL_INTENT_RE = re.compile(
    r"(?:"
    r"<[^>]*(?:dsml|tool[_ ]?calls?)[^>]*>"
    r"|(?:让我|我(?:将|会|需要|要))继续(?:调用|使用|执行).{0,100}"
    r"(?:工具|browser_|evaluate|probe|navigate|snapshot|batch)"
    r"|(?:let me|i(?:'ll| will| need to))\s+(?:continue|now)\s+"
    r"(?:call|calling|use|using|run).{0,100}"
    r"(?:tool|browser_|evaluate|probe|navigate|snapshot|batch)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_BROWSER_NON_RETRYABLE_BLOCKER_TOKENS = frozenset(
    {
        "captcha",
        "login_required",
        "permission_denied",
        "access_denied",
        "user_intervention_required",
        "payment_required",
        "security_verification",
        "task_deadline_exhausted",
    }
)


class _TerminalMixin:
    @staticmethod
    def _has_unfinished_tool_intent(response_text: str) -> bool:
        return _BROWSER_UNFINISHED_TOOL_INTENT_RE.search(str(response_text or "")) is not None

    @classmethod
    def _is_unfinished_model_protocol_response(
        cls,
        state: Any,
        tool_calls: list[Any],
        response_text: str,
    ) -> bool:
        if tool_calls or not isinstance(state, dict):
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status in _BROWSER_TERMINAL_STATUSES:
            return False
        return cls._has_unfinished_tool_intent(response_text)

    @staticmethod
    def _request_model_protocol_recovery(
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        retry_count = int(state.get("model_protocol_retry_count") or 0)
        extra = getattr(ctx, "extra", None)
        deadline = float(extra.get(_BROWSER_TASK_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        deadline_exhausted = bool(deadline and deadline - time.monotonic() <= 5.0)
        if retry_count >= _BROWSER_MODEL_PROTOCOL_RETRY_LIMIT or deadline_exhausted:
            return False
        state["model_protocol_retry_count"] = retry_count + 1
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        if ctx.steering_queue is None:
            ctx.bind_steering_queue(asyncio.Queue())
        ctx.push_steering(
            "Your previous response described another browser tool call but did not emit a real tool call. "
            "Continue in this same task: emit the actual tool call, or answer concisely using only evidence "
            "already collected."
        )
        ctx.request_model_continue()
        return True

    @classmethod
    def _finish_model_protocol_failure(
        cls,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> None:
        evidence_available = cls._has_task_evidence(state)
        state["status"] = "partial" if evidence_available else "blocked"
        state["next_action_class"] = "finish"
        state["terminal_reason"] = "model_tool_protocol_error"
        blockers = list(state.get("blockers") or [])
        if "model_tool_protocol_error" not in blockers:
            blockers.append("model_tool_protocol_error")
        state["blockers"] = blockers[:10]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        ctx.request_force_finish(cls._structured_terminal_result(state))

    def _prepare_terminal_synthesis(
        self,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        """Allow one tool-disabled prose pass, then force the runtime result."""

        status = str(state.get("status") or "").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            return False
        if state.get(_BROWSER_TERMINAL_SYNTHESIS_KEY):
            ctx.request_force_finish(self._structured_terminal_result(state))
            return True

        state[_BROWSER_TERMINAL_SYNTHESIS_KEY] = True
        state["next_action_class"] = "finish"
        if session is not None:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        inputs = getattr(ctx, "inputs", None)
        if inputs is not None:
            inputs.tools = []
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            authoritative = self._authoritative_terminal_payload(state)
            builder.add_section(
                PromptSection(
                    name="browser_terminal_synthesis",
                    content={
                        "en": (
                            "Browser execution has ended. Do not call tools. Summarize the runtime-owned "
                            f"result without changing status, blockers, missing fields, or evidence: {authoritative}"
                        ),
                        "cn": (
                            "浏览器执行已结束。不要调用工具。请说明以下由运行时确定的结果，不得修改状态、"
                            f"阻断项、缺失字段或证据：{authoritative}"
                        ),
                    },
                    priority=100,
                )
            )
        return False

    def _structured_model_failure(
        self,
        ctx: AgentCallbackContext,
        exception: Optional[Exception],
    ) -> Dict[str, Any]:
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        evidence_available = self._has_task_evidence(state)
        status = "partial" if evidence_available else "failed"
        if state:
            state["status"] = "partial" if evidence_available else "blocked"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = "model_provider_unavailable"
            blockers = list(state.get("blockers") or [])
            if "model_provider_unavailable" not in blockers:
                blockers.append("model_provider_unavailable")
            state["blockers"] = blockers[:10]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        payload = {
            "ok": False,
            "status": status,
            "error": {
                "code": "model_provider_unavailable",
                "message": str(exception or "model provider failed")[:500],
                "retry_attempts": int(ctx.retry_attempt) + 1,
            },
            "progress": {
                "task_id": state.get("task_id"),
                "status": state.get("status"),
                "current_phase": state.get("current_phase"),
                "required_fields": list(state.get("required_fields") or [])[:32],
                "field_coverage": list(state.get("field_coverage") or [])[:32],
                "required_evidence_slots": list(state.get("required_evidence_slots") or [])[:12],
                "evidence_slots": list(state.get("evidence_slots") or [])[-12:],
                "blockers": list(state.get("blockers") or [])[:10],
            }
            if state
            else {},
        }
        return {
            "output": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "result_type": "error",
            "error": "model_provider_unavailable",
            "progress_state": self._load_progress_state(session).to_dict() if session is not None else {},
        }

    @classmethod
    def _authoritative_terminal_payload(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        status = str(state.get("status") or "partial").strip().lower()
        missing = cls._missing_completion_requirements(state)
        unverified = cls._advisory_missing_fields(state) if status == "completed" else set()
        missing = [field for field in missing if field not in unverified]
        blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
        missing_slots = [slot for slot in cls._missing_evidence_slots(state) if slot["field"] not in unverified]
        unavailable_slots = cls._unavailable_evidence_slots(state)
        retryable = cls._terminal_result_retryable(state, missing)
        deadline_started_at = float(state.get("deadline_started_at") or 0.0)
        deadline_at = float(state.get("deadline_at") or 0.0)
        now = time.time()
        return {
            "status": status,
            "task_id": state.get("task_id"),
            "current_phase": state.get("current_phase"),
            "missing_fields": missing[:32],
            "unverified_fields": sorted(unverified)[:32],
            "missing_slots": missing_slots[:12],
            "unavailable_slots": unavailable_slots[:12],
            "requested_slots": [
                dict(slot) for slot in (state.get("required_evidence_slots") or [])[:12] if isinstance(slot, dict)
            ],
            "blockers": blockers[:10],
            "field_coverage": list(state.get("field_coverage") or [])[:32],
            "evidence": list(state.get("evidence_slots") or [])[-12:],
            "observations": cls._task_observations(state)[-3:],
            "current_page": dict(state.get("last_page") or {}),
            "requested_result_count": int(state.get("requested_result_count") or 0),
            "observed_result_count": int(state.get("observed_result_count") or 0),
            "terminal_reason": state.get("terminal_reason"),
            "retryable": retryable,
            "recommended_recovery": cls._recommended_recovery(state, missing),
            "resume_count": int(state.get("resume_count") or 0),
            "deadline": {
                "budget_s": float(state.get("deadline_budget_s") or 0.0),
                "elapsed_s": round(max(0.0, now - deadline_started_at), 3) if deadline_started_at else 0.0,
                "remaining_s": round(max(0.0, deadline_at - now), 3) if deadline_at else 0.0,
                "invocation_remaining_s": float(state.get("invocation_remaining_s") or 0.0),
            },
        }

    @classmethod
    def _terminal_result_retryable(
        cls,
        state: Dict[str, Any],
        missing: Optional[list[str]] = None,
    ) -> bool:
        status = str(state.get("status") or "").strip().lower()
        if status == "completed" or int(state.get("resume_count") or 0) >= _BROWSER_TASK_RESUME_LIMIT:
            return False
        blockers = " ".join(str(item or "").strip().lower() for item in state.get("blockers") or [])
        if any(token in blockers for token in _BROWSER_NON_RETRYABLE_BLOCKER_TOKENS):
            return False
        if "phase_budget_exhausted" in blockers:
            return False
        requirements = missing if missing is not None else cls._missing_completion_requirements(state)
        unresolved_slots = cls._missing_evidence_slots(state)
        if requirements and not unresolved_slots and cls._unavailable_evidence_slots(state):
            return False
        return bool(
            status in {"partial", "blocked"}
            and (
                requirements
                or cls._has_task_evidence(state)
                or state.get("terminal_reason")
                in {
                    "model_provider_unavailable",
                    "model_tool_protocol_error",
                    "runtime_completion_requirements_missing",
                    "runtime_blocked",
                    "semantic_replan_denial_budget_exhausted",
                    "task_invocation_slice_exhausted",
                }
            )
        )

    @staticmethod
    def _recommended_recovery(state: Dict[str, Any], missing: list[str]) -> str:
        reported = str(state.get("worker_reported_next_action") or "").strip()
        if reported:
            return reported[:200]
        reason = str(state.get("terminal_reason") or "").strip().lower()
        if reason in {
            "model_provider_unavailable",
            "model_tool_protocol_error",
            "task_invocation_slice_exhausted",
        }:
            return "retry_model_step_from_current_page"
        if missing and not _PhaseMixin._missing_evidence_slots(state):
            if _PhaseMixin._unavailable_evidence_slots(state):
                return "finish_with_explicit_unknown_fields"
        if missing:
            return "collect_missing_evidence_from_current_page"
        return "replan_from_current_page"

    @classmethod
    def _structured_terminal_result(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = cls._authoritative_terminal_payload(state)
        status = str(payload.get("status") or "partial")
        return {
            "output": json.dumps(
                {"browser_result": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "result_type": "answer" if status == "completed" else "error",
            "error": None if status == "completed" else "browser_task_incomplete",
            "authoritative_browser_result": payload,
        }

    @classmethod
    def _render_authoritative_terminal_output(
        cls,
        state: Dict[str, Any],
        model_summary: str,
    ) -> tuple[str, Dict[str, Any]]:
        payload = cls._authoritative_terminal_payload(state)
        if str(model_summary or "").lstrip().startswith("{"):
            try:
                previous = json.loads(model_summary).get("browser_result")
            except (ValueError, AttributeError):
                previous = None
            if isinstance(previous, dict):
                model_summary = str(previous.get("summary") or state.get("last_worker_final") or "")
        if str(model_summary or "").strip():
            payload["summary"] = str(model_summary).strip()[:8_000]
        return (
            json.dumps({"browser_result": payload}, ensure_ascii=False, separators=(",", ":")),
            payload,
        )

    @staticmethod
    def _extract_progress_payload(output_text: str) -> tuple[str, Optional[Dict[str, Any]]]:
        text = str(output_text or "")
        match = _BROWSER_PROGRESS_TAG_RE.search(text)
        if match is None:
            return text, None
        payload_text = match.group(1).strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return text, None
        cleaned = _BROWSER_PROGRESS_TAG_RE.sub("", text, count=1).strip()
        return cleaned, payload if isinstance(payload, dict) else None

    @staticmethod
    def _build_progress_result(
        progress_state: TaskProgressState,
        clean_output: str,
    ) -> Dict[str, Any]:
        """Build completion output from runtime-owned progress only."""

        status = str(progress_state.status or "").strip().lower() or "partial"
        progress = progress_state.to_dict()
        return {
            "ok": status == "completed",
            "status": status,
            "progress": progress,
            "final": clean_output,
            "error": None if status == "completed" else "browser_task_incomplete",
        }

    @classmethod
    def _apply_worker_progress_to_task_state(
        cls,
        session: Any,
        payload: Dict[str, Any],
        final: str,
    ) -> None:
        """Validate a worker completion request against authoritative runtime state."""

        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return
        reported_status = str(payload.get("status") or "partial").strip().lower()
        state["worker_reported_status"] = reported_status
        reported_blockers = payload.get("blockers") or payload.get("missing_requirements") or []
        if isinstance(reported_blockers, str):
            reported_blockers = [reported_blockers]
        state["worker_reported_blockers"] = [str(item)[:300] for item in reported_blockers if str(item).strip()][:10]
        next_action = payload.get("next_action") or payload.get("next_step")
        if next_action:
            state["worker_reported_next_action"] = str(next_action)[:200]
        if final:
            state["last_worker_final"] = str(final)[:8_000]

        current_status = str(state.get("status") or "in_progress").strip().lower()
        if current_status in _BROWSER_TERMINAL_STATUSES:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return

        runtime_blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
        advisory_fields = cls._advisory_missing_fields(state) if final.strip() else set()
        missing_fields = [
            field for field in cls._missing_completion_requirements(state) if field not in advisory_fields
        ]
        unavailable_slots = cls._unavailable_evidence_slots(state)
        phases = state.get("phases") if isinstance(state.get("phases"), dict) else {}
        completed_phase_count = sum(
            1 for details in phases.values() if isinstance(details, dict) and details.get("status") == "completed"
        )
        evidence_available = cls._has_task_evidence(state)
        runtime_ready = bool(completed_phase_count or evidence_available)
        if state.get("replan_required"):
            runtime_blockers = list(dict.fromkeys([*runtime_blockers, "semantic_replan_required"]))

        completion_requirements_met = (
            bool(final.strip() or evidence_available)
            and not runtime_blockers
            and not missing_fields
            and not unavailable_slots
            and runtime_ready
        )
        if reported_status == "completed" and completion_requirements_met:
            state["status"] = "completed"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = "runtime_completion_validated"
        elif reported_status in _BROWSER_TERMINAL_STATUSES:
            state["status"] = "blocked" if runtime_blockers else "partial"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = (
                "runtime_blocked" if runtime_blockers else "runtime_completion_requirements_missing"
            )
            if runtime_blockers:
                state["blockers"] = runtime_blockers[:10]
            elif missing_fields:
                state["blockers"] = [f"missing_required_field:{field}" for field in missing_fields[:10]]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})

    @staticmethod
    def _is_max_iteration_result(result: Dict[str, Any]) -> bool:
        output = str(result.get("output") or "").strip()
        result_type = str(result.get("result_type") or "").strip().lower()
        return result_type == "error" and MAX_ITERATION_MESSAGE.lower() in output.lower()
