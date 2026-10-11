#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextWindow
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY,
    PromptAttachmentManager,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import (
    BROWSER_TASK_STATE_KEY,
    BrowserWorkingContextStore,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_browser_state_metadata_js,
)
from openjiuwen.harness.tools.browser_move.contract import ActionResult, DriverError
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    element,
    make_runtime,
    mcp_fake_driver,
    observation,
    result,
)

_METADATA = {"page_position": {"scroll_y": 200, "pixels_below": 800}, "semantic_state": {}}


def _state(url: str) -> dict:
    return {
        "ok": True,
        "error": None,
        "url": url,
        "title": "Current page",
        "tabs": [
            {"index": 0, "current": True, "url": url, "title": "Current page"},
            {"index": 1, "current": False, "url": "https://other.example", "title": "Other"},
        ],
        "page_position": {
            "viewport_width": 1280,
            "viewport_height": 720,
            "page_width": 1280,
            "page_height": 2400,
            "scroll_x": 0,
            "scroll_y": 400,
            "pixels_above": 400,
            "pixels_below": 1280,
        },
        "page_state": {
            "page_id": "page-current",
            "generation_id": "g0",
            "url": url,
            "title": "Current page",
            "interactives": [
                {
                    "target_id": "t_g0_1",
                    "generation_id": "g0",
                    "role": "button",
                    "text": "Continue",
                    "match_count": 1,
                    "visible": True,
                    "enabled": True,
                    "actionable": True,
                }
            ],
            "cards": [],
            "field_coverage": [],
            "blockers": [],
        },
        "dom": '- button "Continue" [ref=e7]',
    }


def _window_message(window: ContextWindow, name: str) -> UserMessage:
    matches = [message for message in window.context_messages if message.name == name]
    assert len(matches) == 1
    message = matches[0]
    assert isinstance(message, UserMessage)
    return message


def test_rendered_browser_state_is_valid_json_within_configured_limit() -> None:
    config = BrowserStateContextProcessorConfig(provider=AsyncMock(), max_dom_chars=2_000)
    processor = BrowserStateContextProcessor(config)
    state = _state("https://bounded.example/" + ("u" * 2_000))
    state["page_state"]["interactives"] = [
        {
            "target_id": f"t_g0_{index}",
            "generation_id": "g0",
            "role": "button",
            "text": "x" * 1_000,
        }
        for index in range(40)
    ]
    state["semantic_state"] = {
        "form_values": {f"field-{index}": "v" * 1_000 for index in range(20)},
        "blockers": ["b" * 1_000 for _ in range(20)],
    }

    prompt = processor._format_state_text(state)
    body = prompt.split("\n", 2)[2].rsplit("\n</browser_state>", 1)[0]
    payload = json.loads(body)

    assert len(body) <= config.max_dom_chars
    assert payload["truncated"] is True
    assert payload["page_state"]["generation_id"] == "g0"


def test_rendered_browser_state_omits_task_progress_duplicates() -> None:
    processor = BrowserStateContextProcessor(
        BrowserStateContextProcessorConfig(provider=AsyncMock())
    )
    state = _state("https://shop.example/search")
    state["semantic_state"] = {
        "result_count": 10,
        "field_coverage": ["title"],
        "blockers": ["login required"],
    }
    state["page_state"]["field_coverage"] = ["title"]
    state["page_state"]["blockers"] = ["login required"]

    prompt = processor._format_state_text(state)
    body = prompt.split("\n", 2)[2].rsplit("\n</browser_state>", 1)[0]
    payload = json.loads(body)

    assert payload["semantic_state"]["result_count"] == 10
    assert "field_coverage" not in payload["semantic_state"]
    assert "blockers" not in payload["semantic_state"]
    assert "field_coverage" not in payload["page_state"]
    assert "blockers" not in payload["page_state"]
    assert payload["page_state"]["page_blockers"] == ["login required"]


def test_completed_parallel_calls_form_one_refresh_action_group() -> None:
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id="mutate", type="function", name="browser_navigate", arguments="{}"),
                ToolCall(id="probe", type="function", name="browser_probe_cards", arguments="{}"),
            ],
        ),
        ToolMessage(tool_call_id="probe", content="{}", metadata={"effect": "read"}),
        ToolMessage(tool_call_id="mutate", content="{}", metadata={"effect": "navigate"}),
    ]

    group_id, refresh_ids = BrowserStateContextProcessor._completed_refresh_action_group(messages)

    assert group_id
    assert refresh_ids == {"mutate"}


@pytest.mark.asyncio
async def test_parallel_read_probes_merge_once_without_full_browser_capture() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://example.test/initial")
    compact_state = _state("https://example.test/results")
    compact_state["page_state"]["cards"] = [
        {
            "target_id": "t_g0_2",
            "generation_id": "g0",
            "title": "Result",
        }
    ]
    provider.capture_compact_browser_state.return_value = compact_state
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-read-group-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="compare results"))
    await context.get_context_window()
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="cards", type="function", name="browser_probe_cards", arguments="{}"),
                    ToolCall(
                        id="controls",
                        type="function",
                        name="browser_probe_interactives",
                        arguments="{}",
                    ),
                ],
            ),
            ToolMessage(tool_call_id="controls", content="{}", metadata={"effect": "read"}),
            ToolMessage(tool_call_id="cards", content="{}", metadata={"effect": "read"}),
        ]
    )

    window = await context.get_context_window()

    assert provider.capture_browser_state.await_count == 1
    provider.capture_compact_browser_state.assert_awaited_once()
    assert "t_g0_2" in _window_message(window, "current_browser_state").content


@pytest.mark.asyncio
async def test_denied_browser_action_reuses_cached_state_without_observation() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://example.test/current")
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-denied-group-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="inspect current page"))
    initial_window = await context.get_context_window()
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="denied", type="function", name="browser_evaluate", arguments="{}")],
            ),
            ToolMessage(
                tool_call_id="denied",
                content='{"ok":false,"executed":false,"state_changed":false,"denied":true}',
                metadata={"effect": "script", "executed": False, "state_changed": False, "denied": True},
            ),
        ]
    )

    denied_window = await context.get_context_window()

    assert provider.capture_browser_state.await_count == 1
    assert _window_message(denied_window, "current_browser_state") is _window_message(
        initial_window,
        "current_browser_state",
    )


@pytest.mark.asyncio
async def test_failed_mutation_without_state_change_reuses_cached_state() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://example.test/current")
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-failed-mutation-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="click the result"))
    initial_window = await context.get_context_window()
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="failed", type="function", name="browser_click", arguments="{}")],
            ),
            ToolMessage(
                tool_call_id="failed",
                content='{"ok":false,"executed":true,"state_changed":false}',
                metadata={"effect": "act", "success": False, "executed": True, "state_changed": False},
            ),
        ]
    )

    failed_window = await context.get_context_window()

    assert provider.capture_browser_state.await_count == 1
    assert _window_message(failed_window, "current_browser_state") is _window_message(
        initial_window,
        "current_browser_state",
    )


@pytest.mark.asyncio
async def test_ambiguous_timeout_refreshes_state_once_for_reconciliation() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://example.test/current")
    reconciled = _state("https://example.test/next")
    reconciled["reconciliation_only"] = True
    provider.capture_reconciliation_browser_state.return_value = reconciled
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-timeout-reconcile-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="click the result"))
    await context.get_context_window()
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="timeout", type="function", name="browser_click", arguments="{}")],
            ),
            ToolMessage(
                tool_call_id="timeout",
                content='{"ok":false,"executed":true,"state_changed":true,"timed_out":true}',
                metadata={"effect": "act", "success": False, "executed": True, "state_changed": True},
            ),
        ]
    )

    window = await context.get_context_window()

    assert provider.capture_browser_state.await_count == 1
    provider.capture_reconciliation_browser_state.assert_awaited_once()
    assert "https://example.test/next" in _window_message(window, "current_browser_state").content


async def _add_completed_browser_action(
    context,
    *,
    call_id: str,
    effect: str = "script",
) -> None:
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        type="function",
                        name="browser_evaluate",
                        arguments='{"function": "() => window.scrollTo(0, document.body.scrollHeight)"}',
                    )
                ],
            ),
            ToolMessage(content="completed", tool_call_id=call_id, metadata={"effect": effect}),
        ]
    )


@pytest.mark.asyncio
async def test_processor_reuses_cached_browser_state_without_navigation() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://first.example")
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    window = ContextWindow(context_messages=[UserMessage(content="original request")])

    _, window = await processor.on_get_context_window(None, window)
    original_state_message = _window_message(window, "current_browser_state")
    _, window = await processor.on_get_context_window(None, window)

    assert provider.capture_browser_state.await_count == 1
    assert len(window.context_messages) == 2
    assert window.context_messages[0].content == "original request"

    state_message = _window_message(window, "current_browser_state")
    assert state_message is original_state_message
    assert state_message.name == "current_browser_state"
    assert state_message.metadata["browser_state_context"] is True
    assert state_message.metadata["context_message_id"] == "openjiuwen:browser-state"
    assert state_message.metadata[PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY] is True
    assert "https://first.example" in state_message.content
    assert "https://other.example" in state_message.content
    assert '"scroll_y":400' in state_message.content
    assert '"page_change"' not in state_message.content
    assert '"target_id":"t_g0_1"' in state_message.content
    assert "[ref=e7]" not in state_message.content
    assert "image_url" not in state_message.content
    assert all(message.name != "browser_state_progress" for message in window.context_messages)
    provider.capture_browser_state.assert_awaited_with()


@pytest.mark.parametrize("effect", ["act", "navigate", "script"])
@pytest.mark.asyncio
async def test_context_engine_refreshes_state_after_completed_mutating_tool(
    effect: str,
) -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://first.example"),
        _state("https://second.example"),
    ]
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    first_window = await context.get_context_window()
    cached_window = await context.get_context_window()
    assert provider.capture_browser_state.await_count == 1

    await context.add_messages(
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="mutating-call",
                    type="function",
                    name="browser_click",
                    arguments="{}",
                )
            ],
        )
    )
    pending_window = await context.get_context_window()
    assert provider.capture_browser_state.await_count == 1

    await context.add_messages(
        ToolMessage(content="completed", tool_call_id="mutating-call", metadata={"effect": effect})
    )
    refreshed_window = await context.get_context_window()
    reused_window = await context.get_context_window()

    persisted_messages = context.get_messages()
    assert len(persisted_messages) == 3
    assert persisted_messages[0].content == "original request"
    assert all(
        not message.metadata.get("browser_state_context") and not message.metadata.get("browser_state_progress_context")
        for message in persisted_messages
    )
    first_state = _window_message(first_window, "current_browser_state")
    cached_state = _window_message(cached_window, "current_browser_state")
    pending_state = _window_message(pending_window, "current_browser_state")
    refreshed_state = _window_message(refreshed_window, "current_browser_state")
    reused_state = _window_message(reused_window, "current_browser_state")
    assert "https://first.example" in first_state.content
    assert cached_state is first_state
    assert pending_state is first_state
    assert "https://second.example" in refreshed_state.content
    assert refreshed_state is not first_state
    assert reused_state is refreshed_state
    assert len(first_window.context_messages) == 2
    assert provider.capture_browser_state.await_count == 2


@pytest.mark.asyncio
async def test_context_engine_injects_new_capture_after_evaluate_and_reports_unchanged() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://same.example"),
        _state("https://same.example"),
    ]
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-unchanged-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    initial_window = await context.get_context_window()
    await _add_completed_browser_action(context, call_id="unchanged-call")
    unchanged_window = await context.get_context_window()

    initial_state = _window_message(initial_window, "current_browser_state")
    unchanged_state = _window_message(unchanged_window, "current_browser_state")
    assert unchanged_state is not initial_state
    assert unchanged_state.content == initial_state.content
    assert unchanged_window.context_messages[-1] is unchanged_state
    assert provider.capture_browser_state.await_count == 2


@pytest.mark.asyncio
async def test_persisted_prompt_attachments_remain_with_browser_state_tail() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://tail.example")
    manager = PromptAttachmentManager()
    await manager.add_section(
        session_id="browser-state-tail-test",
        section="runtime",
        kind="runtime",
        source="test.browser_state",
        content="runtime attachment",
    )
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-tail-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await manager.sync_to_context(context, "browser-state-tail-test")
    await context.add_messages(UserMessage(content="original request"))

    window = await context.get_context_window()

    attachment = window.context_messages[0]
    assert isinstance(attachment, UserMessage)
    assert "The following dynamic context" in attachment.content
    assert "runtime attachment" in attachment.content
    assert window.context_messages[-1].name == "current_browser_state"


@pytest.mark.asyncio
async def test_no_progress_count_increments_and_resets_after_changed_state() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://same.example"),
        _state("https://same.example"),
        _state("https://same.example"),
        _state("https://changed.example"),
    ]
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-progress-count-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    initial_window = await context.get_context_window()
    initial_state = _window_message(initial_window, "current_browser_state")

    await _add_completed_browser_action(context, call_id="unchanged-call-1")
    first_unchanged_window = await context.get_context_window()
    await _add_completed_browser_action(context, call_id="unchanged-call-2")
    second_unchanged_window = await context.get_context_window()
    await _add_completed_browser_action(context, call_id="changed-call", effect="act")
    changed_window = await context.get_context_window()

    first_unchanged_state = _window_message(first_unchanged_window, "current_browser_state")
    second_unchanged_state = _window_message(second_unchanged_window, "current_browser_state")
    assert first_unchanged_state is not initial_state
    assert second_unchanged_state is not first_unchanged_state
    assert first_unchanged_state.content == initial_state.content
    assert second_unchanged_state.content == first_unchanged_state.content
    assert _window_message(changed_window, "current_browser_state") is not second_unchanged_state
    assert provider.capture_browser_state.await_count == 4


@pytest.mark.asyncio
async def test_context_engine_does_not_refresh_after_read_only_browser_tool() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://first.example")
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-non-navigation-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))
    await context.get_context_window()
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id="console-call",
                        type="function",
                        name="browser_console_messages",
                        arguments="{}",
                    )
                ],
            ),
            ToolMessage(content="console messages", tool_call_id="console-call"),
        ]
    )

    window = await context.get_context_window()

    assert provider.capture_browser_state.await_count == 1
    assert "https://first.example" in _window_message(window, "current_browser_state").content


@pytest.mark.asyncio
async def test_processor_injects_explicit_unavailable_state_without_stale_image() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = RuntimeError("browser disconnected")
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    stale = UserMessage(
        name="current_browser_state",
        metadata={"browser_state_context": True},
        content=[
            {"type": "text", "text": "stale"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,STALE"},
            },
        ],
    )
    window = ContextWindow(context_messages=[stale])

    _, window = await processor.on_get_context_window(None, window)

    assert len(window.context_messages) == 1
    state_content = _window_message(window, "current_browser_state").content
    assert "browser disconnected" in state_content
    assert '"page_state":{}' in state_content
    assert "image_url" not in state_content
    assert processor._page_change == "unknown"


@pytest.mark.asyncio
async def test_processor_load_state_resets_page_change_baseline() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://first.example"),
        _state("https://second.example"),
    ]
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    window = ContextWindow(context_messages=[UserMessage(content="original request")])

    _, first_window = await processor.on_get_context_window(None, window)
    first_state = _window_message(first_window, "current_browser_state")
    assert processor._page_change == "initial"
    processor.load_state({})
    _, restored_window = await processor.on_get_context_window(None, first_window)

    restored_state = _window_message(restored_window, "current_browser_state")
    assert processor._page_change == "initial"
    assert "https://second.example" in restored_state.content
    assert restored_state is not first_state
    assert provider.capture_browser_state.await_count == 2


@pytest.mark.asyncio
async def test_runtime_combines_snapshot_with_page_metadata() -> None:
    driver = mcp_fake_driver(
        observations=[observation([element("e3", "link", "Docs")], url="https://example.test/docs", title="Docs")],
        results=[result(url="https://example.test/docs", title="Docs", value=_METADATA)],
    )
    runtime = make_runtime(driver)

    state = await runtime.capture_browser_state()

    assert driver.kinds() == ["observe", "execute"]
    assert state["ok"] is True
    assert state["url"] == "https://example.test/docs"
    assert state["dom"] == ""
    assert state["page_position"] == {"scroll_y": 200, "pixels_below": 800}
    assert state["tabs"][0]["url"] == "https://example.test/docs"
    assert state["page_state"]["interactives"][0]["text"] == "Docs"
    assert "screenshot" not in state
    target = runtime.page_state.resolve_target(generation_id="g0", ref="e3")
    assert target is not None
    assert target.locator == {"ref": "e3"}


@pytest.mark.asyncio
async def test_runtime_reuses_the_free_observation_of_the_last_command() -> None:
    driver = mcp_fake_driver(
        results=[
            ActionResult(ok=True, observation=observation([element("e5")], url="https://example.test/next")),
            result(url="https://example.test/next", value=_METADATA),
        ]
    )
    runtime = make_runtime(driver)
    tools = runtime.agent_parts(image_support=False, language="en").tools
    tool = next(t for t in tools if t.card.name.endswith("browser_click"))
    await tool.invoke({"target": "e5"})

    state = await runtime.capture_browser_state(action_group_id="click")

    assert "observe" not in driver.kinds()
    assert runtime.observations_reused == 1
    assert state["ok"] is True
    assert state["page_state"]["interactives"][0]["text"] == "Save"


@pytest.mark.asyncio
async def test_runtime_timeout_reconciliation_skips_full_snapshot() -> None:
    semantic = {"form_values": [], "selected_filters": [{"key": "sort", "value": "sales"}], "result_count": 10}
    measured = {"page_position": {}, "semantic_state": semantic}
    driver = mcp_fake_driver(results=[result(url="https://example.test/next", title="Next", value=measured)])
    runtime = make_runtime(driver)

    state = await runtime.capture_reconciliation_browser_state(action_group_id="timeout-group")

    assert driver.kinds() == ["execute"]
    assert state["ok"] is True
    assert state["reconciliation_only"] is True
    assert state["url"] == "https://example.test/next"
    assert state["semantic_progress"]["action_group_id"] == "timeout-group"


@pytest.mark.asyncio
async def test_runtime_automatic_capture_replaces_refs_after_url_generation_sync() -> None:
    driver = mcp_fake_driver(
        observations=[
            observation([element("e1", "button", "First")], url="https://example.test/first", title="First"),
            observation([element("e2", "button", "Second")], url="https://example.test/second", title="Second"),
        ],
        results=[
            result(url="https://example.test/first", value=_METADATA),
            result(url="https://example.test/second", value=_METADATA),
        ],
    )
    runtime = make_runtime(driver)

    first_state = await runtime.capture_browser_state()
    second_state = await runtime.capture_browser_state()

    assert first_state["dom"] == ""
    assert second_state["dom"] == ""
    assert first_state["page_state"]["interactives"][0]["text"] == "First"
    assert second_state["page_state"]["interactives"][0]["text"] == "Second"
    assert runtime.generation_id == "g1"
    assert runtime.page_state.resolve_target(generation_id="g1", ref="e2") is not None
    with pytest.raises(ValueError, match="Stale AX ref e1 belongs to g0"):
        runtime.page_state.resolve_target(generation_id="g1", ref="e1")


@pytest.mark.asyncio
async def test_runtime_does_not_reuse_dom_when_snapshot_capture_fails() -> None:
    driver = mcp_fake_driver(results=[result(url="https://fresh.example", title="Fresh", value=_METADATA)])
    driver.fail_next(DriverError("snapshot timeout", dispatched=True))
    runtime = make_runtime(driver)

    state = await runtime.capture_browser_state()

    assert state["ok"] is False
    assert state["dom"] == ""
    assert "snapshot timeout" in state["dom_error"]
    assert state["url"] == "https://fresh.example"
    assert "screenshot" not in state


def test_browser_state_metadata_probe_collects_tabs_and_position_without_screenshot() -> None:
    js = build_browser_state_metadata_js()

    assert "page.context().pages()" in js
    assert "page_position" in js
    assert "pixels_below" in js
    assert "form_values" in js
    assert "selected_filters" in js
    assert "result_count" in js
    assert "page.screenshot" not in js


@pytest.mark.asyncio
async def test_processor_requires_replan_for_semantic_loop_even_when_dom_changes() -> None:
    state = _state("https://shop.example/search")
    state["dom"] = '- button "Different DOM" [ref=e9]'
    state["semantic_state"] = {
        "url": "https://shop.example/search",
        "form_values": [{"key": "query", "value": "headphones"}],
        "selected_filters": [{"key": "price", "value": "0-100"}],
        "result_count": 10,
        "field_coverage": ["title"],
    }
    state["semantic_progress"] = {
        "progress": "state_revisit",
        "observable_progress": False,
        "consecutive_no_progress": 3,
        "state_revisit": True,
        "state_revisit_count": 3,
        "aba_loop": True,
        "repeated_filter_state": True,
        "replan_required": True,
        "replan_reason": ["three_semantic_state_revisits"],
    }

    class Session:
        def __init__(self) -> None:
            self.state = {
                BROWSER_TASK_STATE_KEY: BrowserRuntimeRail._build_phase_state("find headphones"),
            }

        def get_state(self, key):
            return self.state.get(key)

        def update_state(self, value):
            self.state.update(value)

    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail

    session = Session()
    state["semantic_progress"]["revision"] = 1
    BrowserWorkingContextStore.sync_semantic_progress(session, state["semantic_progress"])

    task_state = session.get_state(BROWSER_TASK_STATE_KEY)
    assert task_state["semantic_progress"]["aba_loop"] is True
    assert task_state["replan_required"] is True
    assert task_state["status"] == "replan_required"
    assert task_state["next_action_class"] == "materially_different_strategy"
