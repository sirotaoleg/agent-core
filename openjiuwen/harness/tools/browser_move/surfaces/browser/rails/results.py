# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool-message rewriting: outcome, page state and observation compaction."""

from __future__ import annotations

import json
from typing import Any, Dict

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.browser_move.contract import Effect, Observe, RunScript
from openjiuwen.harness.tools.browser_move.runtime.session import ToolKind
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import CommandRecord
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.keys import (
    _BATCH_MUTATING_TARGET_OPS,
    _BROWSER_TOOL_RUNTIME_STATE_KEY,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.runner.logging import write_browser_agent_audit_artifact

_BROWSER_OBSERVATION_MESSAGE_MAX_CHARS = 12_000
_BROWSER_PAGE_STATE_TAG = "browser_page_state"


class _ResultsMixin:
    def _record_view(self, kind: ToolKind, record: CommandRecord | None, tool_result: Any) -> Dict[str, Any]:
        """One dict the evidence machinery reads: a helper's payload as is, a command's record with its text."""
        data = getattr(tool_result, "data", None)
        content = data.get("content") if isinstance(data, dict) else None
        if isinstance(content, dict):
            return content
        if record is None:
            if isinstance(data, dict):
                return data
            if isinstance(tool_result, dict):
                return tool_result
            success = getattr(tool_result, "success", None)
            return {"ok": success is not False, "error": getattr(tool_result, "error", None), "result": data}
        surface = record.surface
        return {
            "ok": record.ok,
            "error": None if record.ok else (content or record.error),
            "result": record.value if isinstance(record.action, RunScript) else content,
            "url": str(getattr(surface, "url", "") or ""),
            "title": str(getattr(surface, "title", "") or ""),
            "generation_id": record.generation,
            "page_state": self._runtime.export_page_state(),
        }

    @classmethod
    def _outcome(
        cls, kind: ToolKind, record: CommandRecord | None, tool_result: Any, result: Dict[str, Any]
    ) -> tuple[bool, bool, bool]:
        if record is not None:
            metadata = record.message_metadata
            state_changed = bool(metadata["state_changed"]) or cls._partial_batch_mutated(result)
            return bool(metadata["success"]), bool(metadata["executed"]), state_changed
        success = getattr(tool_result, "success", None)
        if success is None:
            success = result.get("ok", True) is not False and not result.get("error")
        executed = not kind.known
        return bool(success), executed, bool(executed and success and not kind.read_only)

    @staticmethod
    def _partial_batch_mutated(result: Dict[str, Any]) -> bool:
        if str(result.get("status") or "").strip().lower() != "partial":
            return False
        for step in result.get("steps") or []:
            if not isinstance(step, dict) or step.get("ok") is not True:
                continue
            if str(step.get("op") or "").strip().lower() in _BATCH_MUTATING_TARGET_OPS:
                return True
        return False

    def _tool_evidence_args(self, ctx: AgentCallbackContext, inputs: Any) -> Dict[str, Any]:
        args = dict(self._coerce_tool_args(getattr(inputs, "tool_args", None)))
        extra = getattr(ctx, "extra", None)
        runtime_states = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY) if isinstance(extra, dict) else None
        call_state = runtime_states.get(self._tool_call_id(inputs)) if isinstance(runtime_states, dict) else None
        if isinstance(call_state, dict) and call_state.get("evidence_fields"):
            args["_runtime_evidence_fields"] = list(call_state["evidence_fields"])
        return args

    @staticmethod
    def _rewrite_tool_message_contract(inputs: Any, tool_result: Dict[str, Any]) -> None:
        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return
        if not isinstance(parsed, dict):
            return
        for key in ("requested_count", "observed_count", "diagnostics"):
            parsed[key] = tool_result.get(key)
        tool_msg.content = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _set_tool_message_outcome(
        inputs: Any,
        *,
        success: bool,
        executed: bool,
        state_changed: bool,
        denied: bool,
        effect: str | None = None,
    ) -> None:
        tool_msg = getattr(inputs, "tool_msg", None)
        if tool_msg is None:
            return
        metadata = dict(tool_msg.metadata) if isinstance(tool_msg.metadata, dict) else {}
        metadata.update(
            {
                "success": success,
                "executed": executed,
                "state_changed": state_changed,
                "denied": denied,
            }
        )
        if effect is not None:
            metadata["effect"] = effect
        tool_msg.metadata = metadata

    @staticmethod
    def _compact_large_observation_message(inputs: Any, kind: ToolKind, tool_name: str, result: Any) -> None:
        """Bound one model-visible raw observation after runtime evidence extraction."""

        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        observes = isinstance(kind.action, (Observe, RunScript))
        reads = kind.helper and kind.read_only and not kind.exempt_from_budget
        if not (observes or reads):
            return
        if len(content) <= _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            return

        audit = write_browser_agent_audit_artifact("large_browser_observation", result)
        payload = {
            "ok": bool(result.get("ok")) if isinstance(result, dict) else True,
            "observation": "bounded_browser_tool_result",
            "tool": str(tool_name or "").rsplit("_", 1)[-1],
            "truncated": True,
            "original_chars": len(content),
            "preview_head": content[:6_000],
            "preview_tail": content[-2_000:],
            "note": (
                "Runtime evidence and targets were recorded before compaction. "
                "Use browser_working_context and browser_state for subsequent actions."
            ),
        }
        if audit:
            payload["audit"] = audit
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            payload["preview_head"] = content[:4_000]
            payload["preview_tail"] = content[-1_000:]
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            payload["preview_head"] = content[:2_000]
            payload.pop("preview_tail", None)
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            payload.pop("preview_head", None)
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        tool_msg.content = rendered

    def _attach_page_state(self, inputs: Any, kind: ToolKind, result: Any) -> None:
        if kind.helper:
            return
        observed = isinstance(kind.action, Observe)
        if observed and kind.action.include_screenshot:
            return
        if not observed and kind.effect is not Effect.NAVIGATE:
            return

        page_state = self._runtime.export_page_state()
        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        if observed:
            compact_observation = {
                "ok": True,
                "observation": "compact_page_state",
                "page_state": page_state,
                "audit": write_browser_agent_audit_artifact("direct_ax_observation", result),
            }
            tool_msg.content = json.dumps(
                compact_observation,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return
        marker = (
            f"\n<{_BROWSER_PAGE_STATE_TAG}>"
            f"{json.dumps(page_state, ensure_ascii=False, separators=(',', ':'))}"
            f"</{_BROWSER_PAGE_STATE_TAG}>"
        )
        if f"<{_BROWSER_PAGE_STATE_TAG}>" not in content:
            tool_msg.content = content.rstrip() + marker
