# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The security rail that checks each command against the surface's policy rules before it reaches the driver."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
    SecurityDecision,
    SecurityReject,
)

from ..contract import Action, PolicyDenied, PolicyRule, RefResolver
from .page_state import BrowserPageState

Builder = Callable[[Mapping[str, Any], RefResolver], Action]


class ActionPolicy(BaseSecurityRail):
    """Denies a tool call whose arguments do not decode, or that a matching ``PolicyRule`` rejects.

    ``builders`` is read per call, so decoders added to the mapping after construction are enforced too.
    """

    supported_events = {AgentCallbackEvent.BEFORE_TOOL_CALL}

    def __init__(self, builders: Mapping[str, Builder], rules: Iterable[PolicyRule], page_state: BrowserPageState):
        super().__init__()
        self._builders = builders
        self._rules = tuple(rules)
        self._page_state = page_state

    async def run_security_check(self, security_ctx: SecurityCheckContext) -> SecurityDecision:
        inputs = security_ctx.callback_ctx.inputs
        build = self._builders.get(inputs.tool_name)
        if build is None:
            return self.allow()
        try:
            args = json.loads(inputs.tool_args) if isinstance(inputs.tool_args, str) else inputs.tool_args
            action = build(args or {}, self._page_state)
        except ValueError as error:
            return self.reject(str(error), result=PolicyDenied(str(error)))
        for rule in self._rules:
            if isinstance(action, rule.command_type):
                reason = rule.check(action, self._page_state.surface_context)
                if reason:
                    return self.reject(reason, result=PolicyDenied(reason))
        return self.allow()

    def _apply_reject(self, security_ctx: SecurityCheckContext, decision: SecurityReject) -> None:
        # Per call: parallel tool calls share ctx.extra.
        ctx = security_ctx.callback_ctx
        call_id = security_ctx.subject_id
        ctx.inputs.tool_result = decision.result
        ctx.inputs.tool_msg = ToolMessage(
            content=f"### Error\n{decision.message}",
            tool_call_id=call_id,
            metadata={"executed": False, "state_changed": False, "denied": True},
        )
        ctx.extra.setdefault("_skip_tool_calls", {})[call_id] = True


__all__ = ["ActionPolicy"]
