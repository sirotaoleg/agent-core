# coding: utf-8
"""Build the tool/prompt snapshot of the Playwright MCP path.

A view records exactly what the model receives on its first call: the tool list
(name, description, argument schema) and the system prompt, for every agent the
model talks to on that path. Views:

* one per entry of ``DEFAULT_BROWSER_CAPABILITIES``: ``create_browser_agent`` with
  ``browser_capabilities=[name]``; the model talks to that agent (role ``main``);
* ``runner``: ``BrowserAgentRuntime`` built and used exactly as ``run_one_task`` in
  agent-eval's ``bench/webvoyager/run.py`` does, including its helper removal; the
  model talks to the Playwright worker (role ``worker``).

Only the I/O boundary is stubbed; the code that builds tools and prompts runs for real:

* LLM client: ``create_model_client`` returns ``CapturingClient``, which records each
  request and answers with fixed text. Image-modality probe calls are refused, so the
  probe stays inconclusive and the image-capability prompt section is always the
  "unverified" variant (decision T4b-Q3).
* MCP transport: ``mcp.client.stdio.stdio_client`` and ``mcp.ClientSession`` are
  replaced by ``FakeMcpSession``. ``tools/list`` answers with the real
  ``@playwright/mcp@0.0.78`` tool list saved in ``fixtures/``; tool calls answer with a
  blank page. ``BrowserMoveStdioClient`` (which turns the list into tool cards) is real.
* Chrome: the default ``remote`` driver mode never launches Chrome;
  ``ManagedBrowserDriver.start`` raises if anything tries.

JSON format, placeholders and conventions (role labels, language ``en``, OpenRouter
model config, fixed query) are the ones of the browser-use snapshot recorded on
857239db (``golden/browser_use``), so the two can be compared from Phase 4 on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE / "golden" / "playwright_mcp"
BROWSER_USE_GOLDEN_DIR = HERE / "golden" / "browser_use"
MCP_TOOLS_FIXTURE = HERE / "fixtures" / "playwright-mcp-0.0.78-tools.all-caps.json"
REGENERATE_COMMAND = "uv run python -m tests.unit_tests.harness.tools.browser_move.snapshots.regenerate"

DRIVER = "playwright_mcp"
RUNNER_VIEW = "runner"

# Baseline model config (plan 1.2). Values are only carried in configs; nothing is sent.
MODEL_NAME = "anthropic/claude-sonnet-5.5"
API_BASE = "https://openrouter.ai/api/v1"
STUB_API_KEY = "stub-key-not-used"
LANGUAGE = "en"
TASK_QUERY = (
    "Go to https://example.com and complete this task:\n\n"
    "Report the main heading of the page.\n\n"
    "When finished, provide your final answer as text."
)
STUB_ANSWER = "The main heading of the page is Example Domain."
MAX_STUB_CALLS = 8

RUNNER_HELPERS = (
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_batch_interact",
    "browser_custom_action",
    "browser_list_custom_actions",
)

# Environment prefixes cleared for a view so the machine's own settings never leak in.
CLEARED_ENV_PREFIXES = (
    "BROWSER_",
    "PLAYWRIGHT_",
    "MODEL_",
    "OPENAI_",
    "OPENROUTER_",
    "LLM_",
    "OPENJIUWEN_",
    "ANTHROPIC_",
    "DASHSCOPE_",
    "SILICONFLOW_",
    "JUDGE_",
    "WV_",
    "API_",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "AGENT_CORE_DIR",
)

_BLANK_SNAPSHOT = "### Page\n- Page URL: about:blank\n- Page Title: \n### Snapshot\n```yaml\n\n```"
_BLANK_TABS = "### Open tabs\n- 0: (current) [] (about:blank)"


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def view_env(workdir: Path) -> dict[str, str]:
    """Variables set for every view (the runner view reads them through build_runtime_settings)."""
    return {
        "PYTHONHASHSEED": "0",
        "BROWSER_RUNTIME_WORKDIR": str(workdir),
        "BROWSER_TIMEOUT_S": "600",
        # Never executed (the MCP transport is stubbed); it only has to be an existing file.
        "PLAYWRIGHT_MCP_COMMAND": sys.executable,
        "MODEL_PROVIDER": "openrouter",
        "MODEL_NAME": MODEL_NAME,
        "OPENROUTER_API_KEY": STUB_API_KEY,
        "OPENAI_API_KEY": STUB_API_KEY,
        "OPENAI_BASE_URL": API_BASE,
    }


@contextlib.contextmanager
def isolated_env(workdir: Path, extra: dict[str, str] | None = None) -> Iterator[None]:
    """Clear machine settings, set the view variables and cwd; restore everything after."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    try:
        for key in list(os.environ):
            if any(key.upper().startswith(p) for p in CLEARED_ENV_PREFIXES):
                del os.environ[key]
        os.environ.update(view_env(workdir))
        os.environ.update(extra or {})
        os.chdir(workdir)
        yield
    finally:
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)


# ---------------------------------------------------------------------------
# stubs at the I/O boundary
# ---------------------------------------------------------------------------


def load_mcp_tools() -> list[dict[str, Any]]:
    return json.loads(MCP_TOOLS_FIXTURE.read_text(encoding="utf-8"))


class FakeMcpSession:
    """Stands in for ``mcp.ClientSession`` over the Playwright MCP stdio pipe."""

    tools: list[dict[str, Any]] = []
    calls: list[str] = []
    replies: dict[str, str] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeMcpSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def initialize(self) -> Any:
        self.calls.append("initialize")
        return None

    async def list_tools(self) -> Any:
        from mcp import types

        self.calls.append("tools/list")
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in self.tools])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        from mcp import types

        del arguments, kwargs
        self.calls.append(name)
        text = self.replies.get(name, "### Result\nok")
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


@contextlib.asynccontextmanager
async def _fake_stdio_client(*args: Any, **kwargs: Any):
    del args, kwargs
    yield None, None


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _tool_dict(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        fn = tool.get("function", tool)
        name, description, parameters = fn.get("name"), fn.get("description"), fn.get("parameters")
    else:
        name, description, parameters = tool.name, tool.description, tool.parameters
    if isinstance(parameters, type) and hasattr(parameters, "model_json_schema"):
        parameters = parameters.model_json_schema()
    return {"name": name, "description": description, "parameters": _plain(parameters)}


def _make_capturing_client_factory(captured: list[dict[str, Any]], answer: str):
    from openjiuwen.core.foundation.llm import AssistantMessage
    from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
    from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

    def _is_probe(messages: Any) -> bool:
        return "image_url" in json.dumps(_plain(messages), ensure_ascii=False)

    class CapturingClient(BaseModelClient):
        __client_name__ = "snapshot-capture"

        def _record(self, messages: Any, tools: Any) -> AssistantMessage:
            if _is_probe(messages):
                raise RuntimeError("snapshot stub: image probe refused (no network)")
            if len(captured) >= MAX_STUB_CALLS:
                raise RuntimeError("snapshot stub: call budget exhausted")
            captured.append(
                {
                    "client_provider": str(getattr(self.model_client_config, "client_provider", "")),
                    "messages": _plain(messages),
                    "tools": [_tool_dict(t) for t in (tools or [])],
                }
            )
            return AssistantMessage(content=answer)

        async def invoke(self, messages, *, tools=None, **kwargs):
            return self._record(messages, tools)

        async def stream(self, messages, *, tools=None, **kwargs):
            msg = self._record(messages, tools)
            yield AssistantMessageChunk(content=msg.content)

        async def generate_image(self, *args, **kwargs):
            raise RuntimeError("snapshot stub: generate_image not supported")

        async def generate_speech(self, *args, **kwargs):
            raise RuntimeError("snapshot stub: generate_speech not supported")

        async def generate_video(self, *args, **kwargs):
            raise RuntimeError("snapshot stub: generate_video not supported")

    def fake_create_model_client(client_config, model_config):
        return CapturingClient(model_config=model_config, model_client_config=client_config)

    return fake_create_model_client


def _refuse_chrome_start(self, *args: Any, **kwargs: Any) -> str:
    raise AssertionError("snapshot stub: Chrome must not be started")


@contextlib.contextmanager
def io_stubs(
    captured: list[dict[str, Any]],
    mcp_calls: list[str],
    answer: str = STUB_ANSWER,
    mcp_replies: dict[str, str] | None = None,
) -> Iterator[None]:
    """Stub the LLM client, the MCP transport and Chrome start; restore them after."""
    import mcp
    import mcp.client.stdio

    from openjiuwen.core.foundation.llm import model as model_mod
    from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import ManagedBrowserDriver

    session_cls = type(
        "FakeMcpSession",
        (FakeMcpSession,),
        {
            "tools": load_mcp_tools(),
            "calls": mcp_calls,
            "replies": {"browser_snapshot": _BLANK_SNAPSHOT, "browser_tabs": _BLANK_TABS, **(mcp_replies or {})},
        },
    )
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch.object(model_mod, "create_model_client", _make_capturing_client_factory(captured, answer))
        )
        stack.enter_context(patch.object(mcp, "ClientSession", session_cls))
        stack.enter_context(patch.object(mcp.client.stdio, "stdio_client", _fake_stdio_client))
        stack.enter_context(patch.object(ManagedBrowserDriver, "start", _refuse_chrome_start))
        yield


@contextlib.contextmanager
def fresh_checkpointer() -> Iterator[None]:
    """Give the build its own in-memory checkpointer.

    Agent sessions are checkpointed per conversation and AgentCard id; a second build
    in the same process would otherwise restore the first build's finished task.
    """
    from openjiuwen.core.session.checkpointer import checkpointer as checkpointer_mod
    from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer

    factory = checkpointer_mod.CheckpointerFactory
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(checkpointer_mod, "default_inmemory_checkpointer", InMemoryCheckpointer()))
        stack.enter_context(patch.object(factory, "_default_checkpointer", None))
        stack.enter_context(patch.object(factory, "_type_checkpointers", {}))
        yield


async def reset_process_state() -> None:
    """Drop process-global state a previous view may have left behind."""
    from openjiuwen.core.runner import Runner
    from openjiuwen.harness.image_modality_probe import reset_image_support_cache

    reset_image_support_cache()
    for server_id in ("playwright_official_stdio",):
        with contextlib.suppress(Exception):
            await Runner.resource_mgr.remove_mcp_server(server_id=server_id, ignore_exception=True)
    with contextlib.suppress(Exception):
        await Runner.stop()


# ---------------------------------------------------------------------------
# snapshot document
# ---------------------------------------------------------------------------


def _role(message: dict[str, Any]) -> str:
    return str(message.get("role") or "")


def _content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def agent_entry(role: str, call: dict[str, Any]) -> dict[str, Any]:
    system = [_content_text(m) for m in call["messages"] if _role(m) == "system"]
    return {
        "role": role,
        "tools": sorted(call["tools"], key=lambda t: t["name"]),
        "system_prompt": "\n\n".join(system),
    }


def stabilize(doc: Any, workdir: Path) -> Any:
    """Replace run-dependent values with the placeholders used by the 857239db snapshot."""
    home = Path.home()
    literal = [
        (str(workdir), "<WORKDIR>"),
        (workdir.as_posix(), "<WORKDIR>"),
        (str(home), "<HOME>"),
        (home.as_posix(), "<HOME>"),
    ]
    patterns = [
        (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:?\d{2}|Z)?"), "<DATETIME>"),
        (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
        (re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b"), "<TIME>"),
        (re.compile(r"\b[0-9a-f]{32}\b"), "<HEX32>"),
        (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),
        (re.compile(r"(127\.0\.0\.1|localhost):\d{2,5}"), r"\1:<PORT>"),
    ]

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            for old, new in literal:
                for variant in {old, old.lower()}:
                    value = value.replace(variant, new)
            for rx, new in patterns:
                value = rx.sub(new, value)
            return value
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {fix(k): fix(v) for k, v in value.items()}
        return value

    return fix(doc)


def render(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def list_views() -> list[str]:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
        DEFAULT_BROWSER_CAPABILITIES,
    )

    return [c.name for c in DEFAULT_BROWSER_CAPABILITIES] + [RUNNER_VIEW]


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------


async def _capability_view(view: str, captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.runner import Runner
    from openjiuwen.harness.subagents import create_browser_agent

    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenRouter",
            api_key=STUB_API_KEY,
            api_base=API_BASE,
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model=MODEL_NAME, temperature=1.0, top_p=1.0),
    )
    agent = create_browser_agent(model, language=LANGUAGE, browser_capabilities=[view])
    await Runner.start()
    try:
        await agent.ensure_initialized()
        result = await Runner.run_agent(agent, {"query": TASK_QUERY, "conversation_id": f"snapshot-{view}"})
    finally:
        await Runner.stop()
    if not captured:
        raise RuntimeError(f"no model call captured for view {view!r}; result={result!r}")
    return [agent_entry("main", captured[0])]


async def _runner_view(captured: list[dict[str, Any]], notes: dict[str, Any]) -> list[dict[str, Any]]:
    # Same imports, constructor arguments, helper removal and call as run_one_task.
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
        CORE_BROWSER_TOOL_NAMES,
    )
    from openjiuwen.harness.tools.browser_move.playwright_runtime.config import build_runtime_settings
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

    settings = build_runtime_settings()
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
        if runtime._service and worker and hasattr(worker, "ability_manager"):
            for name in RUNNER_HELPERS:
                try:
                    worker.ability_manager.remove_ability(name)
                except Exception:  # the runner swallows these too
                    pass
        notes["answer"] = await runtime.run_browser_task(task=TASK_QUERY, session_id="wv-snapshot")
    finally:
        await runtime.shutdown()
    if not captured:
        raise RuntimeError(f"no model call captured for the runner view; answer={notes.get('answer')!r}")
    return [agent_entry("worker", captured[0])]


async def _build_view_async(view: str, captured: list[dict[str, Any]], notes: dict[str, Any]) -> list[dict[str, Any]]:
    await reset_process_state()
    try:
        if view == RUNNER_VIEW:
            return await _runner_view(captured, notes)
        return await _capability_view(view, captured)
    finally:
        await reset_process_state()


def build_view(view: str, workdir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one view; return the stabilized snapshot document and debugging notes."""
    workdir.mkdir(parents=True, exist_ok=True)
    captured: list[dict[str, Any]] = []
    mcp_calls: list[str] = []
    notes: dict[str, Any] = {}
    with isolated_env(workdir), io_stubs(captured, mcp_calls), fresh_checkpointer():
        agents = asyncio.run(_build_view_async(view, captured, notes))
    notes.update({"model_calls": len(captured), "mcp_calls": sorted(set(mcp_calls)), "captured": captured})
    doc = {"driver": DRIVER, "view": view, "agents": agents}
    return stabilize(doc, workdir), notes


def build_all(work_root: Path) -> dict[str, str]:
    """Render every view; keys are view names, values are golden-file texts."""
    return {view: render(build_view(view, work_root / view)[0]) for view in list_views()}
