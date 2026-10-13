# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The worker loop and its progress state: attempts, restarts, the failure context and the answer of one task."""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

from .agents import default_worker_prompt, refresh_browser_worker_system_prompt, worker_driver_name
from .rails.final_answer_recovery import (
    final_answer_recovery,
    recovery_message,
    recovery_reason,
)
from .utils.parsing import extract_json_object

MAX_ITERATION_MESSAGE = "Max iterations reached without completion"
_ctx_observer_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_observer_session_id",
    default="",
)
_ctx_observer_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_observer_request_id",
    default="",
)


@dataclass
class BrowserTaskProgressState:
    request_id: str = ""
    status: str = "unknown"
    completed_steps: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    next_step: str = ""
    completion_evidence: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    recent_tool_steps: list[str] = field(default_factory=list)
    last_page_url: str = ""
    last_page_title: str = ""
    last_screenshot: Any = None
    last_worker_final: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BrowserTaskProgressState":
        if not isinstance(data, dict):
            return cls()
        last_page = data.get("last_page") if isinstance(data.get("last_page"), dict) else {}
        return cls(
            request_id=str(data.get("request_id") or "").strip(),
            status=str(data.get("status") or "unknown").strip() or "unknown",
            completed_steps=[str(item).strip() for item in data.get("completed_steps") or [] if str(item).strip()],
            remaining_steps=[str(item).strip() for item in data.get("remaining_steps") or [] if str(item).strip()],
            next_step=str(data.get("next_step") or "").strip(),
            completion_evidence=[
                str(item).strip() for item in data.get("completion_evidence") or [] if str(item).strip()
            ],
            missing_requirements=[
                str(item).strip() for item in data.get("missing_requirements") or [] if str(item).strip()
            ],
            recent_tool_steps=[str(item).strip() for item in data.get("recent_tool_steps") or [] if str(item).strip()],
            last_page_url=str(last_page.get("url") or "").strip(),
            last_page_title=str(last_page.get("title") or "").strip(),
            last_screenshot=data.get("last_screenshot"),
            last_worker_final=str(data.get("last_worker_final") or "").strip(),
        )

    @classmethod
    def from_task_state(cls, data: Optional[Dict[str, Any]]) -> "BrowserTaskProgressState":
        """Build the legacy progress DTO from runtime-owned task state."""

        if not isinstance(data, dict) or not data:
            return cls()
        completed_steps, remaining_steps, completion_evidence = cls._phase_progress(data)
        completion_evidence.extend(cls._evidence_summaries(data))
        recent_steps = cls._recent_action_summaries(data)
        current_phase = str(data.get("current_phase") or "").strip()
        next_action = str(data.get("next_action_class") or current_phase).strip()
        last_page = data.get("last_page") if isinstance(data.get("last_page"), dict) else {}
        return cls(
            request_id=str(data.get("task_id") or "").strip(),
            status=str(data.get("status") or "unknown").strip() or "unknown",
            completed_steps=completed_steps,
            remaining_steps=remaining_steps,
            next_step=next_action,
            completion_evidence=completion_evidence[-10:],
            missing_requirements=[str(item) for item in data.get("blockers") or [] if str(item).strip()],
            recent_tool_steps=recent_steps,
            last_page_url=str(last_page.get("url") or "").strip(),
            last_page_title=str(last_page.get("title") or "").strip(),
            last_worker_final=str(data.get("last_worker_final") or "").strip(),
        )

    @staticmethod
    def _phase_progress(data: Dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
        phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
        completed_steps: list[str] = []
        remaining_steps: list[str] = []
        completion_evidence: list[str] = []
        for phase_name, details in phases.items():
            if not isinstance(details, dict):
                continue
            phase_label = str(phase_name).strip()
            if details.get("status") == "completed":
                completed_steps.append(phase_label)
                evidence = str(details.get("completion_evidence") or "").strip()
                if evidence:
                    completion_evidence.append(f"{phase_label}: {evidence}")
            else:
                remaining_steps.append(phase_label)
        return completed_steps, remaining_steps, completion_evidence

    @staticmethod
    def _evidence_summaries(data: Dict[str, Any]) -> list[str]:
        summaries: list[str] = []
        evidence_records = data.get("structured_evidence")
        if isinstance(evidence_records, list):
            for record in evidence_records[-3:]:
                if not isinstance(record, dict):
                    continue
                fields = ", ".join(str(item) for item in record.get("fields") or [])
                if fields:
                    summaries.append(f"{record.get('kind') or 'evidence'}: {fields}")
        evidence_slots = data.get("evidence_slots")
        if isinstance(evidence_slots, list):
            for slot in evidence_slots[-4:]:
                if not isinstance(slot, dict):
                    continue
                label = "/".join(str(slot.get(key) or "") for key in ("entity", "variant", "field")).strip("/")
                value = str(slot.get("value") or "").strip()
                if label and value:
                    summaries.append(f"{label}: {value[:160]}")
        return summaries

    @staticmethod
    def _recent_action_summaries(data: Dict[str, Any]) -> list[str]:
        recent_steps: list[str] = []
        recent_actions = data.get("recent_actions")
        if isinstance(recent_actions, list):
            for action in recent_actions[-6:]:
                if not isinstance(action, dict):
                    continue
                summary_parts = (
                    str(action.get("action_class") or "").strip(),
                    str(action.get("target_summary") or "").strip(),
                    str(action.get("outcome") or "").strip(),
                    str(action.get("semantic_delta") or "").strip(),
                )
                summary = " ".join(filter(None, summary_parts))
                if summary:
                    recent_steps.append(summary[:300])
        return recent_steps

    def is_empty(self) -> bool:
        return (
            self.status == "unknown"
            and not self.completed_steps
            and not self.remaining_steps
            and not self.next_step
            and not self.completion_evidence
            and not self.missing_requirements
            and not self.recent_tool_steps
            and not self.last_page_url
            and not self.last_page_title
            and not self.last_worker_final
            and self.last_screenshot in (None, "")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "completed_steps": list(self.completed_steps),
            "remaining_steps": list(self.remaining_steps),
            "next_step": self.next_step or None,
            "completion_evidence": list(self.completion_evidence),
            "missing_requirements": list(self.missing_requirements),
            "recent_tool_steps": list(self.recent_tool_steps),
            "last_page": {
                "url": self.last_page_url,
                "title": self.last_page_title,
            },
            "last_screenshot": self.last_screenshot,
            "last_worker_final": self.last_worker_final or None,
            "request_id": self.request_id or None,
        }


class _WorkerLoop:
    @staticmethod
    def _is_retryable_transport_message(text: str) -> bool:
        lowered = str(text or "").lower()
        markers = (
            "session terminated",
            "not connected",
            "endofstream",
            "closedresourceerror",
            "brokenresourceerror",
            "stream closed",
            "connection closed",
            "broken pipe",
            "remoteprotocolerror",
            "readerror",
            "writeerror",
        )
        return any(marker in lowered for marker in markers)

    @classmethod
    def _is_retryable_transport_error(cls, exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        return cls._is_retryable_transport_message(name) or cls._is_retryable_transport_message(text)

    @staticmethod
    def _is_retryable_runtime_result(parsed: Dict[str, Any]) -> bool:
        if not isinstance(parsed, dict) or bool(parsed.get("ok", False)):
            return False
        text = (f"{parsed.get('error', '')}\n{parsed.get('final', '')}").lower()
        markers = (
            "frame has been detached",
            "execution context was destroyed",
            "target page, context or browser has been closed",
            "target closed",
            "navigation failed because browser has disconnected",
            "context closed",
            "page crashed",
            "net::err_network_changed",
            "net::err_internet_disconnected",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _should_restart_after_runtime_result(parsed: Dict[str, Any]) -> bool:
        if not isinstance(parsed, dict):
            return False
        text = (f"{parsed.get('error', '')}\n{parsed.get('final', '')}").lower()
        restart_markers = (
            "frame has been detached",
            "target page, context or browser has been closed",
            "target closed",
            "context closed",
            "page crashed",
        )
        return any(marker in text for marker in restart_markers)

    @staticmethod
    def _build_worker_conversation_id(session_id: str, request_id: str) -> str:
        sid = (session_id or "").strip() or "browser-session"
        rid = (request_id or "").strip() or "request"
        return f"{sid}:worker:{rid}:{uuid.uuid4().hex}"

    @classmethod
    def _clean_progress_items(cls, value: Any, *, limit: int) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            candidates = list(value)
        else:
            candidates = [value]
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text_value = " ".join(str(item or "").split()).strip()
            if not text_value:
                continue
            lowered = text_value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(cls._trim_text(text_value, 220))
            if len(cleaned) >= limit:
                break
        return cleaned

    @staticmethod
    def _normalize_progress_status(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "complete": "completed",
            "completed": "completed",
            "done": "completed",
            "partial": "partial",
            "in_progress": "partial",
            "in-progress": "partial",
            "blocked": "blocked",
            "failed": "failed",
        }
        return aliases.get(normalized, "")

    @staticmethod
    def _push_recent_tool_step(existing: list[str], step: str, *, limit: int = 8) -> list[str]:
        normalized = " ".join(str(step or "").split()).strip()
        if not normalized:
            return list(existing)
        updated = [item for item in existing if item != normalized]
        updated.append(normalized)
        return updated[-limit:]

    @classmethod
    def _extract_page_snapshot(cls, value: Any) -> tuple[str, str]:
        if not isinstance(value, dict):
            return "", ""
        location = value.get("page") if isinstance(value.get("page"), dict) else {}
        address = str(value.get("url") or location.get("url") or "").strip()
        title = str(value.get("title") or location.get("title") or "").strip()
        return cls._trim_text(address, 240), cls._trim_text(title, 120)

    @staticmethod
    def _extract_screenshot_snapshot(value: Any) -> Any:
        if not isinstance(value, dict):
            return None
        screenshot = value.get("screenshot")
        if screenshot in (None, ""):
            return None
        return screenshot

    @classmethod
    def _summarize_observation_payload(cls, value: Any) -> str:
        if isinstance(value, dict):
            parts: list[str] = []
            error_text = str(value.get("error") or "").strip()
            if error_text:
                parts.append(f"error={cls._trim_text(error_text, 140)}")
            for key in ("message", "text", "result", "output", "value", "selector"):
                candidate = value.get(key)
                if candidate in (None, "") or isinstance(candidate, (dict, list, tuple, set)):
                    continue
                parts.append(cls._trim_text(str(candidate), 160))
                break
            address, title = cls._extract_page_snapshot(value)
            if address:
                parts.append(f"url={address}")
            if title:
                parts.append(f"title={title}")
            ok_value = value.get("ok")
            if ok_value is True and not parts:
                parts.append("ok")
            if not parts and value:
                parts.append(", ".join(list(value.keys())[:4]))
            return "; ".join(parts)
        if isinstance(value, (list, tuple)):
            nested = [cls._summarize_observation_payload(item) for item in list(value)[:2]]
            nested = [item for item in nested if item]
            return " | ".join(nested)
        text_value = " ".join(str(value or "").split()).strip()
        return cls._trim_text(text_value, 160)

    @classmethod
    def _summarize_tool_result(cls, tool_name: str, tool_result: Any) -> str:
        payload_summary = cls._summarize_observation_payload(tool_result) or "completed"
        if tool_name:
            return f"{tool_name}: {payload_summary}"
        return payload_summary

    def _get_progress_state(self, session_id: str) -> BrowserTaskProgressState:
        return self._progress_by_session.setdefault(session_id, BrowserTaskProgressState())

    def _update_progress_from_tool_observation(
        self,
        *,
        session_id: str,
        request_id: str,
        tool_name: str,
        tool_result: Any,
    ) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        progress_state = self._get_progress_state(sid)
        if request_id:
            progress_state.request_id = request_id
        progress_state.recent_tool_steps = self._push_recent_tool_step(
            progress_state.recent_tool_steps,
            self._summarize_tool_result(tool_name, tool_result),
        )
        address, title = self._extract_page_snapshot(tool_result)
        if address:
            progress_state.last_page_url = address
        if title:
            progress_state.last_page_title = title
        screenshot = self._extract_screenshot_snapshot(tool_result)
        if screenshot is not None:
            progress_state.last_screenshot = screenshot
        if progress_state.status == "unknown":
            progress_state.status = "partial"

    async def observe_worker_tool_result(self, tool_name: str, tool_result: Any) -> None:
        session_id = _ctx_observer_session_id.get().strip()
        if not session_id:
            return
        request_id = _ctx_observer_request_id.get().strip()
        self._update_progress_from_tool_observation(
            session_id=session_id,
            request_id=request_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )

    @classmethod
    def _should_treat_as_completed(cls, parsed: Dict[str, Any]) -> bool:
        status = cls._normalize_progress_status(parsed.get("status") or parsed.get("task_status"))
        if status != "completed":
            return False
        progress = parsed.get("progress") if isinstance(parsed.get("progress"), dict) else {}
        missing = cls._clean_progress_items(
            progress.get("missing_requirements") or parsed.get("missing_requirements"),
            limit=4,
        )
        evidence = cls._clean_progress_items(
            progress.get("completion_evidence") or parsed.get("completion_evidence"),
            limit=4,
        )
        final_text = str(parsed.get("final") or "").strip()
        return not missing and bool(evidence or final_text)

    def _update_progress_from_worker_result(
        self,
        *,
        session_id: str,
        request_id: str,
        parsed: Dict[str, Any],
    ) -> None:
        sid = (session_id or "").strip()
        if not sid or not isinstance(parsed, dict):
            return
        progress_state = self._get_progress_state(sid)
        if request_id:
            progress_state.request_id = request_id

        progress_payload = parsed.get("progress") if isinstance(parsed.get("progress"), dict) else {}
        status = self._normalize_progress_status(parsed.get("status") or parsed.get("task_status"))
        if not status:
            if bool(parsed.get("ok", False)):
                status = "completed"
            elif self._is_max_iteration_result(parsed):
                status = "partial"
            elif str(parsed.get("error") or "").strip():
                status = "failed"
            else:
                status = "partial"
        progress_state.status = status

        completed_steps = self._clean_progress_items(
            progress_payload.get("completed_steps") or parsed.get("completed_steps"),
            limit=8,
        )
        if completed_steps:
            progress_state.completed_steps = completed_steps

        remaining_steps = self._clean_progress_items(
            progress_payload.get("remaining_steps") or parsed.get("remaining_steps"),
            limit=8,
        )
        if remaining_steps:
            progress_state.remaining_steps = remaining_steps

        next_step = str(progress_payload.get("next_step") or parsed.get("next_step") or "").strip()
        if next_step:
            progress_state.next_step = self._trim_text(next_step, 220)

        completion_evidence = self._clean_progress_items(
            progress_payload.get("completion_evidence") or parsed.get("completion_evidence"),
            limit=6,
        )
        if completion_evidence:
            progress_state.completion_evidence = completion_evidence

        missing_requirements = self._clean_progress_items(
            progress_payload.get("missing_requirements") or parsed.get("missing_requirements"),
            limit=6,
        )
        if missing_requirements:
            progress_state.missing_requirements = missing_requirements

        address, title = self._extract_page_snapshot(parsed)
        if address:
            progress_state.last_page_url = address
        if title:
            progress_state.last_page_title = title
        screenshot = self._extract_screenshot_snapshot(parsed)
        if screenshot is not None:
            progress_state.last_screenshot = screenshot

        final_text = str(parsed.get("final") or "").strip()
        if final_text:
            progress_state.last_worker_final = self._trim_text(final_text, 1200)
            if bool(parsed.get("ok", False)) and not progress_state.completion_evidence:
                progress_state.completion_evidence = [self._trim_text(final_text, 220)]

    @classmethod
    def _build_progress_context(cls, progress_state: Optional[BrowserTaskProgressState]) -> str:
        if progress_state is None:
            return ""
        lines: list[str] = []
        if progress_state.status and progress_state.status != "unknown":
            lines.append(f"- Known progress status: {progress_state.status}")
        if progress_state.completed_steps:
            lines.append(f"- Completed steps: {' | '.join(progress_state.completed_steps)}")
        if progress_state.remaining_steps:
            lines.append(f"- Remaining steps: {' | '.join(progress_state.remaining_steps)}")
        if progress_state.next_step:
            lines.append(f"- Next step to try: {progress_state.next_step}")
        if progress_state.completion_evidence:
            lines.append(f"- Completion evidence observed: {' | '.join(progress_state.completion_evidence)}")
        if progress_state.missing_requirements:
            lines.append(f"- Missing requirements / blockers: {' | '.join(progress_state.missing_requirements)}")
        if progress_state.recent_tool_steps:
            lines.append("- Recent browser tool activity:")
            lines.extend(f"  - {step}" for step in progress_state.recent_tool_steps[-6:])
        if not lines:
            return ""
        return "Known progress for continuation:\n" + "\n".join(lines)

    @staticmethod
    def _is_empty_progress_state(progress_state: BrowserTaskProgressState) -> bool:
        return (
            progress_state.status == "unknown"
            and not any(
                (
                    progress_state.completed_steps,
                    progress_state.remaining_steps,
                    progress_state.next_step,
                    progress_state.completion_evidence,
                    progress_state.missing_requirements,
                    progress_state.recent_tool_steps,
                    progress_state.last_page_url,
                    progress_state.last_page_title,
                    progress_state.last_worker_final,
                )
            )
            and progress_state.last_screenshot in (None, "")
        )

    def _export_progress_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        progress_state = self._progress_by_session.get((session_id or "").strip())
        if progress_state is None:
            return None
        if self._is_empty_progress_state(progress_state):
            return None
        return progress_state.to_dict()

    @classmethod
    def should_treat_as_completed(cls, parsed: Dict[str, Any]) -> bool:
        return cls._should_treat_as_completed(parsed)

    def build_failure_summary(
        self,
        *,
        task: str,
        error: str,
        page_url: str,
        page_title: str,
        final: str,
        screenshot: Optional[str],
        attempt: int,
        progress_state: Optional[BrowserTaskProgressState],
    ) -> str:
        return self._build_failure_summary(
            task=task,
            error=error,
            page_url=page_url,
            page_title=page_title,
            final=final,
            screenshot=screenshot,
            attempt=attempt,
            progress_state=progress_state,
        )

    def set_progress_state(self, session_id: str, progress_state: BrowserTaskProgressState) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        if progress_state.is_empty():
            self._progress_by_session.pop(sid, None)
            return
        self._progress_by_session[sid] = progress_state

    def clear_progress_state(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        self._progress_by_session.pop(sid, None)

    async def _run_task_once(
        self, task: str, session_id: str, request_id: str, worker: Callable[[], Awaitable[ReActAgent]]
    ) -> Dict[str, Any]:
        agent = await worker()
        task_prompt = (
            f"Session id: {session_id}\n"
            f"Request id: {request_id}\n"
            f"Max steps: {self.guardrails.max_steps}\n"
            f"Max failures: {self.guardrails.max_failures}\n\n"
            f"Task:\n{task}\n\n"
            "Perform the task in the current logical browser session/tab for this session id."
        )
        if isinstance(agent, ReActAgent):
            await refresh_browser_worker_system_prompt(
                agent,
                worker_prompt=self._session.assembly.surface.profile.worker_prompt or default_worker_prompt,
                screenshot_subdir=self._screenshot_subdir,
                artifacts_subdir=self._artifacts_subdir,
                driver_name=worker_driver_name(self._session.assembly.stack.info),
            )
        worker_conversation_id = self._build_worker_conversation_id(session_id, request_id)
        token_session = _ctx_observer_session_id.set(session_id)
        token_request = _ctx_observer_request_id.set(request_id)
        try:
            result = await Runner.run_agent(
                agent,
                {"query": task_prompt, "conversation_id": worker_conversation_id, "request_id": request_id},
            )
        finally:
            _ctx_observer_session_id.reset(token_session)
            _ctx_observer_request_id.reset(token_request)
        output_text = result.get("output") if isinstance(result, dict) else result
        parsed = extract_json_object(output_text)
        if parsed:
            self._update_progress_from_worker_result(
                session_id=session_id,
                request_id=request_id,
                parsed=parsed,
            )
            return parsed

        output_str = str(output_text) if output_text is not None else ""
        output_lower = output_str.lower()
        max_iterations_reached = MAX_ITERATION_MESSAGE.lower() in output_lower
        reason = recovery_reason(output_str, max_iterations_reached=max_iterations_reached)
        recovery_text = await self._recover_final_answer(agent, worker_conversation_id, request_id, reason)
        recovered = extract_json_object(recovery_text)
        logger.info(
            "Worker final-answer recovery: reason=%s produced_json=%s output_chars=%d",
            reason,
            bool(recovered),
            len(recovery_text),
        )
        if recovered:
            recovered["recovered_from"] = reason
            self._update_progress_from_worker_result(
                session_id=session_id,
                request_id=request_id,
                parsed=recovered,
            )
            return recovered
        if recovery_text.strip() and (max_iterations_reached or not output_str.strip()):
            output_str = recovery_text

        if max_iterations_reached:
            parsed = {
                "ok": False,
                "final": output_str,
                "page": {"url": "", "title": ""},
                "screenshot": None,
                "error": "max_iterations_reached",
                "status": "partial",
            }
            self._update_progress_from_worker_result(
                session_id=session_id,
                request_id=request_id,
                parsed=parsed,
            )
            return parsed

        parsed = {
            "ok": False,
            "final": output_str,
            "page": {"url": "", "title": ""},
            "screenshot": None,
            "error": "Browser worker did not return valid JSON output",
            "status": "failed",
        }
        self._update_progress_from_worker_result(
            session_id=session_id,
            request_id=request_id,
            parsed=parsed,
        )
        return parsed

    async def _recover_final_answer(
        self, agent: ReActAgent, worker_conversation_id: str, request_id: str, reason: str
    ) -> str:
        """Ask the worker once, without tools and in the same conversation, for its final JSON."""
        try:
            with final_answer_recovery():
                result = await Runner.run_agent(
                    agent,
                    {
                        "query": recovery_message(reason),
                        "conversation_id": worker_conversation_id,
                        "request_id": request_id,
                    },
                )
        except Exception:  # noqa: BLE001 - recovery is best effort
            logger.warning("Worker final-answer recovery failed: reason=%s", reason, exc_info=True)
            return ""
        output = result.get("output") if isinstance(result, dict) else result
        return str(output) if output is not None else ""

    async def run_task_once(
        self, task: str, session_id: str, request_id: str, *, worker: Callable[[], Awaitable[ReActAgent]]
    ) -> Dict[str, Any]:
        return await self._run_task_once(task=task, session_id=session_id, request_id=request_id, worker=worker)

    @staticmethod
    def _is_max_iteration_result(parsed: Dict[str, Any]) -> bool:
        if not isinstance(parsed, dict):
            return False
        if str(parsed.get("error", "")).strip().lower() == "max_iterations_reached":
            return True
        marker = MAX_ITERATION_MESSAGE.lower()
        for key in ("final", "error"):
            value = parsed.get(key)
            if value is None:
                continue
            if marker in str(value).lower():
                return True
        return False

    @staticmethod
    def _build_resume_task(task: str, previous_final: str, progress_context: str = "") -> str:
        base = (task or "").strip()
        previous = (previous_final or "").strip()
        if len(previous) > 1200:
            previous = previous[:1200] + "...[truncated]"
        context_parts: list[str] = [
            "Continuation context:",
            "- The previous run reached max iterations before completion.",
            "- Continue from the current browser state in this same session.",
            "- Avoid repeating already completed steps unless needed for recovery.",
        ]
        if progress_context:
            context_parts.append(progress_context)
        if previous:
            context_parts.extend(
                [
                    "- Previous partial status (may be incomplete):",
                    previous,
                ]
            )
        return f"{base}\n\n" + "\n".join(context_parts)

    @staticmethod
    def _trim_text(value: Any, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) > limit:
            return text[:limit] + "...[truncated]"
        return text

    @classmethod
    def _build_failure_summary(
        cls,
        *,
        task: str,
        error: str,
        page_url: str,
        page_title: str,
        final: str,
        screenshot: Any,
        attempt: int,
        progress_state: Optional[BrowserTaskProgressState] = None,
    ) -> str:
        lines = [
            "Failure summary for continuation:",
            f"- Original task: {cls._trim_text(task, 400) or '(empty)'}",
            f"- Failed attempt: {attempt}",
            f"- Error: {cls._trim_text(error, 300) or '(unknown)'}",
        ]
        if page_url or page_title:
            lines.append(
                f"- Last page: url={cls._trim_text(page_url, 240) or '(unknown)'}, "
                f"title={cls._trim_text(page_title, 120) or '(unknown)'}"
            )
        screenshot_text = cls._trim_text(screenshot, 200)
        if screenshot_text:
            lines.append(f"- Last screenshot: {screenshot_text}")
        progress_context = cls._build_progress_context(progress_state)
        if progress_context:
            lines.append(progress_context)
        final_excerpt = cls._trim_text(final, 1200)
        if final_excerpt:
            lines.append("- Partial output excerpt:")
            lines.append(final_excerpt)
        return "\n".join(lines)

    @staticmethod
    def _build_task_with_failure_context(task: str, failure_summary: str) -> str:
        base = (task or "").strip()
        summary = (failure_summary or "").strip()
        if not summary:
            return base
        return (
            f"{base}\n\n"
            "Previous failed attempt context:\n"
            f"{summary}\n\n"
            "Continuation instructions:\n"
            "- Continue from the current browser state in this same session.\n"
            "- Do not repeat completed steps unless required for recovery.\n"
            "- Prioritize resolving the listed failure."
        )

    async def run_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
        *,
        worker: Callable[[], Awaitable[ReActAgent]],
    ) -> Dict[str, Any]:
        """Run ``task`` on the worker ``worker`` returns; after a restart the next call to it rebuilds the worker."""
        sid = self.session_new(session_id)
        rid = (request_id or "").strip() or uuid.uuid4().hex
        effective_timeout = int(timeout_s) if (timeout_s is not None and timeout_s > 0) else self.guardrails.timeout_s
        timeout_deadline = asyncio.get_running_loop().time() + float(effective_timeout)
        attempts = 2 if self.guardrails.retry_once else 1
        base_task = (task or "").strip()
        previous_failure_summary = self._failure_context_by_session.get(sid, "")

        async with self._locks[sid]:
            current_task = asyncio.current_task()
            if current_task is not None:
                self._register_inflight_task(sid, rid, current_task)
            try:
                if await self.is_cancelled(sid, rid):
                    await self.clear_cancel(sid, rid)
                    await self.clear_cancel(sid, None)
                    result = {
                        "ok": False,
                        "session_id": sid,
                        "request_id": rid,
                        "final": "",
                        "page": {"url": "", "title": ""},
                        "screenshot": None,
                        "error": "cancelled_by_frontend",
                        "attempt": 0,
                        "failure_summary": None,
                        "progress_state": None,
                    }
                    return result
                last_error: Optional[str] = None
                used_max_iteration_resume = False
                next_task = self._build_task_with_failure_context(base_task, previous_failure_summary)
                attempt_idx = 0
                max_attempts = attempts + (1 if self.guardrails.resume_on_max_iterations else 0)
                last_failure_final = ""
                last_failure_page: Dict[str, Any] = {}
                last_failure_screenshot: Any = None
                while attempt_idx < max_attempts:
                    remaining_timeout = timeout_deadline - asyncio.get_running_loop().time()
                    if remaining_timeout <= 0:
                        last_error = f"task_timeout: exceeded {effective_timeout}s"
                        break
                    try:
                        parsed = await asyncio.wait_for(
                            self.run_task_once(task=next_task, session_id=sid, request_id=rid, worker=worker),
                            timeout=remaining_timeout,
                        )
                        attempt_idx += 1
                        self._update_progress_from_worker_result(
                            session_id=sid,
                            request_id=rid,
                            parsed=parsed,
                        )
                        parsed_ok = bool(parsed.get("ok", False))
                        if not parsed_ok and self._should_treat_as_completed(parsed):
                            parsed = dict(parsed)
                            parsed["ok"] = True
                            parsed["error"] = None
                            parsed_ok = True
                        should_resume_max_iter = (
                            (not parsed_ok)
                            and self._is_max_iteration_result(parsed)
                            and self.guardrails.resume_on_max_iterations
                            and not used_max_iteration_resume
                        )
                        if not parsed_ok:
                            last_error = str(parsed.get("error") or "")
                            last_failure_final = str(parsed.get("final", ""))
                            last_failure_page = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                            last_failure_screenshot = parsed.get("screenshot")

                        if should_resume_max_iter:
                            used_max_iteration_resume = True
                            next_task = self._build_resume_task(
                                next_task,
                                str(parsed.get("final", "")),
                                progress_context=self._build_progress_context(self._progress_by_session.get(sid)),
                            )
                            last_error = str(parsed.get("error") or MAX_ITERATION_MESSAGE)
                            continue

                        if not parsed_ok and attempt_idx < attempts and self._is_retryable_runtime_result(parsed):
                            location = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                            failure_summary = self._build_failure_summary(
                                task=base_task,
                                error=str(parsed.get("error") or ""),
                                page_url=str(location.get("url", "")),
                                page_title=str(location.get("title", "")),
                                final=str(parsed.get("final", "")),
                                screenshot=parsed.get("screenshot"),
                                attempt=attempt_idx,
                                progress_state=self._progress_by_session.get(sid),
                            )
                            should_restart = self._is_retryable_transport_message(
                                failure_summary
                            ) or self._should_restart_after_runtime_result(parsed)
                            if should_restart:
                                try:
                                    await self._restart()
                                except Exception as restart_exc:
                                    last_error = f"restart_failed: {restart_exc!r}"
                                    break
                            next_task = self._build_task_with_failure_context(base_task, failure_summary)
                            continue

                        location = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                        screenshot = self._normalize_screenshot_value(parsed.get("screenshot"))
                        response = {
                            "ok": parsed_ok,
                            "session_id": sid,
                            "request_id": rid,
                            "final": str(parsed.get("final", "")),
                            "page": {
                                "url": str(location.get("url", "")),
                                "title": str(location.get("title", "")),
                            },
                            "screenshot": screenshot,
                            "error": parsed.get("error"),
                            "attempt": attempt_idx,
                            "progress_state": None,
                        }
                        if parsed.get("recovered_from"):
                            response["recovered_from"] = parsed["recovered_from"]
                        if parsed_ok:
                            self._failure_context_by_session.pop(sid, None)
                            self._progress_by_session.pop(sid, None)
                            response["failure_summary"] = None
                            return response

                        failure_summary = self._build_failure_summary(
                            task=base_task,
                            error=str(parsed.get("error") or ""),
                            page_url=str(location.get("url", "")),
                            page_title=str(location.get("title", "")),
                            final=str(parsed.get("final", "")),
                            screenshot=parsed.get("screenshot"),
                            attempt=attempt_idx,
                            progress_state=self._progress_by_session.get(sid),
                        )
                        self._failure_context_by_session[sid] = failure_summary
                        response["failure_summary"] = failure_summary
                        response["progress_state"] = self._export_progress_state(sid)
                        return response
                    except TimeoutError:
                        attempt_idx += 1
                        last_error = f"task_timeout: exceeded {effective_timeout}s"
                        if attempt_idx >= attempts or asyncio.get_running_loop().time() >= timeout_deadline:
                            break
                    except asyncio.CancelledError:
                        await self.clear_cancel(sid, rid)
                        await self.clear_cancel(sid, None)
                        result = {
                            "ok": False,
                            "session_id": sid,
                            "request_id": rid,
                            "final": "",
                            "page": {"url": "", "title": ""},
                            "screenshot": None,
                            "error": "cancelled_by_frontend",
                            "attempt": attempt_idx + 1,
                            "failure_summary": None,
                            "progress_state": None,
                        }
                        return result
                    except Exception as exc:
                        attempt_idx += 1
                        last_error = str(exc) or repr(exc)
                        if attempt_idx >= attempts:
                            break
                        # Restart before retry on known transport/session failures.
                        if (not str(exc)) or self._is_retryable_transport_error(exc):
                            try:
                                await self._restart()
                            except Exception as restart_exc:
                                last_error = f"restart_failed: {restart_exc!r}"
                                break

                await self.clear_cancel(sid, rid)
                await self.clear_cancel(sid, None)
                page_url = str(last_failure_page.get("url", "")) if isinstance(last_failure_page, dict) else ""
                page_title = str(last_failure_page.get("title", "")) if isinstance(last_failure_page, dict) else ""
                failure_summary = self._build_failure_summary(
                    task=base_task,
                    error=last_error or "unknown browser execution error",
                    page_url=page_url,
                    page_title=page_title,
                    final=last_failure_final,
                    screenshot=last_failure_screenshot,
                    attempt=min(attempt_idx, max_attempts),
                    progress_state=self._progress_by_session.get(sid),
                )
                self._failure_context_by_session[sid] = failure_summary
                result = {
                    "ok": False,
                    "session_id": sid,
                    "request_id": rid,
                    "final": "",
                    "page": {"url": "", "title": ""},
                    "screenshot": None,
                    "error": last_error or "unknown browser execution error",
                    "attempt": min(attempt_idx, max_attempts),
                    "failure_summary": failure_summary,
                    "progress_state": self._export_progress_state(sid),
                }
                return result
            finally:
                if current_task is not None:
                    self._unregister_inflight_task(sid, rid, current_task)
