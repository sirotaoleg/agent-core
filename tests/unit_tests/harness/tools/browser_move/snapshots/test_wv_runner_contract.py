# coding: utf-8
"""What agent-eval's WebVoyager runner relies on: package internals, helper removal and parsed log lines."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import re
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.unit_tests.harness.tools.browser_move.snapshots import builder

_PKG = "openjiuwen.harness.tools.browser_move.playwright_runtime"

# Regexes copied verbatim from the scripts that parse agent logs.
# runs/mcp/budget.py
BUDGET_RESP = re.compile(r"CompletionUsage\((.*?)\), provider=")
BUDGET_FIELD = {
    k: re.compile(k + r"=([0-9.]+)")
    for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens", "cost")
}
# agent-eval bench/webvoyager/run.py (run_one_task)
RUN_USAGE_RE = re.compile(r"completion_tokens=(\d+),\s*prompt_tokens=(\d+),\s*total_tokens=(\d+)")
RUN_TOKEN_RE = re.compile(r"tokens=\{input=(\d+),\s*output=(\d+)(?:,\s*cache=(\d+))?\}")
# runs/mcp/analyze.py
ANALYZE_TOOL_ERROR = r"(?-i:### Error|Traceback \(most recent)"
ANALYZE_TOOL_RE = re.compile(r"name='mcp_playwright-official_(\w+)'|name='(browser_\w+)'")
ANALYZE_USAGE = r"CompletionUsage\((.*?)\), provider="

RUNNER_HELPERS = builder.RUNNER_HELPERS
# Helpers BrowserAgentRuntime.ensure_started adds to the worker; the other two are
# registered as runtime tools only, and the runner removes all five.
WORKER_ADDED_HELPERS = ("browser_probe_interactives", "browser_probe_cards", "browser_batch_interact")

WORKER_ANSWER = json.dumps(
    {
        "ok": True,
        "final": "The main heading of the page is Example Domain.",
        "page": {"url": "https://example.com/", "title": "Example Domain"},
        "screenshot": None,
        "error": None,
        "status": "completed",
    }
)

# Values the launcher (wv_mcp.ps1) and run_one_task set; distinct from the defaults.
LAUNCHER_MCP_ARGS = "-y @playwright/mcp@0.0.78 --isolated --config C:/runs/refactor-mcp/playwright-mcp.config.json"


def _runner_env(logs_dir: Path) -> dict[str, str]:
    return {
        "BROWSER_INSTRUCTION": builder.TASK_QUERY,
        "BROWSER_LOGS_DIR": str(logs_dir),
        "BROWSER_TIMEOUT_S": "321",
        "BROWSER_WORKER_TEMPERATURE": "0.7",
        "BROWSER_WORKER_TOP_P": "0.9",
        "BROWSER_GUARDRAIL_MAX_STEPS": "50",
        "PLAYWRIGHT_MCP_ARGS": LAUNCHER_MCP_ARGS,
    }


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    import os

    for key in list(os.environ):
        if any(key.upper().startswith(p) for p in builder.CLEARED_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BROWSER_RUNTIME_WORKDIR", str(tmp_path))
    return monkeypatch


@pytest.mark.parametrize(
    "module, name",
    [
        (f"{_PKG}.config", "build_runtime_settings"),
        (f"{_PKG}.runtime", "BrowserAgentRuntime"),
        (f"{_PKG}.browser_capabilities", "CORE_BROWSER_TOOL_NAMES"),
    ],
)
def test_runner_import_paths(module: str, name: str) -> None:
    assert hasattr(importlib.import_module(module), name)


def test_core_browser_tool_names() -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
        CORE_BROWSER_TOOL_NAMES,
    )

    assert CORE_BROWSER_TOOL_NAMES == (
        "browser_click",
        "browser_evaluate",
        "browser_file_upload",
        "browser_fill_form",
        "browser_find",
        "browser_handle_dialog",
        "browser_hover",
        "browser_navigate",
        "browser_navigate_back",
        "browser_press_key",
        "browser_select_option",
        "browser_snapshot",
        "browser_tabs",
        "browser_type",
    )


def test_runtime_signature() -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

    params = inspect.signature(BrowserAgentRuntime).parameters
    for name in ("provider", "api_key", "api_base", "model_name", "mcp_cfg", "guardrails", "allowed_tool_names"):
        assert name in params, name
    assert inspect.iscoroutinefunction(BrowserAgentRuntime.ensure_started)
    assert inspect.iscoroutinefunction(BrowserAgentRuntime.run_browser_task)
    assert inspect.iscoroutinefunction(BrowserAgentRuntime.shutdown)
    run_params = inspect.signature(BrowserAgentRuntime.run_browser_task).parameters
    assert "task" in run_params and "session_id" in run_params


def test_runtime_settings_fields() -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.config import RuntimeSettings

    fields = set(RuntimeSettings.__dataclass_fields__)
    assert {"provider", "api_key", "api_base", "model_name", "mcp_cfg", "guardrails"} <= fields


async def _run_one_task(result: dict[str, Any]) -> None:
    # Mirrors run_one_task: same imports, constructor arguments, helper removal, call.
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
        CORE_BROWSER_TOOL_NAMES,
    )
    from openjiuwen.harness.tools.browser_move.playwright_runtime.config import build_runtime_settings
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

    settings = build_runtime_settings()
    result["settings"] = settings
    runtime = BrowserAgentRuntime(
        provider=settings.provider,
        api_key=settings.api_key,
        api_base=settings.api_base,
        model_name=settings.model_name,
        mcp_cfg=settings.mcp_cfg,
        guardrails=settings.guardrails,
        allowed_tool_names=CORE_BROWSER_TOOL_NAMES,
    )
    try:
        await runtime.ensure_started()
        worker = runtime._service.browser_agent
        result["worker"] = worker
        result["tools_before"] = [t.name for t in await worker.ability_manager.list_tool_info()]
        result["removal_errors"] = {}
        if runtime._service and runtime._service.browser_agent and hasattr(worker, "ability_manager"):
            for name in RUNNER_HELPERS:
                try:
                    runtime._service.browser_agent.ability_manager.remove_ability(name)
                except Exception as exc:  # the runner swallows these; the test records them
                    result["removal_errors"][name] = repr(exc)
        result["tools_after"] = [t.name for t in await worker.ability_manager.list_tool_info()]
        result["answer"] = await runtime.run_browser_task(task=builder.TASK_QUERY, session_id="wv-agent")
    finally:
        await runtime.shutdown()


@pytest.fixture(scope="module")
def runner_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("wv-runner")
    captured: list[dict[str, Any]] = []
    mcp_calls: list[str] = []
    result: dict[str, Any] = {}
    env = _runner_env(workdir / "logs" / "agent")
    with (
        builder.isolated_env(workdir, extra=env),
        builder.io_stubs(captured, mcp_calls, answer=WORKER_ANSWER),
        builder.fresh_checkpointer(),
    ):

        async def run() -> None:
            await builder.reset_process_state()
            try:
                await _run_one_task(result)
            finally:
                await builder.reset_process_state()

        asyncio.run(run())
    result["captured"] = captured
    return result


def test_helpers_are_really_removed_from_the_worker(runner_run: dict[str, Any]) -> None:
    before, after = runner_run["tools_before"], runner_run["tools_after"]
    for name in WORKER_ADDED_HELPERS:
        assert name in before, f"{name} was not on the worker before removal: {before}"
    assert not runner_run["removal_errors"], runner_run["removal_errors"]
    assert not set(RUNNER_HELPERS) & set(after), after
    sent = [t["name"] for t in runner_run["captured"][0]["tools"]]
    assert not set(RUNNER_HELPERS) & set(sent), sent
    assert sorted(sent) == sorted(after)


def test_run_browser_task_answer_dict(runner_run: dict[str, Any]) -> None:
    answer = runner_run["answer"]
    assert isinstance(answer, dict)
    # Keys run.py reads: ok (PASS/FAIL), error (FAIL message), final and screenshot (judge).
    assert answer["ok"] is True, answer
    assert answer["final"] == "The main heading of the page is Example Domain."
    assert answer["error"] is None
    assert "screenshot" in answer
    json.dumps(answer, ensure_ascii=False)  # run.py writes it to output.json
    answer["_usage"] = {"input_tokens": 0}  # run.py adds its usage here


def test_runner_env_vars_keep_their_meaning(runner_run: dict[str, Any]) -> None:
    settings = runner_run["settings"]
    assert settings.provider == "openrouter"
    assert settings.model_name == builder.MODEL_NAME
    assert settings.guardrails.timeout_s == 321
    assert settings.mcp_cfg.params["timeout_s"] == 321
    assert settings.guardrails.max_steps == 50
    assert settings.mcp_cfg.params["args"][: len(LAUNCHER_MCP_ARGS.split())] == LAUNCHER_MCP_ARGS.split()
    worker = runner_run["worker"]
    assert worker.config.max_iterations == 50
    assert worker.config.model_config_obj.temperature == pytest.approx(0.7)
    assert worker.config.model_config_obj.top_p == pytest.approx(0.9)
    # BROWSER_INSTRUCTION and BROWSER_LOGS_DIR are set by the runner and not read by
    # agent-core; the run above had them set and completed.
    assert runner_run["answer"]["ok"] is True


def test_worker_tool_names_match_the_analyze_regex(runner_run: dict[str, Any]) -> None:
    for tool in runner_run["captured"][0]["tools"]:
        assert ANALYZE_TOOL_RE.search(f"name='{tool['name']}'"), tool["name"]


@pytest.mark.parametrize(
    "provider, expected_base",
    [
        # run_one_task sets MODEL_PROVIDER=openai when only MODEL_NAME is given.
        ("openai", "https://llm.example/v1"),
        # wv_mcp.ps1 sets openrouter; OPENAI_BASE_URL is not read for it.
        ("openrouter", "https://openrouter.ai/api/v1"),
    ],
)
def test_model_env_vars_keep_their_meaning(clean_env: pytest.MonkeyPatch, provider: str, expected_base: str) -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.config import build_runtime_settings

    clean_env.setenv("MODEL_NAME", "vendor/model-x")
    clean_env.setenv("MODEL_PROVIDER", provider)
    clean_env.setenv("OPENAI_API_KEY", "key-from-openai-var")
    clean_env.setenv("OPENAI_BASE_URL", "https://llm.example/v1")
    settings = build_runtime_settings()
    assert (settings.provider, settings.api_key, settings.api_base, settings.model_name) == (
        provider,
        "key-from-openai-var",
        expected_base,
        "vendor/model-x",
    )


def test_playwright_mcp_args_keep_their_meaning(clean_env: pytest.MonkeyPatch) -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.config import build_runtime_settings

    clean_env.setenv("PLAYWRIGHT_MCP_ARGS", LAUNCHER_MCP_ARGS)
    args = build_runtime_settings().mcp_cfg.params["args"]
    assert args == LAUNCHER_MCP_ARGS.split() + ["--caps=pdf,vision,devtools,config,network,storage,testing"]


_OPENROUTER_RESPONSE = {
    "id": "gen-1",
    "object": "chat.completion",
    "created": 1790000000,
    "model": builder.MODEL_NAME,
    "provider": "Anthropic",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {"name": "mcp_playwright-official_browser_click", "arguments": '{"target": "e57"}'},
                    },
                    {
                        "id": "toolu_2",
                        "type": "function",
                        "function": {"name": "browser_probe_cards", "arguments": "{}"},
                    },
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 4597,
        "completion_tokens": 123,
        "total_tokens": 4720,
        "cost": 0.0028683,
        "is_byok": False,
        "prompt_tokens_details": {"cached_tokens": 4284, "cache_write_tokens": 311},
        "completion_tokens_details": {"reasoning_tokens": 0},
    },
}


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture(scope="module")
def openrouter_call() -> dict[str, Any]:
    """One chat completion through the real OpenAI client, OpenRouter response over a mock HTTP transport."""
    import httpx
    import openai

    from openjiuwen.core.common.logging import llm_logger
    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

    std_logger = llm_logger.logger()
    handler = _Lines()
    console = next((h for h in std_logger.handlers if h.formatter is not None), None)
    if console is not None:
        handler.setFormatter(console.formatter)
    client = OpenAIModelClient(
        model_config=ModelRequestConfig(model=builder.MODEL_NAME, temperature=1.0, top_p=1.0),
        model_client_config=ModelClientConfig(
            client_provider="OpenRouter", api_key=builder.STUB_API_KEY, api_base=builder.API_BASE, verify_ssl=False
        ),
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_OPENROUTER_RESPONSE))
    fake_openai = openai.AsyncOpenAI(
        api_key=builder.STUB_API_KEY, base_url=builder.API_BASE, http_client=httpx.AsyncClient(transport=transport)
    )
    std_logger.addHandler(handler)
    previous_level = std_logger.level
    std_logger.setLevel(logging.INFO)
    try:
        with patch.object(OpenAIModelClient, "_create_async_openai_client", lambda self, timeout=None: fake_openai):
            message = asyncio.run(client.invoke([{"role": "user", "content": "hi"}]))
    finally:
        std_logger.removeHandler(handler)
        std_logger.setLevel(previous_level)
    usage_lines = [line for line in handler.lines if "CompletionUsage(" in line]
    assert usage_lines, handler.lines
    return {"message": message, "usage_line": usage_lines[0]}


def test_budget_usage_line(openrouter_call: dict[str, Any]) -> None:
    resp = BUDGET_RESP.findall(openrouter_call["usage_line"])
    assert len(resp) == 1
    fields = {k: float(m.group(1)) for k, rx in BUDGET_FIELD.items() for m in [rx.search(resp[0])] if m}
    assert fields == {
        "prompt_tokens": 4597,
        "completion_tokens": 123,
        "cached_tokens": 4284,
        "cache_write_tokens": 311,
        "cost": pytest.approx(0.0028683),
    }


def test_analyze_usage_and_tool_names(openrouter_call: dict[str, Any]) -> None:
    line = openrouter_call["usage_line"]
    usage = re.findall(ANALYZE_USAGE, line)
    assert len(usage) == 1
    assert float(re.search(r"cost=([0-9.]+)", usage[0]).group(1)) == pytest.approx(0.0028683)
    assert int(re.search(r"cached_tokens=(\d+)", usage[0]).group(1)) == 4284
    assert ANALYZE_TOOL_RE.findall(line) == [("browser_click", ""), ("", "browser_probe_cards")]


def test_run_py_completion_usage(openrouter_call: dict[str, Any]) -> None:
    assert RUN_USAGE_RE.findall(openrouter_call["usage_line"]) == [("123", "4597", "4720")]


def test_run_py_tokens_line(openrouter_call: dict[str, Any]) -> None:
    from openjiuwen.core.single_agent.agents.react_agent import log_llm_response

    class _Recorder:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
            self.lines.append(msg % args if args else msg)

    recorder = _Recorder()
    log_llm_response(recorder, openrouter_call["message"])
    matches = [m.groups() for line in recorder.lines for m in RUN_TOKEN_RE.finditer(line)]
    # No cache= part today; run.py treats it as optional.
    assert matches == [("4597", "123", None)]


def test_analyze_tool_error_signal_from_mcp_result() -> None:
    """A Playwright MCP ``### Error`` result reaches the agent unchanged."""
    from openjiuwen.harness.tools.browser_move.clients.stdio_client import BrowserMoveStdioClient
    from openjiuwen.harness.tools.browser_move.playwright_runtime.config import build_playwright_mcp_config

    error_text = "### Error\nError: locator.click: Timeout 5000ms exceeded."
    captured: list[dict[str, Any]] = []
    mcp_calls: list[str] = []

    async def call() -> Any:
        client = BrowserMoveStdioClient(build_playwright_mcp_config())
        assert await client.connect()
        try:
            return await client.call_tool("browser_click", {"target": "e57"})
        finally:
            await client.disconnect()

    with builder.io_stubs(captured, mcp_calls, mcp_replies={"browser_click": error_text}):
        result = asyncio.run(call())
    text = result if isinstance(result, str) else json.dumps(builder._plain(result), ensure_ascii=False)
    assert "### Error" in text
    assert re.search(ANALYZE_TOOL_ERROR, text, re.I)


def test_analyze_traceback_signal_format() -> None:
    # Produced by the standard library (logging with exc_info, traceback); pinned as a format.
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        text = traceback.format_exc()
    assert re.search(ANALYZE_TOOL_ERROR, text, re.I)
