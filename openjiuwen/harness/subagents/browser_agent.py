# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for the browser subagent."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.schema.decision_policy import DecisionPolicyModel
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.capabilities import (
    DEFAULT_BROWSER_CAPABILITIES,
    resolve_browser_capabilities,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.runner.agent import (
    BROWSER_AGENT_CARD_ID,
    DEFAULT_BROWSER_AGENT_DESCRIPTION,
    DEFAULT_BROWSER_AGENT_MAX_ITERATIONS,
    DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT,
    DEFAULT_BROWSER_AGENT_TEMPERATURE,
    browser_model_with_temperature,
    build_main_agent,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.runner.facade import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.surfaces.browser.runner.logging import configure_browser_agent_logging
from openjiuwen.harness.tools.browser_move.surfaces.browser.runner.settings import (
    BrowserInstanceConfig,
    RuntimeSettings,
    build_browser_guardrails,
    build_playwright_mcp_config,
    build_runtime_settings,
)

try:
    from openjiuwen.harness.prompts import resolve_language
except ImportError:

    def resolve_language(language: Optional[str] = None) -> str:  # type: ignore[misc]
        return language if language in {"cn", "en"} else "cn"


if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace


BROWSER_AGENT_FACTORY_NAME = "browser_agent"


def _coerce_browser_instance(
    browser_instance: Optional[BrowserInstanceConfig | Dict[str, Any]],
    browser_key: Optional[str],
) -> Optional[BrowserInstanceConfig]:
    """Normalize the per-instance browser config from a model, dict, or bare key.

    A dict form is accepted so the teams manifest can carry browser identity as
    serializable ``factory_kwargs`` across the spawn wire boundary.
    """
    if isinstance(browser_instance, BrowserInstanceConfig):
        return browser_instance
    if isinstance(browser_instance, dict):
        allowed = {f.name for f in dataclasses.fields(BrowserInstanceConfig)}
        return BrowserInstanceConfig(**{k: v for k, v in browser_instance.items() if k in allowed})
    if browser_key:
        return BrowserInstanceConfig(key=str(browser_key))
    return None


def _resolve_runtime_settings(
    model: Model,
    settings: Optional[RuntimeSettings],
    instance: Optional[BrowserInstanceConfig] = None,
) -> RuntimeSettings:
    if settings is not None:
        return settings
    if model.model_client_config is not None:
        cc = model.model_client_config
        request_model_name = ""
        if model.model_config is not None:
            request_model_name = (
                getattr(model.model_config, "model", None) or getattr(model.model_config, "model_name", None) or ""
            )
        return RuntimeSettings(
            provider=cc.client_provider,
            api_key=cc.api_key,
            api_base=cc.api_base or "",
            model_name=request_model_name,
            mcp_cfg=build_playwright_mcp_config(instance),
            guardrails=build_browser_guardrails(),
            instance=instance,
        )
    return build_runtime_settings(instance)


def build_browser_agent_config(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = DEFAULT_BROWSER_AGENT_MAX_ITERATIONS,
    temperature: float = DEFAULT_BROWSER_AGENT_TEMPERATURE,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    browser_key: Optional[str] = None,
    browser_instance: Optional[BrowserInstanceConfig | Dict[str, Any]] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as create_browser_agent()."""
    resolved_language = resolve_language(language)
    instance = _coerce_browser_instance(browser_instance, browser_key)
    browser_model = browser_model_with_temperature(model, temperature)
    resolved_settings = _resolve_runtime_settings(browser_model, settings, instance)
    return SubAgentConfig(
        agent_card=card
        or AgentCard(
            id=BROWSER_AGENT_CARD_ID,
            name="browser_agent",
            description=DEFAULT_BROWSER_AGENT_DESCRIPTION.get(
                resolved_language,
                DEFAULT_BROWSER_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt
        or DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT.get(
            resolved_language,
            DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"],
        ),
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=browser_model,
        rails=rails,
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        factory_name=BROWSER_AGENT_FACTORY_NAME,
        factory_kwargs={"settings": resolved_settings},
    )


def create_browser_agent(
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
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    browser_key: Optional[str] = None,
    browser_instance: Optional[BrowserInstanceConfig | Dict[str, Any]] = None,
    browser_capabilities: Optional[List[str]] = None,
    runtime: Optional[BrowserAgentRuntime] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create the browser subagent with task-scoped capability context.

    ``browser_capabilities`` is resolved against the trusted capability
    catalog here. Core is always applied, including when the caller omits the
    optional capability list. ``runtime`` is an existing facade to build on
    (the WebVoyager runner's); by default the agent gets its own.
    """
    if browser_capabilities is not None and (
        not isinstance(browser_capabilities, list)
        or not all(isinstance(capability, str) for capability in browser_capabilities)
    ):
        raise ValueError("browser_capabilities must be a list of strings")

    resolved_capabilities = resolve_browser_capabilities(browser_capabilities)
    if resolved_capabilities.rejected_names:
        rejected = ", ".join(resolved_capabilities.rejected_names)
        available = ", ".join(capability.name for capability in DEFAULT_BROWSER_CAPABILITIES)
        raise ValueError(f"Unsupported browser capabilities: {rejected}. Available capabilities: {available}")

    logger.info(
        "Resolved browser capabilities: requested=%s, selected=%s, allowed_tools=%s",
        resolved_capabilities.requested_names,
        resolved_capabilities.selected_names,
        resolved_capabilities.allowed_tool_names,
    )

    resolved_language = resolve_language(language)
    browser_model = (
        model if isinstance(model, DecisionPolicyModel) else browser_model_with_temperature(model, temperature)
    )
    configure_browser_agent_logging(CoreSettings.from_process_env())

    if runtime is None:
        instance = _coerce_browser_instance(browser_instance, browser_key)
        resolved_settings = _resolve_runtime_settings(browser_model, settings, instance)
        runtime = BrowserAgentRuntime(
            provider=resolved_settings.provider,
            api_key=resolved_settings.api_key,
            api_base=resolved_settings.api_base,
            model_name=resolved_settings.model_name,
            mcp_cfg=resolved_settings.mcp_cfg,
            guardrails=resolved_settings.guardrails,
            instance=resolved_settings.instance,
            allowed_tool_names=resolved_capabilities.allowed_tool_names,
            workspace=workspace,
            language=resolved_language,
        )
    return build_main_agent(
        runtime,
        browser_model,
        card=card,
        system_prompt=system_prompt,
        tools=tools,
        mcps=mcps,
        subagents=subagents,
        rails=rails,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        temperature=temperature,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )


__all__ = [
    "BROWSER_AGENT_CARD_ID",
    "BROWSER_AGENT_FACTORY_NAME",
    "DEFAULT_BROWSER_AGENT_MAX_ITERATIONS",
    "DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT",
    "DEFAULT_BROWSER_AGENT_TEMPERATURE",
    "build_browser_agent_config",
    "create_browser_agent",
]
