# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.logging import tool_call
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents import react_agent
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class _Recorder:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(message % args)

    def __call__(self, message, *args):
        self.info(message, *args)


@pytest.fixture
def core_log(monkeypatch):
    monkeypatch.setattr(tool_call, "_TOOL_CALL_LOGGERS", {})
    recorder = _Recorder()
    monkeypatch.setattr(react_agent, "logger", recorder)
    return recorder


def _execute(*tool_names: str) -> None:
    agent = ReActAgent(card=AgentCard(name="tool_log_agent", description="tool-call log routing"))
    agent.ability_manager.execute = AsyncMock(return_value=[])
    calls = [
        ToolCall(id=f"c{index}", type="function", name=name, arguments="{}") for index, name in enumerate(tool_names)
    ]
    asyncio.run(agent._execute_tool_call(MagicMock(), calls, None, MagicMock()))


_BROWSER_LINE = "Executing tool: browser_click with args: %s" % (
    ReActAgent._summarize_tool_args_for_log("browser_click", "{}"),
)


def test_tool_calls_go_to_core_logger_without_registered_logger(core_log):
    _execute("browser_click", "read_file")

    assert core_log.lines == [_BROWSER_LINE, "Executing tool: read_file with args: {}"]


def test_registered_logger_takes_matching_tool_calls(core_log):
    routed = _Recorder()
    tool_call.register_tool_call_logger(lambda name: name.startswith("browser_"), routed)

    _execute("browser_click", "read_file")

    assert routed.lines == [_BROWSER_LINE]
    assert core_log.lines == ["Executing tool: read_file with args: {}"]
