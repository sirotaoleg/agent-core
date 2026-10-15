# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent builders for the runtime and the worker."""

from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable, Iterable

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from ..contract import DriverInfo, SurfaceBundle
from .assembly import build_agent_parts, core_rails
from .config import CoreSettings
from .driver_session import DriverSession
from .rails.final_answer_recovery import FinalAnswerRecoveryRail

WORKER_LANGUAGE = "en"
ToolResultObserver = Callable[[str, Any], Awaitable[None]]


def _format_tool_names(tool_call: Any) -> str:
    if isinstance(tool_call, list):
        names = [getattr(item, "name", "") for item in tool_call]
        names = [name for name in names if name]
        return ", ".join(names) if names else "<unknown>"
    name = getattr(tool_call, "name", "")
    return name or "<unknown>"


def _looks_like_tool_call(value: Any) -> bool:
    if isinstance(value, list):
        return all(hasattr(item, "name") for item in value)
    return hasattr(value, "name")


def _drop_none_tool_arguments(tool_call: Any) -> None:
    calls = tool_call if isinstance(tool_call, list) else [tool_call]
    for current_call in calls:
        raw_arguments = getattr(current_call, "arguments", None)
        if not isinstance(raw_arguments, str):
            continue
        try:
            parsed_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            continue

        cleaned_arguments = SchemaUtils.remove_none_values(parsed_arguments)
        if cleaned_arguments is None:
            cleaned_arguments = {}
        if cleaned_arguments != parsed_arguments:
            current_call.arguments = json.dumps(cleaned_arguments, ensure_ascii=False)


def _normalize_execute_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, Any, Any, Any, dict[str, Any]]:
    """Accept both legacy and current ability_manager.execute call shapes."""
    ctx = kwargs.get("ctx")
    tool_call = kwargs.get("tool_call")
    session = kwargs.get("session")
    tag = kwargs.get("tag")
    extra_kwargs = {k: v for k, v in kwargs.items() if k not in {"ctx", "tool_call", "session", "tag"}}
    positional = list(args)

    if tool_call is None and positional:
        if len(positional) >= 3 and not _looks_like_tool_call(positional[0]):
            if ctx is None:
                ctx = positional.pop(0)
        elif len(positional) >= 4 and ctx is None:
            ctx = positional.pop(0)

    if tool_call is None and positional:
        tool_call = positional.pop(0)
    if session is None and positional:
        session = positional.pop(0)
    if tag is None and positional:
        tag = positional.pop(0)

    return ctx, tool_call, session, tag, extra_kwargs


async def _notify_tool_results(
    tool_call: Any,
    results: Any,
    observer: ToolResultObserver,
) -> None:
    calls = tool_call if isinstance(tool_call, list) else [tool_call]
    result_items = results if isinstance(results, list) else []
    for idx, current_call in enumerate(calls):
        if idx >= len(result_items):
            break
        current_result = result_items[idx]
        tool_result = current_result[0] if isinstance(current_result, tuple) and current_result else current_result
        tool_name = getattr(current_call, "name", "") or "<tool>"
        await observer(tool_name, tool_result)


def ensure_execute_signature_compat(
    agent: ReActAgent,
    *,
    tool_result_observer: ToolResultObserver | None = None,
    tool_timeout_s: float | None = None,
) -> None:
    """Wrap ability_manager.execute with signature compatibility and timeout."""
    execute_fn = getattr(agent.ability_manager, "execute", None)
    if execute_fn is None:
        return
    if getattr(execute_fn, "_playwright_timeout_wrapped", False):
        return

    try:
        params = inspect.signature(execute_fn).parameters
    except (TypeError, ValueError):
        return

    original_execute = execute_fn
    supports_ctx = "ctx" in params
    supports_tag = "tag" in params
    supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    if tool_timeout_s is None:
        tool_timeout_s = CoreSettings.from_process_env().tool_timeout_s

    async def execute_with_timeout(*args, **kwargs):
        ctx, tool_call, session, tag, extra_kwargs = _normalize_execute_call(args, kwargs)
        tool_names = _format_tool_names(tool_call)
        _drop_none_tool_arguments(tool_call)
        call_kwargs: dict[str, Any] = {}
        if supports_ctx:
            call_kwargs["ctx"] = ctx
        if "tool_call" in params:
            call_kwargs["tool_call"] = tool_call
        if "session" in params:
            call_kwargs["session"] = session
        if supports_tag:
            call_kwargs["tag"] = tag
        if supports_kwargs:
            call_kwargs.update(extra_kwargs)
        try:
            with anyio.fail_after(tool_timeout_s):
                results = await original_execute(**call_kwargs)
        except TimeoutError as exc:
            logger.error(f"Tool execution timed out after {tool_timeout_s:.1f}s; tools={tool_names}")
            raise RuntimeError(f"tool_execution_timeout: tools={tool_names}, timeout_s={tool_timeout_s:.1f}") from exc
        if tool_result_observer is not None:
            await _notify_tool_results(tool_call, results, tool_result_observer)
        return results

    agent.ability_manager.execute = execute_with_timeout
    setattr(agent.ability_manager.execute, "_playwright_timeout_wrapped", True)


def _normalize_output_subdir(subdir: str, default: str) -> str:
    return (subdir or default).strip().replace("\\", "/").strip("/") or default


def worker_driver_name(info: DriverInfo) -> str:
    return info.display_name or info.name


def default_worker_prompt(
    screenshot_subdir: str,
    artifacts_subdir: str,
    *,
    tool_names: tuple[str, ...] | None = None,
    driver_name: str,
) -> str:
    """The worker's system prompt for a surface that brings none."""
    tools = f" ({', '.join(tool_names)})" if tool_names else ""
    return (
        f"You are a worker agent. Complete the task with the {driver_name} tools{tools} only. "
        f"Save screenshots under {screenshot_subdir}/ and other files under {artifacts_subdir}/. "
        "When the task is done, answer with the result."
    )


# pylint: disable=too-many-arguments
def build_browser_worker_agent(
    provider: str,
    api_key: str,
    *,
    api_base: str,
    model_name: str,
    max_steps: int,
    tools: Iterable[Tool] = (),
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
    tool_result_observer: ToolResultObserver | None = None,
    settings: CoreSettings | None = None,
    worker_prompt: Callable[..., str],
    driver_name: str,
) -> ReActAgent:
    """The worker ReActAgent with ``tools`` as its abilities; ``worker_prompt`` renders its system prompt."""
    screenshot_subdir = _normalize_output_subdir(screenshot_subdir, "screenshots")
    artifacts_subdir = _normalize_output_subdir(artifacts_subdir, "artifacts")
    card = AgentCard(
        id="agent.playwright.browser_worker",
        name="playwright_browser_worker",
        description=f"Worker that executes tasks using {driver_name} tools.",
        input_params={},
    )
    config = (
        ReActAgentConfig()
        .configure_model_client(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )
        .configure_max_iterations(max_steps)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": worker_prompt(screenshot_subdir, artifacts_subdir, driver_name=driver_name),
                }
            ]
        )
    )
    settings = settings or CoreSettings.from_process_env()
    if config.model_config_obj is not None:
        config.model_config_obj.temperature = settings.worker_temperature
        config.model_config_obj.top_p = settings.worker_top_p
    agent = ReActAgent(card=card).configure(config)
    for tool in tools:
        agent.ability_manager.add_ability(tool.card, tool)
    ensure_execute_signature_compat(
        agent, tool_result_observer=tool_result_observer, tool_timeout_s=settings.tool_timeout_s
    )
    return agent


async def build_worker(
    session: DriverSession,
    bundle: SurfaceBundle,
    *,
    provider: str,
    api_key: str,
    api_base: str,
    model_name: str,
    max_steps: int,
    core: CoreSettings,
    capabilities: Iterable[str],
    tool_result_observer: ToolResultObserver,
    screenshot_subdir: str,
    artifacts_subdir: str,
    image_support: bool = False,
) -> ReActAgent:
    """The worker over a started session: the factory's worker tools, the core rails, the final-answer recovery."""
    parts = build_agent_parts(
        session, bundle, capabilities=capabilities, image_support=image_support, language=WORKER_LANGUAGE, worker=True
    )
    worker = build_browser_worker_agent(
        provider=provider,
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        max_steps=max_steps,
        tools=parts.tools,
        screenshot_subdir=screenshot_subdir,
        artifacts_subdir=artifacts_subdir,
        tool_result_observer=tool_result_observer,
        settings=core,
        worker_prompt=bundle.profile.worker_prompt or default_worker_prompt,
        driver_name=worker_driver_name(session.assembly.stack.info),
    )
    rails = core_rails(bundle.profile, parts.builders, session.page_state)
    for rail in (*rails, *parts.rails, FinalAnswerRecoveryRail()):
        await worker.register_rail(rail)
    return worker


async def refresh_browser_worker_system_prompt(
    agent: ReActAgent,
    *,
    worker_prompt: Callable[..., str],
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
    driver_name: str,
) -> None:
    """Re-render the worker system prompt for the tools its next invoke sends to the model."""
    tool_names = tuple(tool.name for tool in await agent.ability_manager.list_tool_info())
    logger.info("Worker prompt rendered for tools: %s", tool_names)
    prompt = worker_prompt(
        _normalize_output_subdir(screenshot_subdir, "screenshots"),
        _normalize_output_subdir(artifacts_subdir, "artifacts"),
        tool_names=tool_names,
        driver_name=driver_name,
    )
    agent.config.configure_prompt_template([{"role": "system", "content": prompt}])
