# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser worker prompt may only name tools the worker receives on that request."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.harness.tools.browser_move.runtime.agents import build_browser_worker_system_prompt
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.runtime_tools import (
    BrowserCustomActionTool,
    BrowserListActionsTool,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import (
    CORE_BROWSER_TOOL_NAMES,
)
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import MCP_PREFIX, make_runtime

# The runtime tools a caller may still add to the worker by card.
RUNTIME_TOOLS = {
    "browser_custom_action": BrowserCustomActionTool,
    "browser_list_custom_actions": BrowserListActionsTool,
}
# The helpers the service gives the worker from the factory's extra tools.
RUNTIME_WORKER_HELPERS = ("browser_probe_interactives", "browser_probe_cards", "browser_batch_interact")
HELPER_TOOLS = (*RUNTIME_WORKER_HELPERS, *RUNTIME_TOOLS)
# Only mentioned in conditional wording ("only when it is present").
CONDITIONAL_MENTIONS = {"browser_run_code", "browser_take_screenshot"}
# Prompt with the full tool surface at 18b840cf; it must stay byte-identical.
FULL_SURFACE_PROMPT_SHA256 = "a814c53b1b2571d807ff0bba21411541397f281390bdb82497a31554fdeb9f89"

_FINAL_JSON = json.dumps(
    {"ok": True, "final": "done", "page": {"url": "", "title": ""}, "screenshot": None, "error": None}
)


def _render(tool_names: tuple[str, ...] | None) -> str:
    return build_browser_worker_system_prompt(
        "screenshots", "artifacts", tool_names=tool_names, driver_name="Playwright MCP"
    )


def _mentioned_tools(prompt: str) -> set[str]:
    return set(re.findall(r"browser_\w+", prompt)) - CONDITIONAL_MENTIONS


class _RecordingLLM:
    """Answers every request with final JSON and records what the model was sent."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requests.append(kwargs)
        return AssistantMessage(content=_FINAL_JSON)


def _run_worker_request(helpers_added: tuple[str, ...], helpers_removed: tuple[str, ...]) -> tuple[str, set[str]]:
    """Build the worker as the service does, run one task, return (system prompt, tool names) of the request."""
    runtime = make_runtime(image_support=None, core_settings=CoreSettings(driver="playwright_mcp"))
    asyncio.run(runtime.ensure_started())
    service = runtime.service
    worker = service.browser_agent
    llm = _RecordingLLM()
    worker.set_llm(llm)
    for name in RUNTIME_WORKER_HELPERS:
        if name not in helpers_added:
            worker.ability_manager.remove_ability(name)
    for name in helpers_added:
        if name in RUNTIME_TOOLS:
            worker.ability_manager.add(RUNTIME_TOOLS[name](MagicMock()).card)
    # Mirrors the WebVoyager harness, which strips helpers after ensure_started().
    for name in helpers_removed:
        worker.ability_manager.remove_ability(name)

    async def current() -> Any:
        return service.browser_agent

    result = asyncio.run(
        service.run_task_once(task="Open example.com", session_id="s-1", request_id="r-1", worker=current)
    )

    assert result["ok"] is True
    assert llm.requests, "the worker never called the model"
    request = llm.requests[0]
    system_prompt = "\n".join(
        str(message.content) for message in request["messages"] if getattr(message, "role", "") == "system"
    )
    tool_names = {tool.name.removeprefix(MCP_PREFIX) for tool in request["tools"] or []}
    return system_prompt, tool_names


class TestWorkerRequestPromptMatchesTools:
    def test_narrowed_surface_prompt_names_no_helper(self) -> None:
        system_prompt, tool_names = _run_worker_request(
            helpers_added=RUNTIME_WORKER_HELPERS,
            helpers_removed=tuple(HELPER_TOOLS),
        )

        assert tool_names == set(CORE_BROWSER_TOOL_NAMES)
        for fragment in (*HELPER_TOOLS, "generation_id", "target_id"):
            assert fragment not in system_prompt, f"{fragment!r} leaked into the narrowed worker prompt"

    @pytest.mark.parametrize(
        "helpers_added, helpers_removed",
        [
            pytest.param(tuple(HELPER_TOOLS), (), id="all-helpers"),
            pytest.param(RUNTIME_WORKER_HELPERS, (), id="runtime-default"),
            pytest.param(RUNTIME_WORKER_HELPERS, tuple(HELPER_TOOLS), id="harness-narrowed"),
            pytest.param(("browser_probe_cards",), (), id="cards-only"),
        ],
    )
    def test_every_named_tool_is_sent(
        self,
        helpers_added: tuple[str, ...],
        helpers_removed: tuple[str, ...],
    ) -> None:
        system_prompt, tool_names = _run_worker_request(helpers_added, helpers_removed)

        missing = _mentioned_tools(system_prompt) - tool_names
        assert not missing, f"prompt names tools the model was not given: {sorted(missing)}"

    def test_runtime_default_surface_does_not_name_custom_action(self) -> None:
        # The service gives the worker the probes and the batch, never browser_custom_action.
        system_prompt, tool_names = _run_worker_request(RUNTIME_WORKER_HELPERS, ())

        assert set(RUNTIME_WORKER_HELPERS) <= tool_names
        assert "browser_custom_action" not in tool_names
        assert "browser_custom_action" not in system_prompt
        assert "browser_probe_cards before broad extraction" in system_prompt
        assert "call browser_batch_interact before falling back" in system_prompt


class TestWorkerPromptRendering:
    def test_full_surface_prompt_is_unchanged(self) -> None:
        full_surface = CORE_BROWSER_TOOL_NAMES + tuple(HELPER_TOOLS)

        prompt = _render(full_surface)

        assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == FULL_SURFACE_PROMPT_SHA256
        assert prompt == _render(None) == build_browser_worker_system_prompt(driver_name="Playwright MCP")
        assert (
            "browser_batch_interact is a standalone runtime helper like browser_probe_interactives and "
            "browser_probe_cards, not a browser_custom_action wrapper. Use it directly. "
        ) in prompt

    def test_core_surface_prompt_names_only_core_tools(self) -> None:
        prompt = _render(CORE_BROWSER_TOOL_NAMES)

        assert _mentioned_tools(prompt) <= set(CORE_BROWSER_TOOL_NAMES)
        assert "generation_id" not in prompt
        assert "target_id" not in prompt
        assert "PageState" not in prompt
        assert "Final output MUST be a single JSON object" in prompt
        assert "Use a browser_run_code tool only when it is present" in prompt

    def test_partial_surface_renders_only_present_helper(self) -> None:
        prompt = _render(CORE_BROWSER_TOOL_NAMES + ("browser_probe_cards",))

        assert "call browser_probe_cards before broad extraction" in prompt
        assert "Use browser_probe_cards to identify compact repeated structures" in prompt
        assert "PageState contract" in prompt
        assert "browser_probe_interactives" not in prompt
        assert "browser_batch_interact" not in prompt
        assert "browser_custom_action" not in prompt
        assert _mentioned_tools(prompt) <= set(CORE_BROWSER_TOOL_NAMES) | {"browser_probe_cards"}
