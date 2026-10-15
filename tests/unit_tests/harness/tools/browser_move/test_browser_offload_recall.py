# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for session-scoped browser offload recall."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.tool_result_window_processor import (
    ToolResultWindowProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.sys_operation import OperationMode, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import LocalWorkConfig
from openjiuwen.harness.tools.browser_move.surfaces.browser.offload_recall import BrowserOffloadRecallTool

_HANDLE = "0123456789abcdef0123456789abcdef"


def _session(session_id: str):
    return SimpleNamespace(get_session_id=lambda: session_id)


def _write_offload(tmp_path, session_id: str, content: str, *, handle: str = _HANDLE):
    offload_dir = tmp_path / "context" / f"{session_id}_context" / "offload"
    offload_dir.mkdir(parents=True)
    path = offload_dir / f"ToolResultWindowProcessor_{handle}.json"
    path.write_text(
        json.dumps(
            {
                "offload_handle": handle,
                "messages": [
                    {
                        "role": "tool",
                        "name": "browser_snapshot",
                        "tool_call_id": "call-1",
                        "content": content,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_recalls_bounded_chunk_from_current_session(tmp_path):
    content = "0123456789" * 30
    _write_offload(tmp_path, "browser-session", content)
    tool = BrowserOffloadRecallTool(str(tmp_path), language="en")

    result = await tool.invoke(
        {"handle": _HANDLE, "offset": 20, "limit": 40},
        session=_session("browser-session"),
    )

    assert result.success is True
    assert result.data["content"] == content[20:60]
    assert result.data["next_offset"] == 60
    assert result.data["has_more"] is True
    assert result.data["stale_interaction_refs"] is True


@pytest.mark.asyncio
async def test_rendered_recall_keeps_paging_metadata_next_to_content(tmp_path):
    content = "0123456789" * 30
    _write_offload(tmp_path, "browser-session", content)
    tool = BrowserOffloadRecallTool(str(tmp_path), language="en")

    result = await tool.invoke(
        {"handle": _HANDLE, "offset": 20, "limit": 40},
        session=_session("browser-session"),
    )
    text = tool.render_for_llm(result)

    assert f"handle: {_HANDLE}" in text
    assert "offset: 20" in text
    assert "next_offset: 60" in text
    assert text.endswith(content[20:60])


@pytest.mark.asyncio
async def test_query_returns_context_around_first_match(tmp_path):
    content = ("before-" * 100) + "UNIQUE TARGET" + ("-after" * 100)
    _write_offload(tmp_path, "browser-session", content)
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": _HANDLE.upper(), "query": "unique target", "limit": 700},
        session=_session("browser-session"),
    )

    assert result.success is True
    assert result.data["found"] is True
    assert result.data["match_offset"] == content.index("UNIQUE TARGET")
    assert "UNIQUE TARGET" in result.data["content"]


@pytest.mark.asyncio
async def test_does_not_read_another_session_artifact(tmp_path):
    _write_offload(tmp_path, "other-session", "secret")
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": _HANDLE},
        session=_session("browser-session"),
    )

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handle",
    [
        "../0123456789abcdef0123456789abcd",
        "ToolResultWindowProcessor_0123456789abcdef",
        "not-a-handle",
    ],
)
async def test_rejects_non_hex_or_path_like_handles(tmp_path, handle):
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": handle},
        session=_session("browser-session"),
    )

    assert result.success is False
    assert "32 hexadecimal" in result.error


@pytest.mark.asyncio
async def test_requires_runtime_session(tmp_path):
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke({"handle": _HANDLE})

    assert result.success is False
    assert "runtime session" in result.error


@pytest.mark.asyncio
async def test_recalls_what_the_window_processor_offloaded_for_a_session_id_with_colons(tmp_path):
    session_id = "wv-task:direct:req-1:0123abcd"
    card = SysOperationCard(id="recall_real_sys_op", mode=OperationMode.LOCAL, work_config=LocalWorkConfig())
    engine = ContextEngine(
        ContextEngineConfig(default_window_message_num=100),
        workspace=SimpleNamespace(root_path=str(tmp_path)),
        sys_operation=SysOperation(card=card),
    )
    context = await engine.create_context(
        context_id="recall_ctx",
        session=SimpleNamespace(get_session_id=lambda: session_id, get_state=lambda *_: None),
        history_messages=[],
        processors=[
            (
                "ToolResultWindowProcessor",
                ToolResultWindowProcessorConfig(tool_names=["browser_snapshot"], keep_last_k=1),
            )
        ],
    )
    original = "HEADING " + "x" * 3000 + " UNIQUE TARGET"
    messages = [UserMessage(content="read the page")]
    for index, content in enumerate([original, "newer snapshot"]):
        call = ToolCall(id=f"tc-{index}", name="browser_snapshot", type="function", arguments="")
        messages += [
            AssistantMessage(content="", tool_calls=[call]),
            ToolMessage(content=content, tool_call_id=call.id, name="browser_snapshot"),
        ]
    await context.add_messages(messages)
    offloaded = context.get_messages()[2]
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": offloaded.offload_handle, "query": "UNIQUE TARGET"}, session=_session(session_id)
    )

    assert offloaded.offload_type == "filesystem"
    assert result.success is True, result.error
    assert result.data["content"].endswith("UNIQUE TARGET")


@pytest.mark.asyncio
async def test_recalls_an_artifact_stored_under_the_literal_session_folder(tmp_path):
    content = "legacy " * 40
    _write_offload(tmp_path, "user@host", content)
    tool = BrowserOffloadRecallTool(str(tmp_path), language="en")

    result = await tool.invoke({"handle": _HANDLE, "offset": 0, "limit": 20}, session=_session("user@host"))

    assert result.success is True, result.error
    assert result.data["content"] == content[:20]
