# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent builders for runtime and browser worker."""

from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable, Iterable, Optional

import anyio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from ..contract import DriverInfo, SurfaceBundle
from .assembly import build_agent_parts, core_rails
from .browser_logging import configure_browser_agent_logging
from .config import CoreSettings
from .driver_session import DriverSession
from .final_answer_recovery import FinalAnswerRecoveryRail

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


_BROWSER_WORKER_HELPER_TOOL_NAMES = (
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_batch_interact",
    "browser_custom_action",
    "browser_list_custom_actions",
)


def build_browser_worker_system_prompt(
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
    *,
    tool_names: Optional[Iterable[str]] = None,
    driver_name: str,
) -> str:
    """Render the worker system prompt; helper guidance only for helpers in ``tool_names`` (``None``: all)."""
    available = set(_BROWSER_WORKER_HELPER_TOOL_NAMES if tool_names is None else tool_names)
    interactives = "browser_probe_interactives" in available
    cards = "browser_probe_cards" in available
    probes = interactives or cards
    batch = "browser_batch_interact" in available
    batch_vs_custom_action = batch and interactives and cards and "browser_custom_action" in available
    helper_guidance = "".join(
        text
        for enabled, text in (
            (
                probes,
                "Before broad page snapshots, full-body scans, or generic DOM scraping, "
                "choose the smallest compact probe that matches the task. ",
            ),
            (
                interactives,
                "Use browser_probe_interactives for buttons, links, inputs, forms, navigation controls, "
                "login controls, pagination controls, menus, and other visible interactive elements. "
                "When using browser_probe_interactives only for page-level controls, prefer max_items around 20-30 "
                "unless the task explicitly requires a larger inventory. ",
            ),
            (
                cards,
                "On product pages, marketplace pages, search-result pages, catalog pages, article-list pages, "
                "or any page with repeated visible cards/listings, call browser_probe_cards before broad extraction. "
                "Use browser_probe_cards to identify compact repeated structures such as product cards, result cards, "
                "book cards, article cards, listing rows, title/author/source/summary, "
                "title/price/rating/review/availability fields, primary links, "
                "visible controls, generation-scoped target IDs, and recurring structure signatures. ",
            ),
            (
                cards and interactives,
                "For product/listing/item-data tasks, prefer browser_probe_cards first; call "
                "browser_probe_interactives "
                "only if you also need page-level navigation, filters, forms, or controls outside the cards. ",
            ),
            (
                cards,
                "If browser_probe_cards returns the fields needed for the task, including article/result-row "
                "title, link, author/source, or summary fields, use that compact result as the evidence "
                "for extraction and final reporting instead of taking screenshots, snapshots, or running extra DOM "
                "scans. "
                "After a successful card probe, use browser_evaluate, or a browser_run_code tool only when it is "
                "explicitly present, for a clearly missing required field or a specific target-based action; "
                "do not repeat broad evaluation "
                "just to re-read the same cards. ",
            ),
            (
                probes,
                "Treat page_id/generation_id/url/title/interactives/cards/"
                "field_coverage/blockers as one PageState contract. ",
            ),
            (
                probes and batch,
                "Pass the current generation_id with a "
                "probe target_id directly to browser_batch_interact; never translate it into guessed CSS. "
                "Explicit selectors are reserved for validated read-only extraction and condition waits. ",
            ),
            (
                cards,
                "If a card has primary_link or href, navigate to it directly. ",
            ),
            (
                batch_vs_custom_action,
                "browser_batch_interact is a standalone runtime helper like browser_probe_interactives and "
                "browser_probe_cards, not a browser_custom_action wrapper. Use it directly. ",
            ),
            (
                batch,
                "For multi-field forms with several known controls, search flows with autocomplete, "
                "dropdown/date-picker "
                "flows, filter panels, or any short sequence where two or more next click/type/wait/extract steps are "
                "already known, call browser_batch_interact before falling back to repeated "
                "browser_click/browser_type and separate wait turns. "
                "Extract multiple fields from the same page in one batch with named extract_text/extract_value "
                "steps, or use one card probe result that already contains every requested field. "
                "Do not force browser_batch_interact for a single uncertain click, one simple text field, or a page "
                "state "
                "that still needs inspection. Prefer generation-scoped target_id in batch steps. "
                "Use autocomplete steps for type-then-choose widgets, and condition-based "
                "wait_for_selector/wait_for_text "
                "steps instead of fixed sleeps. For dynamic pages use wait_for_url, "
                "wait_for_first_card_title, wait_for_sort_state, wait_for_result_count, "
                "wait_for_dom_text_change, wait_for_stable, or wait_for_tab with a short poll interval and total "
                "timeout. "
                "When an action opens a new tab, use click plus wait_for_tab in the same batch; wait_for_tab "
                "activates the matched tab by default. Do not wait for a new URL on the old tab. ",
            ),
            (
                True,
                "Interpret 'fully loaded' or an 'appropriate pause' as condition stability, not a fixed sleep. ",
            ),
            (
                batch,
                "Do not split click+type+wait, click+wait+click, "
                "date open+choose, or search submit+result wait into separate ReAct turns unless "
                "browser_batch_interact "
                "failed or the next target is genuinely unknown. ",
            ),
        )
        if enabled
    )
    return (
        "You are a browser worker agent.\n"
        f"Execute browser tasks step-by-step with {driver_name} tools and approved runtime helper tools only.\n"
        "Before interacting, ensure page or selector readiness.\n"
        "Keep actions targeted and avoid unnecessary page snapshots.\n"
        f"{helper_guidance}"
        "Keep using browser_fill_form for ordinary visible text fields when that official tool is enough. "
        "Use browser_snapshot only when the compact probes are insufficient, when accessibility structure is needed, "
        f"or when you need exact element references required by a {driver_name} action. "
        "Use browser_evaluate for small exact computations. Use a browser_run_code tool only when it is present "
        "in the current tool list and compact probes, evaluate, snapshot, and deterministic actions are "
        "insufficient. Never use run-code to dump the entire document body.\n"
        "If actions repeatedly fail, stop and report the exact failing action.\n"
        "If you use browser_tabs, action MUST be one of: list, new, close, select.\n"
        "Use specialized operations only when their explicit primitive tool is present in the current tool list. "
        "Do not launch nested browser tasks from the browser worker. If the available tools cannot complete an "
        "operation, report the exact blocker instead of inventing a tool name.\n"
        "IMPORTANT: Do NOT use browser_take_screenshot unless strictly necessary. "
        f"If a screenshot is needed, always save it under '{screenshot_subdir}/'. "
        "Use browser_take_screenshot when it is present; do not create screenshots through run-code.\n"
        f"If you produce any output files (reports, notes, summaries, markdown, text files, etc.), "
        f"write them to the '{artifacts_subdir}/' directory relative to the working directory. "
        "Never write output files to the project root or any other location.\n"
        "Final output MUST be a single JSON object with keys:\n"
        "ok (boolean), final (string), page (object with url and title), "
        "screenshot (string|null), error (string|null).\n"
        "Also include status (completed|partial|blocked|failed) whenever possible. "
        "If the task is not fully complete, include progress as an object with "
        "completed_steps, remaining_steps, next_step, completion_evidence, and missing_requirements.\n"
        "Set ok=true only when the exact user-visible goal is fully satisfied and you can cite concrete evidence "
        "from the page, compact probe results, browser snapshots, screenshots, or generated artifacts. "
        "If the task is incomplete or blocked, set ok=false and fill the progress fields so a continuation can "
        "resume with minimal repetition.\n"
        "Return JSON only, even on failures. "
        "Do not output markdown, code fences, or plain text outside the JSON object."
    )


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
            logger.error(
                f"Tool execution timed out after {tool_timeout_s:.1f}s; tools={tool_names}"
            )
            raise RuntimeError(
                f"tool_execution_timeout: tools={tool_names}, timeout_s={tool_timeout_s:.1f}"
            ) from exc
        if tool_result_observer is not None:
            await _notify_tool_results(tool_call, results, tool_result_observer)
        return results

    agent.ability_manager.execute = execute_with_timeout
    setattr(agent.ability_manager.execute, "_playwright_timeout_wrapped", True)


def _normalize_output_subdir(subdir: str, default: str) -> str:
    return (subdir or default).strip().replace("\\", "/").strip("/") or default


def worker_driver_name(info: DriverInfo) -> str:
    return info.display_name or info.name


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
    driver_name: str,
) -> ReActAgent:
    """The worker ReActAgent with ``tools`` as its abilities (the factory's command tools and helpers)."""
    screenshot_subdir = _normalize_output_subdir(screenshot_subdir, "screenshots")
    artifacts_subdir = _normalize_output_subdir(artifacts_subdir, "artifacts")
    card = AgentCard(
        id="agent.playwright.browser_worker",
        name="playwright_browser_worker",
        description="Browser worker that executes web tasks using Playwright MCP tools.",
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
                    "content": build_browser_worker_system_prompt(
                        screenshot_subdir, artifacts_subdir, driver_name=driver_name
                    ),
                }
            ]
        )
    )
    settings = settings or CoreSettings.from_process_env()
    configure_browser_agent_logging(settings)
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
) -> ReActAgent:
    """The worker over a started session: the factory's worker tools, the core rails, the final-answer recovery."""
    parts = build_agent_parts(
        session, bundle, capabilities=capabilities, image_support=False, language=WORKER_LANGUAGE, worker=True
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
        driver_name=worker_driver_name(session.assembly.stack.info),
    )
    rails = core_rails(bundle.profile, parts.builders, session.page_state)
    for rail in (*rails, *parts.rails, FinalAnswerRecoveryRail()):
        await worker.register_rail(rail)
    return worker


async def refresh_browser_worker_system_prompt(
    agent: ReActAgent,
    *,
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
    driver_name: str,
) -> None:
    """Re-render the worker system prompt for the tools its next invoke sends to the model."""
    tool_names = tuple(tool.name for tool in await agent.ability_manager.list_tool_info())
    logger.info(
        "Playwright worker prompt rendered for helper tools: %s",
        tuple(name for name in _BROWSER_WORKER_HELPER_TOOL_NAMES if name in tool_names),
    )
    prompt = build_browser_worker_system_prompt(
        _normalize_output_subdir(screenshot_subdir, "screenshots"),
        _normalize_output_subdir(artifacts_subdir, "artifacts"),
        tool_names=tool_names,
        driver_name=driver_name,
    )
    agent.config.configure_prompt_template([{"role": "system", "content": prompt}])
