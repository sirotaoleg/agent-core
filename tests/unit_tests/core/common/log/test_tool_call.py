# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.common.logging import tool_call


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch):
    monkeypatch.setattr(tool_call, "_TOOL_CALL_LOGGERS", {})


def _is_web_tool(name: str) -> bool:
    return name.startswith("web_")


def test_no_tool_call_logger_without_registration():
    assert tool_call.tool_call_logger("web_search") is None


def test_registered_logger_serves_only_matching_tool_names():
    lines = []
    tool_call.register_tool_call_logger(_is_web_tool, lambda message, *args: lines.append(message % args))

    tool_call.tool_call_logger("web_search")("Executing tool: %s", "web_search")

    assert lines == ["Executing tool: web_search"]
    assert tool_call.tool_call_logger("read_file") is None


def test_registering_a_matcher_again_replaces_its_logger():
    first, second = [], []
    tool_call.register_tool_call_logger(_is_web_tool, lambda message, *args: first.append(message))
    tool_call.register_tool_call_logger(_is_web_tool, lambda message, *args: second.append(message))

    tool_call.tool_call_logger("web_search")("line")

    assert (first, second) == ([], ["line"])
