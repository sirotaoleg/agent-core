# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser main agent over a facade: its prompts, its rails and context processors, and the profile's hook."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ToolResultWindowProcessorConfig
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.schema.decision_policy import DecisionPolicyModel

from ..rails.runtime_rail import BrowserRuntimeRail
from ..state_processor import BrowserStateContextProcessorConfig
from ..working_context.processor import BrowserWorkingContextProcessorConfig

if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace

    from .facade import BrowserAgentRuntime

# Agent checkpoints are namespaced by AgentCard.id inside a conversation.
# Keep the default stable so reconstructing this subagent can restore its
# Session-backed working context on a same-conversation follow-up.
BROWSER_AGENT_CARD_ID = "openjiuwen.browser_agent"
DEFAULT_BROWSER_AGENT_TEMPERATURE = 0.4
DEFAULT_BROWSER_AGENT_MAX_ITERATIONS = 100
_BROWSER_MODEL_TEMPERATURE_MARKER = "_browser_agent_temperature"
_BROWSER_PARENT_MODEL_MARKER = "_browser_agent_parent_model"

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN = (
    "You are a browser automation agent responsible for executing web tasks directly. "
    "Choose the strategy at this agent level and use the available Playwright and runtime tools; "
    "the runtime validates targets and outcomes but does not replace your task judgment.\n"
    "Every model call includes one runtime-maintained <browser_working_context> followed by the latest "
    "<browser_state>. Runtime outcomes and blockers are authoritative; inferred fields are extraction hints, "
    "not additional user requirements. Source observations also provide usable evidence. "
    "A fresh browser capture occurs initially and after a recognized page mutation; otherwise the cached "
    "observation is reused. When the runtime directive requires replanning, change the strategy materially.\n"
    "For a simple lookup, prefer a direct search-results URL when the engine and query are known. Use "
    "browser_probe_interactives for page controls and browser_probe_cards for repeated results or products. "
    "Use the compact PageState target_id with its generation_id directly; never reconstruct guessed CSS from "
    "a target ID. When a card has primary_link or href, navigate directly to that URL.\n"
    "Use browser_batch_interact when two or more deterministic actions or same-page field extractions are already "
    "known. Use a primitive for one uncertain action. Prefer observable condition waits over fixed sleep. Use "
    "browser_snapshot only when compact probes are insufficient, and browser_evaluate only for a small exact "
    "target or computation. If an older result has a <persisted-output> marker, recall it only when its preview "
    "does not contain the needed evidence; recalled targets are not executable after navigation.\n"
    "Use canonical fields when convenient, but do not reread a page just to rename already observed facts. "
    "Report genuinely unavailable values as unknown. One trustworthy page value or structured result is enough; "
    "do not verify the same fact with multiple "
    "tools. Stop immediately when the requested outcome is evidenced. The runtime determines final status, so "
    "return a concise natural-language result rather than another progress object.\n"
    "If an optional capability makes a browser_run_code tool visible, use it only when deterministic tools are "
    "insufficient, and never dump the full document. Preserve session continuity and report a concrete blocker "
    "when the available browser state or tools cannot complete the task."
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN = (
    "你是浏览器自动化代理，负责直接完成网页任务。请在当前代理层决定策略并使用可见的 Playwright "
    "和 runtime 工具；runtime 负责验证目标和结果，但不代替你的任务判断。\n"
    "每次模型调用都会依次提供 runtime 维护的 <browser_working_context> 和最新 <browser_state>。"
    "运行结果和阻断项是权威信息；推断字段只是提取提示，不是新增的用户要求。带来源的页面原文也可作为证据。"
    "系统仅在初始调用和已识别的"
    "页面变更后重新观察；runtime 要求重新规划时，应实质改变策略。\n"
    "已知搜索引擎和关键词时，简单查询优先直接构造搜索结果 URL。页面控件使用 "
    "browser_probe_interactives，重复结果或商品使用 browser_probe_cards。直接使用 PageState 返回的 "
    "target_id 和 generation_id，禁止把 target_id 改写成猜测的 CSS。卡片包含 primary_link 或 href "
    "时直接导航该 URL。\n"
    "只有两个及以上确定动作或同页多字段提取时使用 browser_batch_interact；单个不确定动作使用基础工具。"
    "优先等待可观察条件，不使用固定 sleep。紧凑 Probe 不足时再使用 browser_snapshot；"
    "browser_evaluate 仅用于小范围精确目标或计算。旧结果出现 <persisted-output> 且预览不足时才恢复；"
    "导航后恢复内容中的目标不可继续操作。\n"
    "方便时使用规范字段，但不要仅为改写字段名而重读已经观察到的事实；字段确实不可获取时使用 unknown。"
    "一个可信页面值或结构化结果已经足够，不要用多个工具重复验证同一事实。请求结果有证据后立即结束。"
    "最终状态由 runtime 决定，只返回简洁自然语言结果，不再维护第二份进度对象。\n"
    "只有可选能力明确暴露 browser_run_code 时才使用，并且仅限确定性工具不足的情况；禁止转储完整页面。"
    "保持浏览器会话连续；现有页面或工具确实无法完成时，报告具体 blocker。"
)

DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_BROWSER_AGENT_DESCRIPTION_EN = (
    "Dedicated browser subagent that directly controls the browser with Playwright MCP tools."
)
DEFAULT_BROWSER_AGENT_DESCRIPTION_CN = "专用浏览器子代理，直接使用 Playwright MCP 工具执行网页任务。"
DEFAULT_BROWSER_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_BROWSER_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_BROWSER_AGENT_DESCRIPTION_EN,
}

_WINDOWED_TOOL_NAMES = [
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_snapshot",
    "browser_find",
    "browser_evaluate",
]


def browser_model_with_temperature(model: Model, temperature: float) -> Model:
    """Copy a parent model descriptor and override sampling for browser work."""
    resolved_temperature = max(0.0, min(float(temperature), 2.0))
    if getattr(model, _BROWSER_MODEL_TEMPERATURE_MARKER, None) == resolved_temperature:
        return model
    parent_model = getattr(model, _BROWSER_PARENT_MODEL_MARKER, model)

    model_config = getattr(model, "model_config", None)
    if isinstance(model_config, ModelRequestConfig):
        browser_model_config = model_config.model_copy(
            deep=True,
            update={"temperature": resolved_temperature},
        )
    elif model_config is None:
        browser_model_config = ModelRequestConfig(temperature=resolved_temperature)
    else:
        browser_model_config = copy.copy(model_config)
        setattr(browser_model_config, "temperature", resolved_temperature)

    model_client_config = getattr(model, "model_client_config", None)
    if issubclass(type(model), Model) and model_client_config is not None:
        browser_model = Model(
            model_client_config=model_client_config,
            model_config=browser_model_config,
        )
        setattr(browser_model, _BROWSER_MODEL_TEMPERATURE_MARKER, resolved_temperature)
        setattr(browser_model, _BROWSER_PARENT_MODEL_MARKER, parent_model)
        return browser_model

    # Lightweight test doubles and compatibility model descriptors may not be
    # constructible as a concrete Model. Preserve their shape without mutating
    # the parent object.
    browser_model = copy.copy(model)
    browser_model.model_config = browser_model_config
    setattr(browser_model, _BROWSER_MODEL_TEMPERATURE_MARKER, resolved_temperature)
    setattr(browser_model, _BROWSER_PARENT_MODEL_MARKER, parent_model)
    return browser_model


def build_main_agent(
    runtime: BrowserAgentRuntime,
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = DEFAULT_BROWSER_AGENT_MAX_ITERATIONS,
    temperature: float = DEFAULT_BROWSER_AGENT_TEMPERATURE,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """The browser agent over ``runtime``: the runtime rail, the core rails, the context processors, the prompts."""
    resolved_language = resolve_language(language)
    # A decision policy in the model slot is used as is: no temperature copy, no LLM-only context rails.
    policy_model = model if isinstance(model, DecisionPolicyModel) else None
    browser_model = model if policy_model is not None else browser_model_with_temperature(model, temperature)
    if policy_model is not None:
        policy_model.bind_runtime(runtime)

    final_card = card or AgentCard(
        id=BROWSER_AGENT_CARD_ID,
        name="browser_agent",
        description=DEFAULT_BROWSER_AGENT_DESCRIPTION.get(
            resolved_language,
            DEFAULT_BROWSER_AGENT_DESCRIPTION["cn"],
        ),
    )
    final_prompt = system_prompt or DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT.get(
        resolved_language,
        DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"],
    )

    # The browser tools arrive at the first invoke, once the driver has started (BrowserRuntimeRail.before_invoke).
    injected_rails: List[AgentRail] = [BrowserRuntimeRail(runtime), *runtime.core_rails()]
    processors = [
        (
            "ToolResultWindowProcessor",
            ToolResultWindowProcessorConfig(
                tool_names=list(_WINDOWED_TOOL_NAMES),
                keep_last_k=2,
                trim_size=1000,
                min_offload_chars=1000,
                small_result_trim_size=1000,
            ),
        ),
        ("BrowserStateContextProcessor", BrowserStateContextProcessorConfig(provider=runtime)),
        (
            "BrowserWorkingContextProcessor",
            BrowserWorkingContextProcessorConfig(language=resolved_language, runtime_projection_only=True),
        ),
    ]
    caller_context_rails = [rail for rail in (rails or []) if isinstance(rail, ContextProcessorRail)]
    if policy_model is not None:
        config_kwargs.setdefault("enable_model_anomaly_detection_rail", False)
    elif caller_context_rails:
        for context_rail in caller_context_rails:
            context_rail.add_processors(processors)
    else:
        injected_rails.append(ContextProcessorRail(processors=processors, preset=False))

    agent = create_deep_agent(
        model=browser_model,
        card=final_card,
        system_prompt=final_prompt,
        tools=list(tools or []),
        mcps=list(mcps or []),
        subagents=subagents,
        rails=list(rails or []) + injected_rails,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )
    agent.register_task_resource_cleanup(runtime.release_task_resources, prepare=runtime.acquire_task_resources)
    return agent


def main_agent(
    runtime: BrowserAgentRuntime,
    model: Model,
    *,
    language: str,
    max_iterations: int,
    workspace: Any,
    capabilities: tuple[str, ...],
) -> DeepAgent:
    """The profile's main agent: the browser agent with the worker's sampling, over the runtime's capabilities."""
    logger.info("Browser main agent capabilities: %s", ", ".join(capabilities))
    return build_main_agent(
        runtime,
        model,
        language=language,
        max_iterations=max_iterations,
        temperature=runtime.core.worker_temperature,
        workspace=workspace,
    )


__all__ = [
    "BROWSER_AGENT_CARD_ID",
    "DEFAULT_BROWSER_AGENT_DESCRIPTION",
    "DEFAULT_BROWSER_AGENT_MAX_ITERATIONS",
    "DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT",
    "DEFAULT_BROWSER_AGENT_TEMPERATURE",
    "browser_model_with_temperature",
    "build_main_agent",
    "main_agent",
]
