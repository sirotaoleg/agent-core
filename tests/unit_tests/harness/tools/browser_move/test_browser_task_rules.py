# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The task rules block: today's date and one rule per Phase 10 failure, read by the worker and the main agent."""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.surfaces.browser import tools as tools_module
from openjiuwen.harness.tools.browser_move.surfaces.browser.rails.runtime_rail import BrowserRuntimeRail
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import (
    browser_task_rules,
    build_browser_worker_system_prompt,
)

FixedDate = type("FixedDate", (datetime.date,), {"today": classmethod(lambda cls: cls(2026, 10, 12))})


@pytest.fixture
def fixed_today():
    with patch.object(tools_module.datetime, "date", FixedDate):
        yield


@pytest.mark.parametrize("language", ["en", "cn"])
def test_the_rules_carry_today_and_stay_short(fixed_today, language: str) -> None:
    rules = browser_task_rules()[language]

    assert "2026-10-12" in rules
    assert len(rules.splitlines()) <= 15


@pytest.mark.parametrize(
    "phrase",
    [
        "A date without a year means its next occurrence",
        "Apply every filter, category and option the task names",
        "never answer that from memory",
        "When the task names a source, open that source",
        "Perform every UI step the task names",
        "When the question has two readings, answer both",
        "query it again disambiguated",
        "without caveats that undermine it",
        "Do not retry a hand-built search URL more than twice",
    ],
)
def test_each_rule_is_in_the_block(phrase: str) -> None:
    assert phrase in browser_task_rules()["en"]


def test_the_worker_prompt_carries_the_rules(fixed_today) -> None:
    prompt = build_browser_worker_system_prompt(driver_name="Playwright MCP")

    assert browser_task_rules()["en"] in prompt
    assert prompt.index("Task rules:") < prompt.index("Final output MUST be a single JSON object")


def test_the_main_agent_gets_the_rules_each_model_call(fixed_today) -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    runtime.service._progress_by_session = {}
    builder = SystemPromptBuilder(language="en")
    agent = MagicMock()
    agent.system_prompt_builder = builder
    ctx = AgentCallbackContext(
        agent=agent, session=None, inputs=ModelCallInputs(messages=[UserMessage(content="Book a hotel")])
    )

    asyncio.run(BrowserRuntimeRail(runtime).before_model_call(ctx))

    assert browser_task_rules()["en"] in builder.build()
