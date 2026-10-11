#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""The browser agent's runtime: the driver session, the factory tools and the rail that keeps a task on course."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import hashlib
import json
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit
from weakref import WeakSet

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.browser_context import (
    reset_browser_agent_log_context,
    set_browser_agent_log_context,
)
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, ToolMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.harness.rails._multimodal import (
    should_enable_read_image_multimodal,
)

from ..contract import (
    Action,
    Click,
    Drag,
    DriverError,
    Effect,
    Health,
    Hover,
    Observe,
    PolicyDenied,
    RunScript,
    Scroll,
    SwitchContext,
)
from ..controllers import ActionController, BaseController
from ..surfaces.browser.helpers import CUSTOM_ACTIONS, activate_page, page_metadata, probe_for_policy
from ..utils.parsing import extract_json_object
from .assembly import AgentParts, build_agent_parts
from .browser_capabilities import CORE_BROWSER_TOOL_NAMES, capabilities_of
from .browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
    write_browser_agent_audit_artifact,
)
from .browser_working_context import (
    BROWSER_TASK_STATE_KEY,
    BrowserWorkingContextStore,
    latest_browser_user_request,
)
from .config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
    CoreSettings,
    SurfaceSettings,
    browser_surface_settings,
)
from .driver_session import DriverSession
from .page_state import CARD_EVIDENCE_FIELDS, BrowserPageState, BrowserTarget
from .semantic_state import SemanticStateTracker, price_interval_signature
from .service import MAX_ITERATION_MESSAGE, BrowserService, BrowserTaskProgressState
from .site_profiles import infer_profile_evidence_entity
from .status_logging import BrowserSubagentStatusLogger, is_browser_subagent_status_log_enabled
from .tool_factory import Builder, CommandRecord, ToolContext, ToolFlags, command_record

_BROWSER_PROGRESS_STATE_KEY = "__browser_subagent_progress_state__"
_BROWSER_PROGRESS_TASK_KEY = "__browser_subagent_last_task__"
_BROWSER_IMAGE_CAPABILITY_SECTION_NAME = "browser_image_input_capability"
_BROWSER_DATE_PICKER_SECTION_NAME = "browser_date_picker_guidance"
_BROWSER_DATE_PICKER_GUIDANCE = {
    "en": (
        "For calendar/date-picker widgets, do not type the date into a text field. Click the "
        "specific day cell instead (look for a data-iso=\"YYYY-MM-DD\" attribute or an aria-label "
        "naming the full date), then confirm the selection before proceeding."
    ),
    "cn": (
        "对于日历/日期选择器组件，不要向文本框输入日期，而应点击对应的日期单元格"
        "（查找 data-iso=\"YYYY-MM-DD\" 属性或包含完整日期的 aria-label），"
        "确认选中后再继续。"
    ),
}
_BROWSER_PHASE_STATE_KEY = BROWSER_TASK_STATE_KEY

_BROWSER_LOG_CONTEXT_TOKEN_KEY = "__browser_agent_log_context_token__"
_BROWSER_TOOL_RUNTIME_STATE_KEY = "__browser_tool_runtime_state__"
_BROWSER_ACTION_GROUP_BY_CALL_KEY = "__browser_action_group_by_call__"
_BROWSER_ACTION_GROUP_RESULTS_KEY = "__browser_action_group_results__"
_BROWSER_SKIP_TOOL_CALLS_KEY = "_skip_tool_calls"
_BROWSER_TASK_DEADLINE_KEY = "__browser_task_deadline__"
_BROWSER_INVOCATION_DEADLINE_KEY = "__browser_invocation_deadline__"
_BROWSER_SIMPLE_TASK_DEADLINE_S = 240.0
_BROWSER_COMPLEX_TASK_DEADLINE_S = 600.0
_BROWSER_MODEL_RETRY_LIMIT = 1
_BROWSER_MODEL_PROTOCOL_RETRY_LIMIT = 1
_BROWSER_REPLAN_DENIAL_LIMIT = 3
_BROWSER_READ_ONLY_RECOVERY_LIMIT = 1
_BROWSER_TASK_RESUME_LIMIT = 1
_BROWSER_TERMINAL_SYNTHESIS_KEY = "terminal_synthesis_started"
_BROWSER_OBSERVATION_MESSAGE_MAX_CHARS = 12_000

_ACTIVE_BROWSER_RUNTIMES: WeakSet[Any] = WeakSet()
_BROWSER_PROGRESS_TAG_RE = re.compile(
    r"<browser_progress>\s*(\{.*?\})\s*</browser_progress>",
    re.DOTALL | re.IGNORECASE,
)
_BROWSER_UNFINISHED_TOOL_INTENT_RE = re.compile(
    r"(?:"
    r"<[^>]*(?:dsml|tool[_ ]?calls?)[^>]*>"
    r"|(?:让我|我(?:将|会|需要|要))继续(?:调用|使用|执行).{0,100}"
    r"(?:工具|browser_|evaluate|probe|navigate|snapshot|batch)"
    r"|(?:let me|i(?:'ll| will| need to))\s+(?:continue|now)\s+"
    r"(?:call|calling|use|using|run).{0,100}"
    r"(?:tool|browser_|evaluate|probe|navigate|snapshot|batch)"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_BROWSER_OUTPUT_REQUEST_CUE_RE = re.compile(
    r"(?:返回|告诉我|提取|获取|输出|给出|列出|汇报|报告|比较|对比|"
    r"return|tell\s+me|extract|retrieve|output|provide|list|report|compare)"
    r"[^。；;.!?\n]{0,240}",
    re.IGNORECASE,
)
_BROWSER_EXPLICIT_REF_RE = re.compile(
    r"""^\s*\[?\s*ref\s*=\s*["']?([^"'\]\s]+)["']?\s*\]?\s*$""",
    re.IGNORECASE,
)
_BROWSER_PAGE_STATE_TAG = "browser_page_state"

_BROWSER_REF_TARGET_KEYS = frozenset(
    {
        "target",
        "startTarget",
        "endTarget",
        "ref",
        "startRef",
        "endRef",
    }
)
_BROWSER_REF_ALIAS_KEYS = {
    "ref": "target",
    "startRef": "startTarget",
    "endRef": "endTarget",
}
# Evidence hints and element descriptions the model may add; a tool whose schema lacks them must not see them.
_BROWSER_RUNTIME_ARG_KEYS = frozenset({"field", "fields", "element"})

_BATCH_SAFE_READ_SELECTOR_OPS = frozenset({"extract_text", "extract_value"})
_BATCH_MUTATING_TARGET_OPS = frozenset(
    {"click", "fill", "type", "autocomplete", "select_option", "set_checked", "select_visible_text"}
)
_BATCH_EXPLICIT_SELECTOR_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
    }
)
_FORM_COMMAND_NAMES = frozenset({"TypeText", "PressKey", "SelectOption", "FillForm", "UploadFile"})
_BROWSER_TERMINAL_STATUSES = frozenset({"blocked", "partial", "completed"})
_BROWSER_NON_RETRYABLE_BLOCKER_TOKENS = frozenset(
    {
        "captcha",
        "login_required",
        "permission_denied",
        "access_denied",
        "user_intervention_required",
        "payment_required",
        "security_verification",
        "task_deadline_exhausted",
    }
)
_BROWSER_FIELD_ALIASES: Dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "标题", "名称", "电影", "商品"),
    "url": ("url", "link", "href", "primary link", "primary_link", "链接", "网址"),
    "price": ("price", "cost", "价格", "价钱", "费用"),
    "rating": ("rating", "score", "评分", "星级"),
    "product_rating": ("product rating", "item rating", "商品评分", "宝贝评分"),
    "shop_rating": ("shop rating", "seller rating", "store rating", "店铺评分", "卖家评分"),
    "author": ("author", "creator", "writer", "作者", "博主", "发布者", "回答者"),
    "likes": ("likes", "like count", "upvotes", "点赞", "点赞数", "赞同", "赞同数", "获赞", "获赞数"),
    "favorites": ("favorites", "favourites", "bookmarks", "收藏", "收藏数"),
    "comments": ("comments", "comment count", "replies", "评论", "评论数", "回复数"),
    "shop": ("shop", "store", "merchant", "seller", "店铺", "商家", "卖家"),
    "duration": ("duration", "video length", "时长", "视频长度"),
    "views": ("views", "view count", "play count", "播放量", "观看数"),
    "exchange_rate": ("exchange rate", "conversion rate", "汇率", "兑换率"),
    "high_temperature": (
        "high temperature",
        "maximum temperature",
        "highest temperature",
        "最高温",
        "最高气温",
        "高温",
    ),
    "low_temperature": (
        "low temperature",
        "minimum temperature",
        "lowest temperature",
        "最低温",
        "最低气温",
        "低温",
    ),
    "sort_state": ("sort state", "sort order", "ordering", "排序状态", "排序方式", "排序"),
    "date": ("date", "日期", "哪天"),
    "time": ("time", "时间", "几点"),
    "address": ("address", "location", "地址", "地点"),
}
# Inferred display fields are advisory; these distinctions require typed evidence.
_BROWSER_STRICT_EVIDENCE_FIELDS = frozenset({"rating", "product_rating", "shop_rating", "sort_state"})
_BROWSER_EVALUATE_FIELD_ALIASES = {
    "article_title": "title",
    "page_title": "title",
    "product_title": "title",
    "result_title": "title",
    "author_name": "author",
    "author_raw": "author",
    "creator_name": "author",
    "profile_name": "author",
    "writer_name": "author",
    "like_count": "likes",
    "likes_count": "likes",
    "like_raw": "likes",
    "upvote_count": "likes",
    "favorite_count": "favorites",
    "favorites_count": "favorites",
    "favourite_count": "favorites",
    "bookmark_count": "favorites",
    "fav_raw": "favorites",
    "comment_count": "comments",
    "comments_count": "comments",
    "reply_count": "comments",
    "comment_raw": "comments",
    "com_raw": "comments",
    "active_tab": "sort_state",
    "active_tabs": "sort_state",
    "selected_tab": "sort_state",
    "selected_tabs": "sort_state",
    "sort_selected": "sort_state",
}
_BROWSER_CONTEXTUAL_EVALUATE_FIELD_ALIASES = frozenset(
    {
        "name",
        "profile_name",
        "active_tab",
        "active_tabs",
        "selected_tab",
        "selected_tabs",
    }
)
_BROWSER_SELECTED_SORT_LABELS = {
    "sale": "销量",
    "sales": "销量",
    "volume": "销量",
    "销量": "销量",
    "price": "价格",
    "价格": "价格",
    "latest": "最新",
    "newest": "最新",
    "最新": "最新",
    "comprehensive": "综合",
    "relevance": "综合",
    "default": "综合",
    "综合": "综合",
    "rating": "评分",
    "score": "评分",
    "评分": "评分",
}
_BROWSER_UNKNOWN_VALUE_RE = re.compile(
    r"^(?:unknown|n/?a|not available|not found|未找到|未知|暂无|未显示|不可用)$",
    re.IGNORECASE,
)
_BROWSER_ZERO_COMMENT_RE = re.compile(
    r"^(?:0|no comments?|no replies|还没有评论|暂无评论|没有评论|暂无回复|0\s*条评论)$",
    re.IGNORECASE,
)
_BROWSER_IMAGE_CAPABILITY_GUIDANCE = {
    True: {
        "en": (
            "The current model can inspect image input. Use browser_take_screenshot only when "
            "pixel-level visual evidence is required and DOM probes, accessibility snapshots, "
            "or targeted evaluation cannot answer the task."
        ),
        "cn": (
            "当前模型可以理解图片输入。仅当任务需要像素级视觉证据，且 DOM 探测、无障碍快照或"
            "定向脚本无法解决时，才使用 browser_take_screenshot。"
        ),
    },
    False: {
        "en": (
            "Image input is unavailable or unverified for this run. Do not request screenshots, "
            "including browser_take_screenshot or browser_batch_interact with op=screenshot. "
            "Use DOM probes, accessibility snapshots, and targeted evaluation instead."
        ),
        "cn": (
            "本次运行的图片输入能力不可用或尚未确认。不要请求截图，包括 "
            "browser_take_screenshot 或 browser_batch_interact 的 op=screenshot；"
            "请改用 DOM 探测、无障碍快照和定向脚本。"
        ),
    },
}
_BROWSER_PHASE_DEFINITIONS = {
    "navigation": {
        "budget": 12,
        "completion_condition": "Target URL or expected page identity is observed.",
    },
    "form": {
        "budget": 24,
        "completion_condition": "All required fields are populated and submission is evidenced.",
    },
    "filtering": {
        "budget": 20,
        "completion_condition": "Requested filter/sort state and changed results are observed.",
    },
    "extraction": {
        "budget": 20,
        "completion_condition": "One structured result contains every requested field.",
    },
}

def _contains_any_token(value: str, tokens: Iterable[str]) -> bool:
    for token in tokens:
        if token in value:
            return True
    return False


def _has_non_empty_mapping_value(
    value: Dict[str, Any],
    keys: Iterable[str],
) -> bool:
    for key in keys:
        if value.get(key) not in (None, ""):
            return True
    return False


def _copy_non_none_values(
    value: Dict[str, Any],
    keys: Iterable[str],
) -> Dict[str, Any]:
    copied: Dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item is not None:
            copied[key] = item
    return copied


@dataclass(frozen=True)
class ToolKind:
    """What the rail knows of one tool call: the command decoded from its arguments, or a helper's declared flags."""

    action: Action | None = None
    effect: Effect = Effect.ACT
    exempt_from_budget: bool = False
    progress: bool = False
    known: bool = False
    helper: bool = False
    fields: frozenset[str] = frozenset()

    @property
    def command(self) -> type[Action] | None:
        return None if self.action is None else type(self.action)

    @property
    def read_only(self) -> bool:
        return self.effect is Effect.READ


def _tool_args(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class BrowserAgentRuntime:
    """Runtime kernel for browser lifecycle and deterministic helper actions."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
        instance: Optional[BrowserInstanceConfig] = None,
        allowed_tool_names: Optional[Iterable[str]] = None,
        *,
        core_settings: Optional[CoreSettings] = None,
        browser_settings: Optional[SurfaceSettings] = None,
        workspace: Any = None,
        language: str = "cn",
    ) -> None:
        self._instance = instance
        self._core_settings = core_settings
        self._browser_settings = browser_settings or browser_surface_settings()
        self._workspace = workspace
        self._language = language
        resolved_allowed_tool_names = (
            CORE_BROWSER_TOOL_NAMES if allowed_tool_names is None else tuple(dict.fromkeys(allowed_tool_names))
        )
        self._capabilities = capabilities_of(resolved_allowed_tool_names)
        self._page_state = BrowserPageState()
        self._service = BrowserService(
            provider=provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            mcp_cfg=mcp_cfg,
            guardrails=guardrails,
            instance=instance,
            allowed_tool_names=resolved_allowed_tool_names,
            browser_settings=self._browser_settings,
            core_settings=core_settings,
            page_state=self._page_state,
        )
        self._browser_custom_action_tool = None
        self._browser_list_actions_tool = None
        self._controller: BaseController = ActionController()
        self._agent: Any = None
        self._session: DriverSession | None = None
        self._context: ToolContext | None = None
        self._parts: dict[bool, AgentParts] = {}
        self._builders: dict[str, Builder] = {}
        self._fields: dict[str, frozenset[str]] = {}
        self._flags: dict[str, ToolFlags] = {}
        self._semantic_state_tracker = SemanticStateTracker()
        self.observations_reused = 0
        _ACTIVE_BROWSER_RUNTIMES.add(self)

    @property
    def service(self) -> BrowserService:
        return self._service

    @property
    def core_settings(self) -> Optional[CoreSettings]:
        return self._core_settings

    @property
    def controller(self) -> BaseController:
        return self._controller

    @property
    def page_state(self) -> BrowserPageState:
        return self._page_state

    @property
    def session(self) -> DriverSession | None:
        return self._session

    @property
    def page_id(self) -> str:
        return self._page_state.page_id

    @property
    def generation_id(self) -> str:
        return self._page_state.generation_id

    def export_page_state(self) -> Dict[str, Any]:
        return self._page_state.export()

    def set_task_requested_fields(self, fields: Iterable[str]) -> None:
        """Keep PageState projection scoped to fields requested by this task."""

        self._page_state.set_requested_fields(fields)

    def resolve_model_target_id(self, target_id: str) -> BrowserTarget:
        """Resolve or safely refresh a runtime-owned model target."""

        page_state = self._page_state
        normalized = str(target_id or "").strip()
        target = page_state.get_target(normalized)
        if target is None:
            raise ValueError(
                f"Unknown PageState target_id {normalized}; current generation is "
                f"{page_state.generation_id}."
            )
        if target.generation != page_state.generation:
            target = page_state.get_target(page_state.refresh_target_id(normalized))
        if target is None:
            raise ValueError(
                f"PageState target_id {normalized} could not be refreshed in "
                f"{page_state.generation_id}."
            )
        return target

    def target_recovery_state(self, target_id: str) -> Dict[str, Any]:
        """Return bounded current-generation recovery details for one target."""

        page_state = self._page_state
        return {
            "current_generation": page_state.generation_id,
            "candidate_fresh_targets": page_state.recovery_candidates(target_id, limit=5),
        }

    def resolve_primary_link(self, tool_args: Any) -> str:
        parsed = _tool_args(tool_args)
        explicit = str(parsed.get("primary_link") or parsed.get("href") or parsed.get("url") or "").strip()
        if explicit:
            return explicit
        target_id = str(parsed.get("target_id") or parsed.get("target") or "").strip()
        if target_id.startswith("t_g"):
            try:
                target = self.resolve_model_target_id(target_id)
            except ValueError:
                target = None
            if target is not None and target.href:
                return target.href
        page_state = self._page_state
        for key in ("selector", "target"):
            selector = str(parsed.get(key) or "").strip()
            generation_and_href = page_state.selector_primary_links.get(selector)
            if generation_and_href and generation_and_href[0] == page_state.generation:
                return generation_and_href[1]
        return ""

    def tool_kind(self, tool_name: str, tool_args: Any) -> ToolKind:
        """The command ``tool_args`` decode to on a factory tool, or a helper's flags; unknown tools act."""
        build = self._builders.get(tool_name)
        if build is not None:
            fields = self._fields.get(tool_name, frozenset())
            try:
                action = build(_tool_args(tool_args), self._page_state)
            except ValueError:
                return ToolKind(progress=True, known=True, fields=fields)
            return ToolKind(action=action, effect=action.effect, progress=True, known=True, fields=fields)
        flags = self._flags.get(tool_name)
        if flags is None:
            return ToolKind()
        return ToolKind(
            effect=flags.effect,
            exempt_from_budget=flags.exempt_from_budget,
            progress=flags.progress,
            known=True,
            helper=True,
        )

    @staticmethod
    def _register_runtime_tool(tool_obj: Any, *, tool_name: str) -> None:
        add_result = Runner.resource_mgr.add_tool(tool_obj, tag="agent.playwright.runtime")
        if add_result is None or getattr(add_result, "is_ok", lambda: False)():
            return
        error_value = getattr(add_result, "value", add_result)
        if "already exist" in str(error_value):
            return
        raise RuntimeError(f"Failed to register {tool_name} tool: {error_value}")

    async def cancel_run(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.request_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        await self._service.clear_cancel(session_id=session_id, request_id=request_id)
        return {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
            "error": None,
        }

    async def ensure_runtime_ready(self) -> None:
        """Make sure the service's driver session runs; the custom actions run on it from then on."""
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        await self._service.ensure_runtime_ready()
        session = self._service.session
        if session is self._session:
            return
        self._drop_session()
        profile = session.assembly.surface.profile
        self._context = ToolContext(
            session,
            self._page_state,
            profile.renderer,
            session.assembly.stack.info,
            profile.settings,
            False,
            self._workspace,
            self._language,
        )
        self._session = session
        self._controller.register_builtin_actions(upload_root=self._browser_settings.upload_root)
        for name, handler in CUSTOM_ACTIONS.items():
            self._controller.register_action(name, functools.partial(handler, self._context))

    def agent_parts(self, *, image_support: bool) -> AgentParts:
        """The factory tools and rails of the started session, built once per image-support value."""
        parts = self._parts.get(image_support)
        if parts is None:
            session = self._session
            parts = build_agent_parts(
                session,
                session.assembly.surface,
                capabilities=self._capabilities,
                image_support=image_support,
                language=self._language,
                workspace=self._workspace,
            )
            self._parts[image_support] = parts
            self._builders.update(parts.builders)
            for tool in parts.tools:
                flags = getattr(tool, "flags", None)
                if isinstance(flags, ToolFlags):
                    self._flags[tool.card.name] = flags
                elif tool.card.name in parts.builders:
                    properties = (tool.card.input_params or {}).get("properties") or {}
                    self._fields[tool.card.name] = frozenset(properties)
        return parts

    def _drop_session(self) -> None:
        self._session = None
        self._context = None
        self._parts = {}

    async def capture_browser_state(self, *, action_group_id: str = "") -> Dict[str, Any]:
        """The observation the next model call reads: the last one while it is current, else a fresh one."""
        await self.ensure_runtime_ready()
        page_state = self._page_state
        observation = page_state.fresh_observation
        dom_error = None
        snapshot_audit: Dict[str, Any] = {}
        if observation is None:
            try:
                observation = await self._context.observe(Observe())
            except DriverError as exc:
                dom_error = f"page observation failed: {exc}"
                logger.warning("[BrowserAgentRuntime] page observation failed: %s", exc, exc_info=True)
        else:
            self.observations_reused += 1
        if observation is not None:
            snapshot_audit = write_browser_agent_audit_artifact("ax_snapshot", observation.native_text or "")

        metadata, metadata_error = await page_metadata(self._context)
        exported = page_state.export()

        errors = [error for error in (dom_error, metadata_error) if error]
        semantic_state = metadata.get("semantic_state")
        if not isinstance(semantic_state, dict):
            semantic_state = {}
        semantic_state.update(
            {
                "url": metadata.get("url") or exported["url"],
                "field_coverage": sorted(page_state.field_coverage),
            }
        )
        semantic_tracker = self._semantic_state_tracker
        if metadata_error:
            semantic_progress = semantic_tracker.latest
            semantic_progress.update(
                {
                    "progress": "unknown",
                    "observable_progress": False,
                    "capture_error": metadata_error,
                }
            )
        else:
            semantic_progress = semantic_tracker.observe(
                semantic_state,
                action_group_id=action_group_id,
            )
        semantic_state = self._with_semantic_provenance(
            semantic_progress,
            fallback_state=semantic_state,
        )
        return {
            "ok": not errors,
            "error": "; ".join(errors) or None,
            "url": metadata.get("url") or exported["url"],
            "title": metadata.get("title") or exported["title"],
            "tabs": metadata.get("tabs") or [],
            "page_position": metadata.get("page_position") or {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state.get("field_coverage") or [],
            "page_state": exported,
            "dom": "",
            "dom_error": dom_error,
            "audit": {"ax_snapshot": snapshot_audit} if snapshot_audit else {},
        }

    async def capture_reconciliation_browser_state(self, *, action_group_id: str) -> Dict[str, Any]:
        """Reconcile an ambiguous mutation from the page metadata alone, without observing."""

        await self.ensure_runtime_ready()
        metadata, metadata_error = await page_metadata(self._context)
        page_state = self.export_page_state()
        semantic_state = metadata.get("semantic_state")
        if not isinstance(semantic_state, dict):
            semantic_state = {}
        semantic_state.update(
            {
                "url": metadata.get("url") or page_state.get("url") or "",
                "field_coverage": page_state.get("field_coverage") or [],
            }
        )
        tracker = self._semantic_state_tracker
        if metadata_error:
            semantic_progress = tracker.latest
            semantic_progress.update(
                {
                    "progress": "unknown",
                    "observable_progress": False,
                    "capture_error": metadata_error,
                }
            )
        else:
            semantic_progress = tracker.observe(semantic_state, action_group_id=action_group_id)
        semantic_state = self._with_semantic_provenance(
            semantic_progress,
            fallback_state=semantic_state,
        )
        return {
            "ok": not metadata_error,
            "error": metadata_error,
            "url": metadata.get("url") or page_state.get("url") or "",
            "title": metadata.get("title") or page_state.get("title") or "",
            "tabs": metadata.get("tabs") or [],
            "page_position": metadata.get("page_position") or {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state.get("field_coverage") or [],
            "page_state": page_state,
            "dom": "",
            "dom_error": None,
            "reconciliation_only": True,
        }

    async def capture_compact_browser_state(self, *, action_group_id: str) -> Dict[str, Any]:
        """Merge completed read-only observations without another browser round trip."""
        page_state = self.export_page_state()
        semantic_tracker = self._semantic_state_tracker
        semantic_state = semantic_tracker.current_state
        semantic_state.update(
            {
                "url": page_state.get("url") or semantic_state.get("url") or "",
                "field_coverage": page_state.get("field_coverage") or semantic_state.get("field_coverage") or [],
            }
        )
        semantic_progress = semantic_tracker.observe(
            semantic_state,
            action_group_id=action_group_id,
        )
        semantic_state = self._with_semantic_provenance(
            semantic_progress,
            fallback_state=semantic_state,
        )
        return {
            "ok": True,
            "error": None,
            "url": page_state.get("url") or "",
            "title": page_state.get("title") or "",
            "tabs": [],
            "page_position": {},
            "semantic_state": semantic_state,
            "semantic_progress": semantic_progress,
            "field_coverage": semantic_state["field_coverage"],
            "page_state": page_state,
            "dom": "",
            "dom_error": None,
        }

    def _with_semantic_provenance(
        self,
        semantic_progress: Dict[str, Any],
        *,
        fallback_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        tracked_state = semantic_progress.get("semantic_state")
        semantic_state = dict(tracked_state) if isinstance(tracked_state, dict) else dict(fallback_state)
        semantic_state["generation_id"] = self.generation_id
        semantic_progress["semantic_state"] = semantic_state
        return semantic_state

    async def acquire_task_resources(self) -> None:
        """Acquire one task reference before invoking a reusable subagent."""
        _ACTIVE_BROWSER_RUNTIMES.add(self)
        self._service.acquire_task_binding()

    async def ensure_started(self) -> None:
        """The runner's agent: the worker on Playwright MCP, the main agent on this facade otherwise."""
        if self._service.core.topology == "direct":
            if self._agent is None:
                self._agent = self._direct_agent()
            return
        await self._service.ensure_started()
        await self.ensure_runtime_ready()
        if self._browser_custom_action_tool is not None:
            return
        from .runtime_tools import BrowserCustomActionTool, BrowserListActionsTool

        self._browser_custom_action_tool = BrowserCustomActionTool(self, language="en")
        self._browser_list_actions_tool = BrowserListActionsTool(self, language="en")
        self._register_runtime_tool(self._browser_custom_action_tool, tool_name="browser_custom_action")
        self._register_runtime_tool(self._browser_list_actions_tool, tool_name="browser_list_custom_actions")

    def _direct_agent(self) -> Any:
        from openjiuwen.harness.subagents.browser_agent import create_browser_agent

        core = self._service.core
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=self._service.provider,
                api_key=self._service.api_key,
                api_base=self._service.api_base,
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(
                model=self._service.model_name, temperature=core.worker_temperature, top_p=core.worker_top_p
            ),
        )
        return create_browser_agent(
            model,
            language=self._language,
            temperature=core.worker_temperature,
            max_iterations=self._service.guardrails.max_steps,
            workspace=self._workspace,
            browser_capabilities=list(self._capabilities),
            runtime=self,
        )

    async def run_browser_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        if self._agent is not None:
            return await self._run_direct(task, session_id, request_id, timeout_s)
        return await self._service.run_task(
            task=task,
            session_id=session_id,
            request_id=request_id,
            timeout_s=timeout_s,
        )

    async def _run_direct(
        self, task: str, session_id: Optional[str], request_id: Optional[str], timeout_s: Optional[int]
    ) -> Dict[str, Any]:
        sid = self._service.session_new(session_id)
        rid = (request_id or "").strip() or uuid.uuid4().hex
        timeout = int(timeout_s) if timeout_s else self._service.guardrails.timeout_s
        inputs = {"query": task, "conversation_id": f"{sid}:direct:{rid}:{uuid.uuid4().hex}", "request_id": rid}
        try:
            result = await asyncio.wait_for(Runner.run_agent(self._agent, inputs), timeout=float(timeout))
        except TimeoutError:
            return self._direct_answer(sid, rid, error=f"task_timeout: exceeded {timeout}s")
        except Exception as exc:  # noqa: BLE001 - the runner reads the error from the answer
            return self._direct_answer(sid, rid, error=str(exc) or repr(exc))
        return self._direct_answer(sid, rid, result=result if isinstance(result, dict) else {"output": result})

    def _direct_answer(
        self, session_id: str, request_id: str, *, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None
    ) -> Dict[str, Any]:
        """The runner's answer dict from a main-agent run: the page from the page state, status when reported."""
        result = result or {}
        surface = self._page_state.surface_context
        payload = result.get("authoritative_browser_result")
        progress = result.get("progress_state")
        error = error if error is not None else result.get("error")
        answer: Dict[str, Any] = {
            "ok": not error and result.get("result_type") != "error",
            "session_id": session_id,
            "request_id": request_id,
            "final": str(result.get("output") or ""),
            "page": {"url": surface.url if surface else "", "title": surface.title if surface else ""},
            "screenshot": None,
            "error": error or None,
        }
        status = (payload or {}).get("status") or (progress or {}).get("status")
        if status:
            answer["status"] = status
        if progress:
            answer["progress"] = progress
        return answer

    async def run_custom_action(
        self,
        *,
        action: str,
        session_id: str = "",
        request_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_runtime_ready()
        self._controller.bind_runtime(self)
        return await self._controller.run_action(
            action=action,
            session_id=session_id,
            request_id=request_id,
            **(params or {}),
        )

    async def probe_for_policy(self, source: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run a policy-owned page function and register its elements as PageState targets."""
        await self.ensure_runtime_ready()
        return await probe_for_policy(self._context, source, params)

    async def activate_page(self, url: str) -> bool:
        """Bring the page at ``url`` (else the current one) to the front so its timers run and its menus render."""
        await self.ensure_runtime_ready()
        return await activate_page(self._context, url)

    async def list_actions(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "actions": self._controller.list_actions(),
            "details": self._controller.describe_actions(),
        }

    async def runtime_health(self) -> Dict[str, Any]:
        session = self._service.session
        if session is not None and self._service.started:
            health = await session.health()
        else:
            health = Health(ok=False, detail="not started")
        return {
            "ok": health.ok,
            "started": bool(self._service.started),
            "detail": health.detail,
            "provider": self._service.provider,
            "api_base": self._service.api_base,
            "model_name": self._service.model_name,
        }

    async def shutdown(self) -> None:
        try:
            await self._service.shutdown()
        finally:
            _ACTIVE_BROWSER_RUNTIMES.discard(self)
            self._drop_session()
            self._agent = None
            # The next asyncio.run loop must not find clients bound to this one (the runner runs one loop per task).
            await OpenAIModelClient.aclose()

    async def release_task_resources(self) -> None:
        """Release task bindings while preserving Chrome and its profile."""
        fully_released = await self._service.release_task_binding()
        if fully_released:
            self._page_state.reset()
            self._semantic_state_tracker.reset()

    async def reset(self) -> None:
        """Release the current browser and restart lazily on the next task."""
        try:
            await self._service.reset()
        finally:
            self._drop_session()
            self._page_state.reset()
            self._semantic_state_tracker.reset()

    @property
    def semantic_progress(self) -> Dict[str, Any]:
        """Return the latest task-local semantic progress observation."""
        return self._semantic_state_tracker.latest

    def acknowledge_semantic_replan(self) -> None:
        """Acknowledge one materially different action selected after replanning."""
        self._semantic_state_tracker.acknowledge_replan()

    def reset_semantic_task(self) -> None:
        """Start semantic loop tracking for a new user task without resetting Chrome."""
        self._semantic_state_tracker.reset()

async def reset_active_browser_runtimes() -> int:
    """Reset every live browser runtime owned by this process."""
    runtimes = list(_ACTIVE_BROWSER_RUNTIMES)
    if not runtimes:
        return 0

    results = await asyncio.gather(
        *(runtime.reset() for runtime in runtimes),
        return_exceptions=True,
    )
    reset_count = 0
    for runtime, result in zip(runtimes, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Failed to reset active browser runtime %s: %s",
                id(runtime),
                result,
            )
            continue
        reset_count += 1
    return reset_count


async def reset_managed_browser_runtime(
    *,
    browser_key: str = "",
    profile_name: str = "jiuwenclaw",
    display_mode: str,
    browser_binary: str = "",
) -> int:
    """Reset only the configured managed browser, including its idle handle."""
    normalized_binary = BrowserService.normalize_browser_binary(browser_binary)
    normalized_key = str(browser_key or "").strip()
    normalized_profile = str(profile_name or "").strip()
    normalized_mode = str(display_mode or "").strip().lower()

    reset_count = 0
    for runtime in list(_ACTIVE_BROWSER_RUNTIMES):
        identity = runtime.service.lifecycle_identity
        matches_browser = identity.browser_key == normalized_key
        matches_profile = identity.profile_name == normalized_profile
        matches_mode = identity.display_mode == normalized_mode
        matches_binary = identity.browser_binary == normalized_binary
        if identity.driver_mode != "managed":
            continue
        if not matches_browser or not matches_profile:
            continue
        if not matches_mode or not matches_binary:
            continue
        try:
            await runtime.reset()
        except Exception as exc:
            logger.warning(
                "Failed to reset managed browser runtime %s: %s",
                id(runtime),
                exc,
            )
        else:
            reset_count += 1

    reset_count += await BrowserService.reset_registered_managed_browser(
        browser_key=normalized_key,
        profile_name=normalized_profile,
        display_mode=normalized_mode,
        browser_binary=normalized_binary,
    )
    return reset_count


class BrowserRuntimeRail(AgentRail):
    """Rail that makes direct browser sessions resumable and completion-aware."""

    def __init__(self, runtime: BrowserAgentRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._railed: WeakSet[Any] = WeakSet()
        self._status_logger = (
            BrowserSubagentStatusLogger(command_of=self._command_of)
            if is_browser_subagent_status_log_enabled(runtime.core_settings)
            else None
        )
        browser_agent_log_info(
            "[BROWSER_SUBAGENT_BOOT] enabled=%s logger=%s",
            self._status_logger is not None,
            type(self._status_logger).__name__ if self._status_logger is not None else None,
        )

    def _command_of(self, tool_name: str, tool_args: Any) -> str:
        return self._command_name(self._runtime.tool_kind(tool_name, tool_args))

    def _emit_status(self, method_name: str, ctx: AgentCallbackContext) -> None:
        if self._status_logger is None:
            return
        method = getattr(self._status_logger, method_name, None)
        if not callable(method):
            browser_agent_log_warning(
                "[BROWSER_SUBAGENT_ERROR] missing_status_method=%s",
                method_name,
            )
            return
        try:
            method(ctx)
        except Exception:
            browser_agent_log_warning(
                "[BROWSER_SUBAGENT_ERROR] method=%s",
                method_name,
                exc_info=True,
            )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if isinstance(getattr(ctx, "extra", None), dict):
            ctx.extra[_BROWSER_LOG_CONTEXT_TOKEN_KEY] = set_browser_agent_log_context(True)
            try:
                timeout_s = max(1, int(self._runtime.service.guardrails.timeout_s))
            except (TypeError, ValueError, AttributeError):
                timeout_s = 600
            ctx.extra.setdefault(_BROWSER_TASK_DEADLINE_KEY, time.monotonic() + timeout_s)
            run_context = self._browser_run_context(ctx)
            if run_context.get("browser_resume") is True:
                ctx.extra["_browser_resume_requested"] = True
        if ctx.steering_queue is None:
            ctx.bind_steering_queue(asyncio.Queue())
        self._emit_status("before_invoke", ctx)
        await self._runtime.ensure_runtime_ready()
        await self._add_agent_parts(ctx)
        session = getattr(ctx, "session", None)
        if session is None:
            return
        self._hydrate_service_progress_from_session(session)
        query = getattr(getattr(ctx, "inputs", None), "query", None)
        task_text = str(query or "").strip()
        if task_text:
            state = self._ensure_task_state(
                session,
                task_text,
                resume=bool(ctx.extra.get("_browser_resume_requested")),
            )
            self._bind_shared_task_deadline(ctx, session, state)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_model_call", ctx)
        if self._browser_run_context(ctx).get("browser_resume") is True:
            ctx.extra["_browser_resume_requested"] = True
        session = getattr(ctx, "session", None)
        state: Dict[str, Any] = {}
        if session is not None:
            messages = getattr(getattr(ctx, "inputs", None), "messages", None) or []
            task_text = latest_browser_user_request(messages)
            if task_text:
                state = self._ensure_task_state(
                    session,
                    task_text,
                    resume=bool(ctx.extra.get("_browser_resume_requested")),
                )
                self._bind_shared_task_deadline(ctx, session, state)
            self._sync_semantic_progress(session)
            loaded_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
            state = loaded_state if isinstance(loaded_state, dict) else {}

        if self._finish_if_task_deadline_exhausted(ctx, session, state):
            return

        if self._prepare_terminal_synthesis(ctx, session, state):
            return

        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is None:
            return
        if str(state.get("status") or "").strip().lower() not in _BROWSER_TERMINAL_STATUSES:
            builder.remove_section("browser_terminal_synthesis")

        image_input_supported = self._image_input_supported(ctx.agent)
        builder.add_section(
            PromptSection(
                name=_BROWSER_IMAGE_CAPABILITY_SECTION_NAME,
                content=_BROWSER_IMAGE_CAPABILITY_GUIDANCE[image_input_supported],
                priority=85,
            )
        )
        builder.add_section(
            PromptSection(
                name=_BROWSER_DATE_PICKER_SECTION_NAME,
                content=_BROWSER_DATE_PICKER_GUIDANCE,
                priority=85,
            )
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_model_call", ctx)
        response = getattr(getattr(ctx, "inputs", None), "response", None)
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if (
            tool_calls
            and isinstance(state, dict)
            and str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES
        ):
            ctx.request_force_finish(self._structured_terminal_result(state))
            return
        response_text = str(getattr(response, "content", "") or "")
        if self._is_unfinished_model_protocol_response(state, tool_calls, response_text):
            if self._request_model_protocol_recovery(ctx, session, state):
                return
            self._finish_model_protocol_failure(ctx, session, state)
            return
        call_ids = [str(tool_call.id) for tool_call in tool_calls if getattr(tool_call, "id", None)]
        extra = getattr(ctx, "extra", None)
        if not call_ids or not isinstance(extra, dict):
            return
        action_group_id = hashlib.sha256("\x1f".join(call_ids).encode("utf-8")).hexdigest()[:16]
        group_by_call = extra.setdefault(_BROWSER_ACTION_GROUP_BY_CALL_KEY, {})
        for call_id in call_ids:
            group_by_call[call_id] = action_group_id
        extra.setdefault(_BROWSER_ACTION_GROUP_RESULTS_KEY, {})[action_group_id] = {
            "expected": call_ids,
            "completed": [],
            "read_only": all(
                self._is_read_only_recovery(self._runtime.tool_kind(call.name, call.arguments), call.arguments)
                for call in tool_calls
            ),
        }

    @staticmethod
    def _has_unfinished_tool_intent(response_text: str) -> bool:
        return _BROWSER_UNFINISHED_TOOL_INTENT_RE.search(str(response_text or "")) is not None

    @classmethod
    def _is_unfinished_model_protocol_response(
        cls,
        state: Any,
        tool_calls: list[Any],
        response_text: str,
    ) -> bool:
        if tool_calls or not isinstance(state, dict):
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status in _BROWSER_TERMINAL_STATUSES:
            return False
        return cls._has_unfinished_tool_intent(response_text)

    @staticmethod
    def _request_model_protocol_recovery(
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        retry_count = int(state.get("model_protocol_retry_count") or 0)
        extra = getattr(ctx, "extra", None)
        deadline = float(extra.get(_BROWSER_TASK_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        deadline_exhausted = bool(deadline and deadline - time.monotonic() <= 5.0)
        if retry_count >= _BROWSER_MODEL_PROTOCOL_RETRY_LIMIT or deadline_exhausted:
            return False
        state["model_protocol_retry_count"] = retry_count + 1
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        if ctx.steering_queue is None:
            ctx.bind_steering_queue(asyncio.Queue())
        ctx.push_steering(
            "Your previous response described another browser tool call but did not emit a real tool call. "
            "Continue in this same task: emit the actual tool call, or answer concisely using only evidence "
            "already collected."
        )
        ctx.request_model_continue()
        return True

    @classmethod
    def _finish_model_protocol_failure(
        cls,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> None:
        evidence_available = cls._has_task_evidence(state)
        state["status"] = "partial" if evidence_available else "blocked"
        state["next_action_class"] = "finish"
        state["terminal_reason"] = "model_tool_protocol_error"
        blockers = list(state.get("blockers") or [])
        if "model_tool_protocol_error" not in blockers:
            blockers.append("model_tool_protocol_error")
        state["blockers"] = blockers[:10]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        ctx.request_force_finish(cls._structured_terminal_result(state))

    def _prepare_terminal_synthesis(
        self,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        """Allow one tool-disabled prose pass, then force the runtime result."""

        status = str(state.get("status") or "").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            return False
        if state.get(_BROWSER_TERMINAL_SYNTHESIS_KEY):
            ctx.request_force_finish(self._structured_terminal_result(state))
            return True

        state[_BROWSER_TERMINAL_SYNTHESIS_KEY] = True
        state["next_action_class"] = "finish"
        if session is not None:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        inputs = getattr(ctx, "inputs", None)
        if inputs is not None:
            inputs.tools = []
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            authoritative = self._authoritative_terminal_payload(state)
            builder.add_section(
                PromptSection(
                    name="browser_terminal_synthesis",
                    content={
                        "en": (
                            "Browser execution has ended. Do not call tools. Summarize the runtime-owned "
                            f"result without changing status, blockers, missing fields, or evidence: {authoritative}"
                        ),
                        "cn": (
                            "浏览器执行已结束。不要调用工具。请说明以下由运行时确定的结果，不得修改状态、"
                            f"阻断项、缺失字段或证据：{authoritative}"
                        ),
                    },
                    priority=100,
                )
            )
        return False

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_model_exception", ctx)
        exception = getattr(ctx, "exception", None)
        if not self._is_retryable_model_exception(exception):
            return
        extra = getattr(ctx, "extra", None)
        deadline = float(extra.get(_BROWSER_TASK_DEADLINE_KEY) or 0.0) if isinstance(extra, dict) else 0.0
        remaining_s = deadline - time.monotonic() if deadline else 0.0
        if ctx.retry_attempt < _BROWSER_MODEL_RETRY_LIMIT and remaining_s > 5.0:
            ctx.request_retry(delay_seconds=min(0.5, max(0.0, remaining_s - 5.0)))
            return
        ctx.request_force_finish(self._structured_model_failure(ctx, exception))

    @staticmethod
    def _is_retryable_model_exception(exception: Optional[Exception]) -> bool:
        if isinstance(exception, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        message = str(exception or "").lower()
        retryable_tokens = (
            "timeout",
            "timed out",
            "429",
            "500",
            "502",
            "503",
            "504",
            "upstream",
            "rate limit",
            "service unavailable",
        )
        return _contains_any_token(message, retryable_tokens)

    def _structured_model_failure(
        self,
        ctx: AgentCallbackContext,
        exception: Optional[Exception],
    ) -> Dict[str, Any]:
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        evidence_available = self._has_task_evidence(state)
        status = "partial" if evidence_available else "failed"
        if state:
            state["status"] = "partial" if evidence_available else "blocked"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = "model_provider_unavailable"
            blockers = list(state.get("blockers") or [])
            if "model_provider_unavailable" not in blockers:
                blockers.append("model_provider_unavailable")
            state["blockers"] = blockers[:10]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        payload = {
            "ok": False,
            "status": status,
            "error": {
                "code": "model_provider_unavailable",
                "message": str(exception or "model provider failed")[:500],
                "retry_attempts": int(ctx.retry_attempt) + 1,
            },
            "progress": {
                "task_id": state.get("task_id"),
                "status": state.get("status"),
                "current_phase": state.get("current_phase"),
                "required_fields": list(state.get("required_fields") or [])[:32],
                "field_coverage": list(state.get("field_coverage") or [])[:32],
                "required_evidence_slots": list(state.get("required_evidence_slots") or [])[:12],
                "evidence_slots": list(state.get("evidence_slots") or [])[-12:],
                "blockers": list(state.get("blockers") or [])[:10],
            }
            if state
            else {},
        }
        return {
            "output": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "result_type": "error",
            "error": "model_provider_unavailable",
            "progress_state": self._load_progress_state(session).to_dict() if session is not None else {},
        }

    @classmethod
    def _authoritative_terminal_payload(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        status = str(state.get("status") or "partial").strip().lower()
        missing = cls._missing_completion_requirements(state)
        unverified = cls._advisory_missing_fields(state) if status == "completed" else set()
        missing = [field for field in missing if field not in unverified]
        blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
        missing_slots = [slot for slot in cls._missing_evidence_slots(state) if slot["field"] not in unverified]
        unavailable_slots = cls._unavailable_evidence_slots(state)
        retryable = cls._terminal_result_retryable(state, missing)
        deadline_started_at = float(state.get("deadline_started_at") or 0.0)
        deadline_at = float(state.get("deadline_at") or 0.0)
        now = time.time()
        return {
            "status": status,
            "task_id": state.get("task_id"),
            "current_phase": state.get("current_phase"),
            "missing_fields": missing[:32],
            "unverified_fields": sorted(unverified)[:32],
            "missing_slots": missing_slots[:12],
            "unavailable_slots": unavailable_slots[:12],
            "requested_slots": [
                dict(slot)
                for slot in (state.get("required_evidence_slots") or [])[:12]
                if isinstance(slot, dict)
            ],
            "blockers": blockers[:10],
            "field_coverage": list(state.get("field_coverage") or [])[:32],
            "evidence": list(state.get("evidence_slots") or [])[-12:],
            "observations": cls._task_observations(state)[-3:],
            "current_page": dict(state.get("last_page") or {}),
            "requested_result_count": int(state.get("requested_result_count") or 0),
            "observed_result_count": int(state.get("observed_result_count") or 0),
            "terminal_reason": state.get("terminal_reason"),
            "retryable": retryable,
            "recommended_recovery": cls._recommended_recovery(state, missing),
            "resume_count": int(state.get("resume_count") or 0),
            "deadline": {
                "budget_s": float(state.get("deadline_budget_s") or 0.0),
                "elapsed_s": round(max(0.0, now - deadline_started_at), 3) if deadline_started_at else 0.0,
                "remaining_s": round(max(0.0, deadline_at - now), 3) if deadline_at else 0.0,
                "invocation_remaining_s": float(state.get("invocation_remaining_s") or 0.0),
            },
        }

    @classmethod
    def _terminal_result_retryable(
        cls,
        state: Dict[str, Any],
        missing: Optional[list[str]] = None,
    ) -> bool:
        status = str(state.get("status") or "").strip().lower()
        if status == "completed" or int(state.get("resume_count") or 0) >= _BROWSER_TASK_RESUME_LIMIT:
            return False
        blockers = " ".join(str(item or "").strip().lower() for item in state.get("blockers") or [])
        if any(token in blockers for token in _BROWSER_NON_RETRYABLE_BLOCKER_TOKENS):
            return False
        if "phase_budget_exhausted" in blockers:
            return False
        requirements = missing if missing is not None else cls._missing_completion_requirements(state)
        unresolved_slots = cls._missing_evidence_slots(state)
        if requirements and not unresolved_slots and cls._unavailable_evidence_slots(state):
            return False
        return bool(
            status in {"partial", "blocked"}
            and (
                requirements
                or cls._has_task_evidence(state)
                or state.get("terminal_reason") in {
                    "model_provider_unavailable",
                    "model_tool_protocol_error",
                    "runtime_completion_requirements_missing",
                    "runtime_blocked",
                    "semantic_replan_denial_budget_exhausted",
                    "task_invocation_slice_exhausted",
                }
            )
        )

    @staticmethod
    def _recommended_recovery(state: Dict[str, Any], missing: list[str]) -> str:
        reported = str(state.get("worker_reported_next_action") or "").strip()
        if reported:
            return reported[:200]
        reason = str(state.get("terminal_reason") or "").strip().lower()
        if reason in {
            "model_provider_unavailable",
            "model_tool_protocol_error",
            "task_invocation_slice_exhausted",
        }:
            return "retry_model_step_from_current_page"
        if missing and not BrowserRuntimeRail._missing_evidence_slots(state):
            if BrowserRuntimeRail._unavailable_evidence_slots(state):
                return "finish_with_explicit_unknown_fields"
        if missing:
            return "collect_missing_evidence_from_current_page"
        return "replan_from_current_page"

    @classmethod
    def _structured_terminal_result(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = cls._authoritative_terminal_payload(state)
        status = str(payload.get("status") or "partial")
        return {
            "output": json.dumps(
                {"browser_result": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "result_type": "answer" if status == "completed" else "error",
            "error": None if status == "completed" else "browser_task_incomplete",
            "authoritative_browser_result": payload,
        }

    @classmethod
    def _render_authoritative_terminal_output(
        cls,
        state: Dict[str, Any],
        model_summary: str,
    ) -> tuple[str, Dict[str, Any]]:
        payload = cls._authoritative_terminal_payload(state)
        if str(model_summary or "").lstrip().startswith("{"):
            try:
                previous = json.loads(model_summary).get("browser_result")
            except (ValueError, AttributeError):
                previous = None
            if isinstance(previous, dict):
                model_summary = str(previous.get("summary") or state.get("last_worker_final") or "")
        if str(model_summary or "").strip():
            payload["summary"] = str(model_summary).strip()[:8_000]
        return (
            json.dumps({"browser_result": payload}, ensure_ascii=False, separators=(",", ":")),
            payload,
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("before_tool_call", ctx)
        if self._handle_progress_tool_alias(ctx):
            return
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        self._bind_shared_task_deadline(ctx, session, state)
        if self._finish_if_task_deadline_exhausted(ctx, session, state):
            return
        try:
            self._prepare_tool_call(ctx)
        except (ValueError, BaseError) as exc:
            self._deny_tool_call(ctx, exc)

    def _handle_progress_tool_alias(self, ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "").strip().lower()
        if tool_name != "browser_progress" and not tool_name.endswith("_browser_progress"):
            return False
        session = getattr(ctx, "session", None)
        if session is None:
            self._deny_tool_call(ctx, ValueError("browser_progress requires an active browser task session"))
            return True
        payload = self._coerce_tool_args(getattr(inputs, "tool_args", None))
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        state = state if isinstance(state, dict) else {}
        reported_status = str(payload.get("status") or "").strip().lower()
        if not reported_status:
            missing = self._missing_completion_requirements(state)
            blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
            runtime_ready = bool(
                state.get("structured_evidence")
                or state.get("evidence_slots")
                or state.get("field_coverage")
            )
            if not blockers and not missing and runtime_ready:
                payload["status"] = "completed"
                reported_status = "completed"
            elif missing and not self._missing_evidence_slots(state) and self._unavailable_evidence_slots(state):
                payload["status"] = "partial"
                reported_status = "partial"
            else:
                self._deny_tool_call(
                    ctx,
                    ValueError(
                        "browser_progress did not include a terminal status and runtime evidence is "
                        "not yet complete. Continue this browser task and collect only the unresolved "
                        "requirements before finishing."
                    ),
                )
                return True
        if reported_status not in _BROWSER_TERMINAL_STATUSES:
            self._deny_tool_call(
                ctx,
                ValueError(
                    "<browser_progress> is a final text protocol. Continue with a real browser tool "
                    "until status is completed, partial, or blocked."
                ),
            )
            return True
        self._apply_worker_progress_to_task_state(session, payload, "")
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        state = state if isinstance(state, dict) else {}
        ctx.request_force_finish(self._structured_terminal_result(state))
        return True

    def _prepare_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        if tool_name.strip().lower() == "browser_progress":
            raise ValueError(
                "<browser_progress> is a text protocol appended to the final response, not a callable tool."
            )
        tool_args = getattr(inputs, "tool_args", None)
        kind = self._runtime.tool_kind(tool_name, tool_args)
        # Only the dialect that addresses elements by ``target`` takes the runtime's target ids and ref aliases.
        if kind.known and not kind.helper and kind.fields & _BROWSER_REF_TARGET_KEYS:
            normalized_args = self._normalize_target_args(tool_args, kind.fields)
            if normalized_args is not tool_args:
                inputs.tool_args = tool_args = normalized_args
                kind = self._runtime.tool_kind(tool_name, tool_args)
            tool_name, tool_args, kind = self._rewrite_primary_link_click(inputs, tool_name, tool_args, kind)
            self._reject_unresolved_targets(tool_args)
        evidence_fields = self._runtime_evidence_fields(tool_args)
        if kind.helper and not self._image_input_supported(getattr(ctx, "agent", None)):
            steps = _tool_args(tool_args).get("steps")
            if any(
                isinstance(step, dict) and str(step.get("op") or "").strip().lower() == "screenshot"
                for step in (steps if isinstance(steps, list) else [])
            ):
                raise ValueError(
                    "Screenshot input is unavailable for this browser agent. "
                    "Remove the screenshot batch step and use DOM probes or "
                    "structured extraction."
                )
        self._validate_model_tool_args(ctx, tool_name, tool_args)
        session = getattr(ctx, "session", None)
        self._sync_semantic_progress(session)
        group = self._tool_action_group(ctx)
        action_class = self._consume_phase_budget(
            session,
            kind,
            tool_name,
            tool_args,
            current_page_state=self._runtime.export_page_state(),
            replan_group_admitted=bool(group.get("read_only") and group.get("trial_admitted")),
        )
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if isinstance(state, dict) and state.get("replan_trial_pending") and group.get("read_only"):
            group["trial_admitted"] = True
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            tool_call_id = self._tool_call_id(inputs)
            tool_runtime_state = extra.setdefault(_BROWSER_TOOL_RUNTIME_STATE_KEY, {})
            tool_runtime_state[tool_call_id] = {
                "started_at": time.perf_counter(),
                "action_class": action_class,
                "evidence_fields": evidence_fields,
            }

    async def _add_agent_parts(self, ctx: AgentCallbackContext) -> None:
        agent = getattr(ctx, "agent", None)
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return
        parts = self._runtime.agent_parts(image_support=self._image_input_supported(agent))
        for tool in parts.tools:
            ability_manager.add_ability(tool.card, tool)
        if agent in self._railed:
            return
        for rail in parts.rails:
            await agent.register_rail(rail)
        self._railed.add(agent)

    @staticmethod
    def _tool_action_group(ctx: AgentCallbackContext) -> Dict[str, Any]:
        extra = getattr(ctx, "extra", None) or {}
        call_id = BrowserRuntimeRail._tool_call_id(getattr(ctx, "inputs", None))
        group_id = (extra.get(_BROWSER_ACTION_GROUP_BY_CALL_KEY) or {}).get(call_id)
        return (extra.get(_BROWSER_ACTION_GROUP_RESULTS_KEY) or {}).get(group_id, {})

    @classmethod
    def _validate_model_tool_args(cls, ctx: AgentCallbackContext, tool_name: str, tool_args: Any) -> None:
        """Validate locally before reserving a browser strategy trial."""
        manager = getattr(getattr(ctx, "agent", None), "ability_manager", None)
        get_tool = getattr(manager, "get", None)
        tool = get_tool(tool_name) if callable(get_tool) else None
        if callable(get_tool) and tool is None:
            raise build_error(StatusCode.AGENT_TOOL_NOT_FOUND, error_msg=f"Tool {tool_name} is not registered")
        schema = getattr(tool, "input_params", None)
        if isinstance(schema, dict):
            SchemaUtils.validate_with_schema(cls._coerce_tool_args(tool_args), schema)

    def _runtime_evidence_fields(self, tool_args: Any) -> list[str]:
        args = self._coerce_tool_args(tool_args)
        fields: set[str] = set()
        for value in (args.get("field"), args.get("fields")):
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str):
                    canonical = self._canonical_field_name(item)
                    if canonical in _BROWSER_FIELD_ALIASES:
                        fields.add(canonical)

        target_id = str(args.get("target_id") or args.get("target") or "").strip()
        if target_id.startswith("t_g"):
            try:
                target = self._runtime.resolve_model_target_id(target_id)
            except ValueError:
                target = None
            if target is not None and target.field_name in _BROWSER_FIELD_ALIASES:
                fields.add(target.field_name)
        return sorted(fields)

    def _rewrite_primary_link_click(
        self,
        inputs: Any,
        tool_name: str,
        tool_args: Any,
        kind: ToolKind,
    ) -> tuple[str, Any, ToolKind]:
        if not isinstance(kind.action, Click):
            return tool_name, tool_args, kind
        primary_link = self._runtime.resolve_primary_link(tool_args)
        if not isinstance(primary_link, str) or not primary_link:
            return tool_name, tool_args, kind
        inputs.tool_name = re.sub(
            r"browser_click$",
            "browser_navigate",
            tool_name,
            flags=re.IGNORECASE,
        )
        inputs.tool_args = {"url": primary_link}
        return inputs.tool_name, inputs.tool_args, self._runtime.tool_kind(inputs.tool_name, inputs.tool_args)

    @staticmethod
    def _reject_unresolved_targets(tool_args: Any) -> None:
        for key in _BROWSER_REF_TARGET_KEYS:
            value = str(_tool_args(tool_args).get(key) or "").strip()
            if value.startswith("t_g"):
                raise ValueError(f"PageState target_id {value} has no executable locator.")

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("on_tool_exception", ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._emit_status("after_tool_call", ctx)
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "").strip()
        tool_args = getattr(inputs, "tool_args", None)
        tool_result = getattr(inputs, "tool_result", None)
        if self._is_denied(tool_result):
            self._set_tool_message_outcome(
                inputs,
                success=False,
                executed=False,
                state_changed=False,
                denied=True,
            )
            return

        record = command_record(tool_result)
        kind = self._kind_after(self._runtime.tool_kind(tool_name, tool_args), record)
        result = self._record_view(kind, record, tool_result)
        success, executed, state_changed = self._outcome(kind, record, tool_result, result)
        session = getattr(ctx, "session", None)
        if success and "probe_cards" in tool_name:
            self._enrich_probe_result_contract(session, inputs, tool_name, result)
        self._set_tool_message_outcome(
            inputs,
            success=success,
            executed=executed,
            state_changed=state_changed,
            denied=False,
            effect=kind.effect.value if kind.known and not kind.exempt_from_budget else None,
        )
        if success or state_changed:
            self._attach_page_state(inputs, kind, result)
        if session is None:
            self._compact_large_observation_message(inputs, kind, tool_name, result)
            self._mark_action_group_call_completed(ctx, {})
            return
        evidence_args = self._tool_evidence_args(ctx, inputs)
        progress_delta = self._record_phase_result(session, kind, tool_name, evidence_args, result)
        progress_delta.update(
            {
                "executed": executed,
                "denied": False,
                "state_changed": state_changed,
                "ambiguous": bool(not success and state_changed),
            }
        )
        action_class, elapsed_ms = self._consume_tool_call_timing(ctx, inputs)
        recovered = self._record_recent_action(
            session,
            kind=kind,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=result,
            action_class=str(action_class or ""),
            elapsed_ms=elapsed_ms,
            progress_delta=progress_delta,
        )
        if recovered:
            self._runtime.acknowledge_semantic_replan()
        self._compact_large_observation_message(inputs, kind, tool_name, result)
        if not kind.progress:
            self._mark_action_group_call_completed(ctx, progress_delta)
            return
        self._persist_service_progress_to_session(session)
        self._mark_action_group_call_completed(ctx, progress_delta)

    @staticmethod
    def _is_denied(tool_result: Any) -> bool:
        if isinstance(tool_result, PolicyDenied):
            return True
        return isinstance(tool_result, dict) and (
            tool_result.get("denied") is True or tool_result.get("executed") is False
        )

    @staticmethod
    def _kind_after(kind: ToolKind, record: CommandRecord | None) -> ToolKind:
        if record is None:
            return kind
        return dataclasses.replace(kind, action=record.action, effect=record.effect, known=True)

    def _record_view(self, kind: ToolKind, record: CommandRecord | None, tool_result: Any) -> Dict[str, Any]:
        """One dict the evidence machinery reads: a helper's payload as is, a command's record with its text."""
        data = getattr(tool_result, "data", None)
        content = data.get("content") if isinstance(data, dict) else None
        if isinstance(content, dict):
            return content
        if record is None:
            if isinstance(data, dict):
                return data
            if isinstance(tool_result, dict):
                return tool_result
            success = getattr(tool_result, "success", None)
            return {"ok": success is not False, "error": getattr(tool_result, "error", None), "result": data}
        surface = record.surface
        return {
            "ok": record.ok,
            "error": None if record.ok else (content or record.error),
            "result": record.value if isinstance(record.action, RunScript) else content,
            "url": str(getattr(surface, "url", "") or ""),
            "title": str(getattr(surface, "title", "") or ""),
            "generation_id": record.generation,
            "page_state": self._runtime.export_page_state(),
        }

    @classmethod
    def _outcome(
        cls, kind: ToolKind, record: CommandRecord | None, tool_result: Any, result: Dict[str, Any]
    ) -> tuple[bool, bool, bool]:
        if record is not None:
            metadata = record.message_metadata
            state_changed = bool(metadata["state_changed"]) or cls._partial_batch_mutated(result)
            return bool(metadata["success"]), bool(metadata["executed"]), state_changed
        success = getattr(tool_result, "success", None)
        if success is None:
            success = result.get("ok", True) is not False and not result.get("error")
        # A tool of this runtime answers without a record only when it decoded no command: nothing was sent.
        executed = not kind.known
        return bool(success), executed, bool(executed and success and not kind.read_only)

    @staticmethod
    def _partial_batch_mutated(result: Dict[str, Any]) -> bool:
        if str(result.get("status") or "").strip().lower() != "partial":
            return False
        for step in result.get("steps") or []:
            if not isinstance(step, dict) or step.get("ok") is not True:
                continue
            if str(step.get("op") or "").strip().lower() in _BATCH_MUTATING_TARGET_OPS:
                return True
        return False

    def _tool_evidence_args(self, ctx: AgentCallbackContext, inputs: Any) -> Dict[str, Any]:
        args = dict(self._coerce_tool_args(getattr(inputs, "tool_args", None)))
        extra = getattr(ctx, "extra", None)
        runtime_states = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY) if isinstance(extra, dict) else None
        call_state = runtime_states.get(self._tool_call_id(inputs)) if isinstance(runtime_states, dict) else None
        if isinstance(call_state, dict) and call_state.get("evidence_fields"):
            args["_runtime_evidence_fields"] = list(call_state["evidence_fields"])
        return args

    @classmethod
    def _enrich_probe_result_contract(
        cls,
        session: Any,
        inputs: Any,
        tool_name: str,
        tool_result: Dict[str, Any],
    ) -> None:
        if "probe_cards" not in str(tool_name or "").strip().lower():
            return
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        requested_count = int(state.get("requested_result_count") or 0)
        observed_count = int(tool_result.get("observed_count") or 0)
        diagnostics = tool_result.get("diagnostics")
        diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        diagnostics.update(
            {
                "requested_count": requested_count,
                "observed_count": observed_count,
            }
        )

        current_classes = cls._probe_card_classifications(tool_result.get("cards"))
        conflicts = cls._probe_classification_conflicts(state, current_classes)
        has_conflict = bool(conflicts or diagnostics.get("classification_conflict"))
        if has_conflict:
            diagnostics["classification_conflict"] = True
            if conflicts:
                diagnostics["classification_conflicts"] = conflicts
            fallback_count = int(state.get("probe_classification_fallback_count") or 0)
            if fallback_count < 1:
                diagnostics["recommended_fallback"] = "one_precise_probe"
                state["probe_classification_fallback_count"] = fallback_count + 1
            else:
                diagnostics["recommended_fallback"] = "retain_unknown_classification"
            diagnostics["precise_fallback_remaining"] = max(
                0,
                1 - int(state.get("probe_classification_fallback_count") or 0),
            )
        tool_result["requested_count"] = requested_count
        tool_result["observed_count"] = observed_count
        tool_result["diagnostics"] = diagnostics
        page_state = tool_result.get("page_state")
        if isinstance(page_state, dict):
            page_state["requested_count"] = requested_count
            page_state["observed_count"] = observed_count
            if has_conflict:
                page_state["classification_conflict"] = True
        state["last_card_probe"] = {
            "url": str(tool_result.get("url") or "")[:500],
            "generation_id": str(tool_result.get("generation_id") or ""),
            "classifications": current_classes,
        }
        if session is not None:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        cls._rewrite_tool_message_contract(inputs, tool_result)

    @staticmethod
    def _probe_classification_conflicts(
        state: Dict[str, Any],
        current_classes: Dict[str, str],
    ) -> list[str]:
        previous_probe = state.get("last_card_probe")
        previous_probe = previous_probe if isinstance(previous_probe, dict) else {}
        previous_classes = previous_probe.get("classifications")
        previous_classes = previous_classes if isinstance(previous_classes, dict) else {}
        return [
            identity
            for identity, classification in current_classes.items()
            if identity in previous_classes and previous_classes[identity] != classification
        ][:5]

    @staticmethod
    def _probe_card_classifications(cards: Any) -> Dict[str, str]:
        classifications: Dict[str, str] = {}
        for card in cards if isinstance(cards, list) else []:
            if not isinstance(card, dict):
                continue
            identity = " ".join(
                str(card.get("primary_link") or card.get("href") or card.get("title") or "").split()
            ).lower()[:300]
            if not identity:
                continue
            classifications[identity] = ":".join(
                (
                    str(card.get("region") or ""),
                    str(card.get("kind") or ""),
                    str(bool(card.get("is_ad"))).lower(),
                )
            )
        return classifications

    @staticmethod
    def _rewrite_tool_message_contract(inputs: Any, tool_result: Dict[str, Any]) -> None:
        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return
        if not isinstance(parsed, dict):
            return
        for key in ("requested_count", "observed_count", "diagnostics"):
            parsed[key] = tool_result.get(key)
        tool_msg.content = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    def _consume_tool_call_timing(self, ctx: AgentCallbackContext, inputs: Any) -> tuple[str, int]:
        extra = getattr(ctx, "extra", None)
        tool_runtime_state = extra.get(_BROWSER_TOOL_RUNTIME_STATE_KEY, {}) if isinstance(extra, dict) else {}
        call_state = (
            tool_runtime_state.pop(self._tool_call_id(inputs), {})
            if isinstance(tool_runtime_state, dict)
            else {}
        )
        if isinstance(extra, dict) and not tool_runtime_state:
            extra.pop(_BROWSER_TOOL_RUNTIME_STATE_KEY, None)
        started_at = call_state.get("started_at") if isinstance(call_state, dict) else None
        action_class = str(call_state.get("action_class") or "") if isinstance(call_state, dict) else ""
        elapsed_ms = int(max(0.0, (time.perf_counter() - started_at) * 1000)) if started_at else 0
        return action_class, elapsed_ms

    @staticmethod
    def _set_tool_message_outcome(
        inputs: Any,
        *,
        success: bool,
        executed: bool,
        state_changed: bool,
        denied: bool,
        effect: str | None = None,
    ) -> None:
        tool_msg = getattr(inputs, "tool_msg", None)
        if tool_msg is None:
            return
        metadata = dict(tool_msg.metadata) if isinstance(tool_msg.metadata, dict) else {}
        metadata.update(
            {
                "success": success,
                "executed": executed,
                "state_changed": state_changed,
                "denied": denied,
            }
        )
        if effect is not None:
            metadata["effect"] = effect
        tool_msg.metadata = metadata

    @staticmethod
    def _compact_large_observation_message(inputs: Any, kind: ToolKind, tool_name: str, result: Any) -> None:
        """Bound one model-visible raw observation after runtime evidence extraction."""

        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        observes = isinstance(kind.action, (Observe, RunScript))
        reads = kind.helper and kind.read_only and not kind.exempt_from_budget
        if not (observes or reads):
            return
        if len(content) <= _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            return

        audit = write_browser_agent_audit_artifact("large_browser_observation", result)
        payload = {
            "ok": bool(result.get("ok")) if isinstance(result, dict) else True,
            "observation": "bounded_browser_tool_result",
            "tool": str(tool_name or "").rsplit("_", 1)[-1],
            "truncated": True,
            "original_chars": len(content),
            "preview_head": content[:6_000],
            "preview_tail": content[-2_000:],
            "note": (
                "Runtime evidence and targets were recorded before compaction. "
                "Use browser_working_context and browser_state for subsequent actions."
            ),
        }
        if audit:
            payload["audit"] = audit
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            payload["preview_head"] = content[:4_000]
            payload["preview_tail"] = content[-1_000:]
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            payload["preview_head"] = content[:2_000]
            payload.pop("preview_tail", None)
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _BROWSER_OBSERVATION_MESSAGE_MAX_CHARS:
            payload.pop("preview_head", None)
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        tool_msg.content = rendered


    @staticmethod
    def _tool_call_id(inputs: Any) -> str:
        tool_call = getattr(inputs, "tool_call", None)
        call_id = str(getattr(tool_call, "id", "") or "").strip()
        return call_id or f"tool-context-{id(inputs)}"

    @classmethod
    def _denial_code(cls, message: str) -> str:
        normalized = str(message or "").lower()
        if "already" in normalized and "browser task" in normalized:
            return "browser_task_terminal"
        if "semantic" in normalized or "replan" in normalized:
            return "browser_replan_required"
        if "budget exhausted" in normalized:
            return "browser_phase_budget_exhausted"
        if "browser_progress" in normalized:
            return "browser_progress_is_text_protocol"
        if "screenshot input" in normalized:
            return "browser_image_input_unavailable"
        if "known url" in normalized:
            return "browser_direct_navigation_required"
        return "browser_action_denied"

    def _deny_tool_call(self, ctx: AgentCallbackContext, exc: Exception) -> None:
        inputs = getattr(ctx, "inputs", None)
        session = getattr(ctx, "session", None)
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        state = state if isinstance(state, dict) else {}
        status = str(state.get("status") or "in_progress").strip().lower()
        payload = {
            "ok": False,
            "status": "denied",
            "executed": False,
            "state_changed": False,
            "denied": True,
            "error": {
                "code": self._denial_code(str(exc)),
                "message": str(exc),
                "terminal": status in _BROWSER_TERMINAL_STATUSES,
                "replan_required": bool(state.get("replan_required")),
            },
            "runtime_state": {
                "task_status": status,
                "current_phase": state.get("current_phase"),
                "next_action_class": state.get("next_action_class"),
                "blockers": list(state.get("blockers") or [])[:10],
            },
        }
        target_match = re.search(r"\bt_g\d+_\d+\b", str(exc))
        if target_match is not None:
            payload["runtime_state"].update(
                self._runtime.target_recovery_state(target_match.group(0))
            )
        call_id = self._tool_call_id(inputs)
        inputs.tool_result = payload
        inputs.tool_msg = ToolMessage(
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            tool_call_id=call_id,
            metadata={
                "executed": False,
                "state_changed": False,
                "denied": True,
            },
        )
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra.setdefault(_BROWSER_SKIP_TOOL_CALLS_KEY, {})[call_id] = True
        self._mark_action_group_call_completed(
            ctx,
            {
                "executed": False,
                "state_changed": False,
                "denied": True,
                "new_evidence_fields": [],
            },
        )


    def _mark_action_group_call_completed(
        self,
        ctx: AgentCallbackContext,
        progress_delta: Dict[str, Any],
    ) -> None:
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        call_id = self._tool_call_id(getattr(ctx, "inputs", None))
        group_by_call = extra.get(_BROWSER_ACTION_GROUP_BY_CALL_KEY)
        group_id = group_by_call.pop(call_id, "") if isinstance(group_by_call, dict) else ""
        groups = extra.get(_BROWSER_ACTION_GROUP_RESULTS_KEY)
        group = groups.get(group_id) if isinstance(groups, dict) and group_id else None
        if isinstance(group, dict):
            completed = group.setdefault("completed", [])
            if call_id not in completed:
                completed.append(call_id)
            if progress_delta.get("executed") is False:
                denied = group.setdefault("denied", [])
                if call_id not in denied:
                    denied.append(call_id)
            elif progress_delta.get("executed") is True:
                executed = group.setdefault("executed", [])
                if call_id not in executed:
                    executed.append(call_id)
            if progress_delta.get("state_changed") is True:
                group["state_changed"] = True
            evidence_fields = group.setdefault("evidence_fields", [])
            for field_name in progress_delta.get("new_evidence_fields") or []:
                if field_name not in evidence_fields:
                    evidence_fields.append(field_name)
            if set(completed) >= set(group.get("expected") or []):
                group["ready"] = True
                session = getattr(ctx, "session", None)
                state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
                if isinstance(state, dict):
                    state["last_action_group"] = {
                        "action_group_id": group_id,
                        "call_count": len(group.get("expected") or []),
                        "executed_count": len(group.get("executed") or []),
                        "denied_count": len(group.get("denied") or []),
                        "state_changed": bool(group.get("state_changed")),
                        "evidence_fields": evidence_fields[:32],
                    }
                    session.update_state({_BROWSER_PHASE_STATE_KEY: state})
                groups.pop(group_id, None)
                if not groups:
                    extra.pop(_BROWSER_ACTION_GROUP_RESULTS_KEY, None)
        if isinstance(group_by_call, dict) and not group_by_call:
            extra.pop(_BROWSER_ACTION_GROUP_BY_CALL_KEY, None)

    def _attach_page_state(self, inputs: Any, kind: ToolKind, result: Any) -> None:
        if kind.helper:
            return
        observed = isinstance(kind.action, Observe)
        if observed and kind.action.include_screenshot:
            return
        if not observed and kind.effect is not Effect.NAVIGATE:
            return

        page_state = self._runtime.export_page_state()
        tool_msg = getattr(inputs, "tool_msg", None)
        content = getattr(tool_msg, "content", None)
        if tool_msg is None or not isinstance(content, str):
            return
        if observed:
            compact_observation = {
                "ok": True,
                "observation": "compact_page_state",
                "page_state": page_state,
                "audit": write_browser_agent_audit_artifact("direct_ax_observation", result),
            }
            tool_msg.content = json.dumps(
                compact_observation,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return
        marker = (
            f"\n<{_BROWSER_PAGE_STATE_TAG}>"
            f"{json.dumps(page_state, ensure_ascii=False, separators=(',', ':'))}"
            f"</{_BROWSER_PAGE_STATE_TAG}>"
        )
        if f"<{_BROWSER_PAGE_STATE_TAG}>" not in content:
            tool_msg.content = content.rstrip() + marker

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            session = getattr(ctx, "session", None)
            result = getattr(getattr(ctx, "inputs", None), "result", None)
            if session is None or not isinstance(result, dict):
                return

            session_id = session.get_session_id()
            self._hydrate_service_progress_from_session(session)
            output_text = str(result.get("output") or "")
            clean_output, progress_payload = self._extract_progress_payload(output_text)
            if clean_output != output_text:
                result["output"] = clean_output

            if progress_payload is not None:
                self._apply_worker_progress_to_task_state(session, progress_payload, clean_output)
                if self._finalize_terminal_invoke(session, result, clean_output):
                    return
                progress_state = self._load_progress_state(session)
                parsed_progress = self._build_progress_result(progress_state, clean_output)
                self._runtime.service.set_progress_state(session_id, progress_state)
                exported = progress_state.to_dict()
                if self._runtime.service.should_treat_as_completed(parsed_progress) and clean_output.strip():
                    result["result_type"] = "answer"
                    result["progress_state"] = exported
                    self._clear_progress_state(session)
                    return

                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error=str(parsed_progress.get("error") or "browser_task_incomplete"),
                    page_url=progress_state.last_page_url if progress_state is not None else "",
                    page_title=progress_state.last_page_title if progress_state is not None else "",
                    final=clean_output,
                    screenshot=progress_state.last_screenshot if progress_state is not None else None,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["result_type"] = "error"
                result["failure_summary"] = failure_summary
                result["progress_state"] = exported
                result["output"] = failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
                self._persist_service_progress_to_session(session)
                return

            if self._is_max_iteration_result(result):
                if self._terminalize_invoke_failure(session, "max_iterations_reached"):
                    if self._finalize_terminal_invoke(session, result, clean_output):
                        return
                progress_state = self._load_progress_state(session)
                self._runtime.service.set_progress_state(session_id, progress_state)
                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error="max_iterations_reached",
                    page_url=progress_state.last_page_url if progress_state is not None else "",
                    page_title=progress_state.last_page_title if progress_state is not None else "",
                    final=clean_output or output_text,
                    screenshot=progress_state.last_screenshot if progress_state is not None else None,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["failure_summary"] = failure_summary
                result["progress_state"] = progress_state.to_dict()
                result["output"] = failure_summary
                self._persist_service_progress_to_session(session)
                return

            if str(result.get("result_type", "")).lower() == "answer":
                task_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
                if not isinstance(task_state, dict) or not task_state:
                    self._clear_progress_state(session)
                    return
                self._apply_worker_progress_to_task_state(
                    session,
                    {"status": "completed"},
                    clean_output,
                )
                if self._finalize_terminal_invoke(session, result, clean_output):
                    return
                progress_state = self._load_progress_state(session)
                exported = progress_state.to_dict()
                parsed_progress = self._build_progress_result(progress_state, clean_output)
                if self._runtime.service.should_treat_as_completed(parsed_progress) and clean_output.strip():
                    result["progress_state"] = exported
                    self._clear_progress_state(session)
                    return
                failure_summary = self._runtime.service.build_failure_summary(
                    task=self._load_task_text(session),
                    error="empty_or_unverified_browser_result",
                    page_url=progress_state.last_page_url,
                    page_title=progress_state.last_page_title,
                    final=clean_output,
                    screenshot=progress_state.last_screenshot,
                    attempt=1,
                    progress_state=progress_state,
                )
                result["result_type"] = "error"
                result["failure_summary"] = failure_summary
                result["progress_state"] = exported
                result["output"] = failure_summary if not clean_output else f"{clean_output}\n\n{failure_summary}"
                self._persist_service_progress_to_session(session)
                return

            progress_state = self._load_progress_state(session)
            if str(result.get("result_type") or "").lower() == "error":
                self._terminalize_invoke_failure(session, "browser_execution_error")
            if self._finalize_terminal_invoke(session, result, clean_output):
                return
            exported = progress_state.to_dict() if not progress_state.is_empty() else None
            if exported:
                self._runtime.service.set_progress_state(session_id, progress_state)
                result["progress_state"] = exported
                self._persist_service_progress_to_session(session)

        finally:
            self._emit_status("after_invoke", ctx)
            extra = getattr(ctx, "extra", None)
            if isinstance(extra, dict):
                token = extra.pop(_BROWSER_LOG_CONTEXT_TOKEN_KEY, None)
                if token is not None:
                    reset_browser_agent_log_context(token)

    def _finalize_terminal_invoke(
        self,
        session: Any,
        result: Dict[str, Any],
        model_summary: str,
    ) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if not isinstance(state, dict):
            return False
        status = str(state.get("status") or "").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            return False
        output, payload = self._render_authoritative_terminal_output(state, model_summary)
        progress_state = self._load_progress_state(session)
        result["output"] = output
        result["result_type"] = "answer" if status == "completed" else "error"
        result["error"] = None if status == "completed" else "browser_task_incomplete"
        result["progress_state"] = progress_state.to_dict()
        result["authoritative_browser_result"] = payload
        if status == "completed":
            self._clear_progress_state(session)
        else:
            result["failure_summary"] = self._runtime.service.build_failure_summary(
                task=self._load_task_text(session),
                error="browser_task_incomplete",
                page_url=progress_state.last_page_url,
                page_title=progress_state.last_page_title,
                final="",
                screenshot=progress_state.last_screenshot,
                attempt=1,
                progress_state=progress_state,
            )
            self._persist_service_progress_to_session(session)
        return True

    def _normalize_target_args(self, tool_args: Any, fields: frozenset[str] = frozenset()) -> Any:
        """Rewrite ref aliases, resolve PageState target ids and drop the runtime's own keys the tool does not take."""
        original_is_json = isinstance(tool_args, str)
        if original_is_json:
            try:
                parsed = json.loads(tool_args)
            except ValueError:
                return tool_args
        elif isinstance(tool_args, dict):
            parsed = dict(tool_args)
        else:
            return tool_args
        if not isinstance(parsed, dict):
            return tool_args

        parsed, changed = self._normalize_target_payload(parsed)
        for key in _BROWSER_RUNTIME_ARG_KEYS - fields if fields else ():
            if key in parsed:
                parsed.pop(key)
                changed = True
        if not changed:
            return tool_args
        return json.dumps(parsed, ensure_ascii=False) if original_is_json else parsed

    def _normalize_target_payload(
        self,
        tool_args: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool]:
        parsed = dict(tool_args)
        changed = self._normalize_target_aliases(parsed)
        for key in _BROWSER_REF_TARGET_KEYS:
            value = str(parsed.get(key) or "").strip()
            if not value.startswith("t_g"):
                continue
            target = self._runtime.resolve_model_target_id(value)
            executable_target = str(target.ref or target.selector or "").strip()
            if not executable_target:
                # A card's primary link has no element to act on; the rail turns its click into a navigation.
                continue
            parsed[key] = executable_target
            element_key = {
                "target": "element",
                "startTarget": "startElement",
                "endTarget": "endElement",
            }.get(key)
            if element_key and not str(parsed.get(element_key) or "").strip():
                parsed[element_key] = str(target.text or target.kind or target.target_id)[:200]
            changed = True

        fields = parsed.get("fields")
        if isinstance(fields, list):
            normalized_fields = []
            for field in fields:
                if not isinstance(field, dict):
                    normalized_fields.append(field)
                    continue
                nested, nested_changed = self._normalize_target_payload(field)
                normalized_fields.append(nested)
                changed = changed or nested_changed
            parsed["fields"] = normalized_fields
        return parsed, changed

    @staticmethod
    def _normalize_target_aliases(parsed: Dict[str, Any]) -> bool:
        changed = False

        if "target_id" in parsed:
            parsed["target"] = parsed.pop("target_id")
            changed = True
        if "generation_id" in parsed:
            parsed.pop("generation_id", None)
            changed = True

        for alias, target_key in _BROWSER_REF_ALIAS_KEYS.items():
            if alias not in parsed:
                continue
            if target_key not in parsed:
                parsed[target_key] = parsed[alias]
            parsed.pop(alias, None)
            changed = True

        for key in _BROWSER_REF_TARGET_KEYS:
            value = parsed.get(key)
            if not isinstance(value, str):
                continue
            match = _BROWSER_EXPLICIT_REF_RE.fullmatch(value)
            if match is None:
                continue
            parsed[key] = match.group(1)
            changed = True
        return changed

    @staticmethod
    def _extract_progress_payload(output_text: str) -> tuple[str, Optional[Dict[str, Any]]]:
        text = str(output_text or "")
        match = _BROWSER_PROGRESS_TAG_RE.search(text)
        if match is None:
            return text, None
        payload_text = match.group(1).strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return text, None
        cleaned = _BROWSER_PROGRESS_TAG_RE.sub("", text, count=1).strip()
        return cleaned, payload if isinstance(payload, dict) else None

    @staticmethod
    def _build_progress_result(
        progress_state: BrowserTaskProgressState,
        clean_output: str,
    ) -> Dict[str, Any]:
        """Build completion output from runtime-owned progress only."""

        status = str(progress_state.status or "").strip().lower() or "partial"
        progress = progress_state.to_dict()
        return {
            "ok": status == "completed",
            "status": status,
            "progress": progress,
            "final": clean_output,
            "error": None if status == "completed" else "browser_task_incomplete",
        }

    @classmethod
    def _apply_worker_progress_to_task_state(
        cls,
        session: Any,
        payload: Dict[str, Any],
        final: str,
    ) -> None:
        """Validate a worker completion request against authoritative runtime state."""

        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return
        reported_status = str(payload.get("status") or "partial").strip().lower()
        state["worker_reported_status"] = reported_status
        reported_blockers = payload.get("blockers") or payload.get("missing_requirements") or []
        if isinstance(reported_blockers, str):
            reported_blockers = [reported_blockers]
        state["worker_reported_blockers"] = [str(item)[:300] for item in reported_blockers if str(item).strip()][:10]
        next_action = payload.get("next_action") or payload.get("next_step")
        if next_action:
            state["worker_reported_next_action"] = str(next_action)[:200]
        if final:
            state["last_worker_final"] = str(final)[:8_000]

        current_status = str(state.get("status") or "in_progress").strip().lower()
        if current_status in _BROWSER_TERMINAL_STATUSES:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return

        runtime_blockers = [str(item) for item in state.get("blockers") or [] if str(item).strip()]
        advisory_fields = cls._advisory_missing_fields(state) if final.strip() else set()
        missing_fields = [
            field for field in cls._missing_completion_requirements(state) if field not in advisory_fields
        ]
        unavailable_slots = cls._unavailable_evidence_slots(state)
        phases = state.get("phases") if isinstance(state.get("phases"), dict) else {}
        completed_phase_count = sum(
            1 for details in phases.values() if isinstance(details, dict) and details.get("status") == "completed"
        )
        evidence_available = cls._has_task_evidence(state)
        runtime_ready = bool(completed_phase_count or evidence_available)
        if state.get("replan_required"):
            runtime_blockers = list(dict.fromkeys([*runtime_blockers, "semantic_replan_required"]))

        completion_requirements_met = (
            bool(final.strip() or evidence_available)
            and not runtime_blockers
            and not missing_fields
            and not unavailable_slots
            and runtime_ready
        )
        if reported_status == "completed" and completion_requirements_met:
            state["status"] = "completed"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = "runtime_completion_validated"
        elif reported_status in _BROWSER_TERMINAL_STATUSES:
            state["status"] = "blocked" if runtime_blockers else "partial"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = (
                "runtime_blocked" if runtime_blockers else "runtime_completion_requirements_missing"
            )
            if runtime_blockers:
                state["blockers"] = runtime_blockers[:10]
            elif missing_fields:
                state["blockers"] = [f"missing_required_field:{field}" for field in missing_fields[:10]]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})

    @staticmethod
    def _is_max_iteration_result(result: Dict[str, Any]) -> bool:
        output = str(result.get("output") or "").strip()
        result_type = str(result.get("result_type") or "").strip().lower()
        return result_type == "error" and MAX_ITERATION_MESSAGE.lower() in output.lower()

    @staticmethod
    def _load_task_text(session: Any) -> str:
        if session is None:
            return ""
        return str(session.get_state(_BROWSER_PROGRESS_TASK_KEY) or "").strip()

    @staticmethod
    def _load_progress_state(session: Any) -> BrowserTaskProgressState:
        if session is None:
            return BrowserTaskProgressState()
        task_state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(task_state, dict) and task_state:
            return BrowserTaskProgressState.from_task_state(task_state)
        return BrowserTaskProgressState.from_dict(session.get_state(_BROWSER_PROGRESS_STATE_KEY))

    def _hydrate_service_progress_from_session(self, session: Any) -> BrowserTaskProgressState:
        session_id = session.get_session_id()
        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            self._runtime.service.clear_progress_state(session_id)
            return progress_state
        self._runtime.service.set_progress_state(session_id, progress_state)
        return progress_state

    def _persist_service_progress_to_session(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        progress_state = self._load_progress_state(session)
        if progress_state.is_empty():
            self._runtime.service.clear_progress_state(session_id)
            exported: Dict[str, Any] = {}
        else:
            self._runtime.service.set_progress_state(session_id, progress_state)
            exported = progress_state.to_dict()
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: exported,
            }
        )

    def _ensure_task_state(self, session: Any, task: str, *, resume: bool = False) -> Dict[str, Any]:
        normalized_task = str(task or "").strip()
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if isinstance(state, dict) and str(state.get("task") or "").strip() == normalized_task:
            self._runtime.set_task_requested_fields(state.get("required_fields") or [])
            return state
        if isinstance(state, dict) and resume:
            resumed = self._resume_task_state(session, state, normalized_task)
            self._runtime.set_task_requested_fields(resumed.get("required_fields") or [])
            return resumed
        self._runtime.reset_semantic_task()
        self._runtime.service.clear_progress_state(session.get_session_id())
        state = self._build_phase_state(normalized_task)
        self._runtime.set_task_requested_fields(state.get("required_fields") or [])
        session.update_state(
            {
                _BROWSER_PROGRESS_TASK_KEY: normalized_task,
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PHASE_STATE_KEY: state,
            }
        )
        return state

    @staticmethod
    def _browser_run_context(ctx: AgentCallbackContext) -> Dict[str, Any]:
        context = getattr(getattr(ctx, "inputs", None), "run_context", None)
        if context is None:
            context = (getattr(ctx, "extra", None) or {}).get("run_context")
        if isinstance(context, dict):
            return context.get("extra", context)
        extra = getattr(context, "extra", None)
        return extra if isinstance(extra, dict) else {}

    @staticmethod
    def _bind_shared_task_deadline(
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> float:
        if session is None or not isinstance(state, dict) or not state:
            return 0.0
        extra = getattr(ctx, "extra", None)
        shared_context = BrowserRuntimeRail._browser_run_context(ctx)
        context_budget_s = float(shared_context.get("browser_query_budget_s") or 0.0)
        budget_s = float(
            state.get("deadline_budget_s")
            or context_budget_s
            or (
                _BROWSER_COMPLEX_TASK_DEADLINE_S
                if str(state.get("task_type") or "") == "complex"
                else _BROWSER_SIMPLE_TASK_DEADLINE_S
            )
        )
        now = time.time()
        context_started_at = float(shared_context.get("browser_query_started_at") or 0.0)
        context_deadline_at = float(shared_context.get("browser_query_deadline_at") or 0.0)
        started_at = float(state.get("deadline_started_at") or context_started_at or now)
        deadline_at = float(state.get("deadline_at") or context_deadline_at or started_at + budget_s)
        if context_deadline_at:
            deadline_at = min(deadline_at, context_deadline_at)
        query_id = str(shared_context.get("browser_query_id") or state.get("query_id") or "").strip()
        if query_id:
            state["query_id"] = query_id[:128]
        state["deadline_budget_s"] = budget_s
        state["deadline_started_at"] = started_at
        state["deadline_at"] = deadline_at
        remaining_s = max(0.0, deadline_at - now)
        state["deadline_remaining_s"] = round(remaining_s, 3)
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        if isinstance(extra, dict):
            invocation_deadline = float(extra.get(_BROWSER_INVOCATION_DEADLINE_KEY) or 0.0)
            if not invocation_deadline:
                invocation_deadline = time.monotonic() + remaining_s
                extra[_BROWSER_INVOCATION_DEADLINE_KEY] = invocation_deadline
            effective_remaining_s = min(
                remaining_s,
                max(0.0, invocation_deadline - time.monotonic()),
            )
            extra[_BROWSER_TASK_DEADLINE_KEY] = time.monotonic() + effective_remaining_s
            state["invocation_remaining_s"] = round(effective_remaining_s, 3)
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return effective_remaining_s
        return remaining_s

    @classmethod
    def _finish_if_task_deadline_exhausted(
        cls,
        ctx: AgentCallbackContext,
        session: Any,
        state: Dict[str, Any],
    ) -> bool:
        if session is None or not isinstance(state, dict) or not state:
            return False
        deadline_at = float(state.get("deadline_at") or 0.0)
        shared_exhausted = bool(deadline_at and time.time() >= deadline_at)
        extra = getattr(ctx, "extra", None)
        invocation_deadline = (
            float(extra.get(_BROWSER_INVOCATION_DEADLINE_KEY) or 0.0)
            if isinstance(extra, dict)
            else 0.0
        )
        invocation_exhausted = bool(invocation_deadline and time.monotonic() >= invocation_deadline)
        if not shared_exhausted and not invocation_exhausted:
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            missing = cls._missing_completion_requirements(state)
            evidence_available = cls._has_task_evidence(state)
            if evidence_available and not missing and not state.get("blockers"):
                state["status"] = "completed"
                state["terminal_reason"] = "task_deadline_completed_from_evidence"
            else:
                state["status"] = "partial" if evidence_available else "blocked"
                blockers = list(state.get("blockers") or [])
                blocker = "task_deadline_exhausted" if shared_exhausted else "task_invocation_slice_exhausted"
                if blocker not in blockers:
                    blockers.append(blocker)
                state["blockers"] = blockers[:10]
                state["terminal_reason"] = blocker
            state["next_action_class"] = "finish"
            state["deadline_remaining_s"] = (
                0.0 if shared_exhausted else round(max(0.0, deadline_at - time.time()), 3)
            )
            state["invocation_remaining_s"] = 0.0
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        ctx.request_force_finish(cls._structured_terminal_result(state))
        return True

    @staticmethod
    def _terminalize_invoke_failure(session: Any, reason: str) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY) if session is not None else None
        if not isinstance(state, dict) or not state:
            return False
        status = str(state.get("status") or "in_progress").strip().lower()
        if status not in _BROWSER_TERMINAL_STATUSES:
            evidence_available = BrowserRuntimeRail._has_task_evidence(state)
            state["status"] = "partial" if evidence_available else "blocked"
            state["next_action_class"] = "finish"
            state["terminal_reason"] = reason
            blockers = list(state.get("blockers") or [])
            if reason not in blockers:
                blockers.append(reason)
            state["blockers"] = blockers[:10]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return True

    def _resume_task_state(
        self,
        session: Any,
        state: Dict[str, Any],
        resume_instruction: str,
    ) -> Dict[str, Any]:
        if str(state.get("resume_instruction") or "").strip() == resume_instruction:
            return state
        missing = self._missing_completion_requirements(state)
        if not self._terminal_result_retryable(state, missing):
            return state

        state["resume_count"] = int(state.get("resume_count") or 0) + 1
        state["resume_instruction"] = resume_instruction[:2_000]
        state["status"] = "in_progress"
        state["next_action_class"] = (
            "collect_missing_evidence" if missing else "materially_different_strategy"
        )
        state["terminal_reason"] = ""
        state.pop(_BROWSER_TERMINAL_SYNTHESIS_KEY, None)
        state["replan_required"] = False
        state["replan_trial_pending"] = False
        state["replan_count"] = 0
        state["replan_denial_count"] = 0
        state["blocked_strategy"] = ""
        state["trial_strategy"] = ""
        state["model_protocol_retry_count"] = 0
        recoverable_prefixes = (
            "semantic_",
            "missing_required_field:",
            "missing_evidence_slot:",
            "model_provider_unavailable",
            "model_tool_protocol_error",
            "task_invocation_slice_exhausted",
        )
        state["blockers"] = [
            blocker
            for blocker in state.get("blockers") or []
            if not str(blocker).strip().lower().startswith(recoverable_prefixes)
        ][:10]
        known_urls = list(state.get("known_urls") or [])
        for url in re.findall(r"https?://[^\s<>\"]+", resume_instruction):
            if url not in known_urls:
                known_urls.append(url)
        state["known_urls"] = known_urls[:4]
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        self._runtime.reset_semantic_task()
        return state

    @staticmethod
    def _build_phase_state(task: str) -> Dict[str, Any]:
        normalized = str(task or "").lower()
        complex_tokens = (
            "form",
            "filter",
            "sort",
            "compare",
            "book",
            "checkout",
            "register",
            "login",
            "purchase",
            "choose",
            "select",
            "fill",
            "submit",
            "switch",
            "cart",
            "basket",
            "add to cart",
            "\u8868\u5355",
            "\u7b5b\u9009",
            "\u6392\u5e8f",
            "\u6bd4\u8f83",
            "\u5bf9\u6bd4",
            "\u9009\u62e9",
            "\u586b\u5199",
            "\u63d0\u4ea4",
            "\u5207\u6362",
            "\u8bbe\u7f6e",
            "\u65e5\u671f",
            "\u9884\u8ba2",
            "\u767b\u5f55",
            "\u6ce8\u518c",
            "\u7ed3\u8d26",
            "\u8d2d\u4e70",
            "\u8d2d\u7269\u8f66",
            "\u52a0\u8d2d",
            "\u52a0\u5165\u8d2d\u7269\u8f66",
        )
        task_type = "complex" if any(token in normalized for token in complex_tokens) else "simple"
        phase_names = tuple(_BROWSER_PHASE_DEFINITIONS) if task_type == "complex" else ("navigation", "extraction")
        phases = {
            name: {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[name]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[name]["completion_condition"],
                "blocked_signature": "",
                "visited_price_intervals": [],
            }
            for name in phase_names
        }
        required_slots = BrowserRuntimeRail._infer_required_evidence_slots(task)
        required_fields = list(
            dict.fromkeys(
                str(slot.get("field") or "")
                for slot in required_slots
                if str(slot.get("field") or "").strip()
            )
        )
        return {
            "task_id": hashlib.sha256(str(task).encode("utf-8")).hexdigest()[:16],
            "task": task,
            "goal": task,
            "requirements_source": "inferred",
            "task_type": task_type,
            "status": "in_progress",
            "phases": phases,
            "current_phase": phase_names[0],
            "known_urls": re.findall(r"https?://[^\s<>\"]+", task)[:4],
            "constraints": [],
            "required_fields": required_fields,
            "required_evidence_slots": required_slots,
            "requested_result_count": BrowserRuntimeRail._infer_requested_result_count(task),
            "observed_result_count": 0,
            "replan_count": 0,
            "replan_denial_count": 0,
            "resume_count": 0,
            "direct_navigation_guidance_count": 0,
            "read_only_recovery_counts": {},
            "model_protocol_retry_count": 0,
            "replan_required": False,
            "replan_trial_pending": False,
            "blocked_strategy": "",
            "failed_strategies": [],
            "trial_strategy": "",
            "last_strategy_fingerprint": "",
            "next_action_class": "navigation",
            "semantic_revision": 0,
            "structured_evidence": [],
            "evidence_slots": [],
            "field_coverage": [],
            "blockers": [],
            "recent_actions": [],
            "action_sequence": 0,
            "last_page": {},
        }

    @classmethod
    def _infer_required_fields(cls, task: str) -> list[str]:
        normalized = str(task or "").lower()
        request_scopes = _BROWSER_OUTPUT_REQUEST_CUE_RE.findall(normalized)
        output_scope = " ".join(request_scopes).strip() or normalized
        inferred = [
            field
            for field, aliases in _BROWSER_FIELD_ALIASES.items()
            if any(cls._contains_field_alias(output_scope, alias) for alias in (field, *aliases))
        ]
        if "product_rating" in inferred or "shop_rating" in inferred:
            inferred = [field_name for field_name in inferred if field_name != "rating"]
        if "product_rating" in inferred and not any(
            cls._contains_field_alias(normalized, alias) for alias in ("title", "name", "标题", "名称", "电影", "结果")
        ):
            inferred = [field_name for field_name in inferred if field_name != "title"]
        if "shop_rating" in inferred:
            without_shop_rating = re.sub(
                r"shop\s+rating|seller\s+rating|store\s+rating|店铺评分|卖家评分",
                "",
                normalized,
                flags=re.IGNORECASE,
            )
            if not any(
                cls._contains_field_alias(without_shop_rating, alias) for alias in _BROWSER_FIELD_ALIASES["shop"]
            ):
                inferred = [field_name for field_name in inferred if field_name != "shop"]
        return inferred

    @classmethod
    def _infer_required_evidence_slots(cls, task: str) -> list[Dict[str, str]]:
        """Create one provenance-bearing evidence slot per requested field."""
        normalized = str(task or "").lower()
        required_fields = cls._infer_required_fields(task)
        entity = cls._infer_evidence_entity(normalized)
        comprehensive_requested = any(
            token in normalized for token in ("comprehensive", "relevance", "综合", "默认排序")
        )
        latest_requested = any(token in normalized for token in ("latest", "newest", "最新", "最近发布"))
        split_title_variants = bool(
            "title" in required_fields and comprehensive_requested and latest_requested
        )
        slots = [
            {"entity": entity, "variant": "default", "field": field_name}
            for field_name in required_fields
            if not (field_name == "title" and split_title_variants)
        ]
        if split_title_variants:
            slots.extend(
                [
                    {"entity": entity, "variant": "comprehensive", "field": "title"},
                    {"entity": entity, "variant": "latest", "field": "title"},
                ]
            )
        return slots

    @staticmethod
    def _infer_evidence_entity(normalized_task: str) -> str:
        profile_entity = infer_profile_evidence_entity(normalized_task)
        if profile_entity:
            return profile_entity
        entities = (
            (("商品",), "product"),
            (("回答",), "article_or_answer"),
            (("article", "文章"), "article"),
            (("电影",), "movie"),
            (("weather", "天气", "气温"), "weather"),
            (("hotel", "酒店"), "hotel"),
            (("video", "视频"), "video"),
        )
        for aliases, entity in entities:
            if any(alias in normalized_task for alias in aliases):
                return entity
        return "task_result"

    @staticmethod
    def _contains_field_alias(text: str, alias: str) -> bool:
        normalized_alias = str(alias or "").strip().lower()
        if not normalized_alias:
            return False
        if any("\u4e00" <= character <= "\u9fff" for character in normalized_alias):
            return normalized_alias in text
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized_alias)}(?![a-z0-9_])", text) is not None

    _NUMBER_WORDS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }

    @classmethod
    def _infer_requested_result_count(cls, task: str) -> int:
        normalized = str(task or "").lower()
        patterns = (
            r"(?:top|first)\s*(\d{1,2})(?:\s*(?:results?|items?|articles?|links?))?",
            r"(?:前|最前)\s*(\d{1,2})\s*(?:条|个|篇|项|部|名|则)?",
            r"(\d{1,2})\s*(?:条|个|篇|项|部)\s*(?:结果|记录|文章|商品|视频|链接)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match is not None:
                return min(20, max(1, int(match.group(1))))
        number_words = "|".join(cls._NUMBER_WORDS)
        verb_match = re.search(
            rf"\b(?:find|discover|list|show|get|recommend|identify|locate)\s+(\d{{1,2}}|{number_words})\b",
            normalized,
        )
        if verb_match is not None:
            token = verb_match.group(1)
            count = int(token) if token.isdigit() else cls._NUMBER_WORDS[token]
            return min(20, max(1, count))
        return 0

    @staticmethod
    def _snake_case_field_name(value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
        text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", text)
        return text.strip("_").lower()

    @classmethod
    def _canonical_field_name(cls, value: Any) -> str:
        snake_name = cls._snake_case_field_name(value)
        normalized = " ".join(snake_name.replace("_", " ").split())
        if not normalized:
            return ""
        for field_name, aliases in _BROWSER_FIELD_ALIASES.items():
            candidates = (field_name.replace("_", " "), *aliases)
            if any(normalized == candidate.lower() for candidate in candidates):
                return field_name
        return str(value or "").strip()[:80]

    @classmethod
    def _canonical_evaluate_field_name(cls, value: Any) -> str:
        snake_name = cls._snake_case_field_name(value)
        if not snake_name:
            return ""
        explicit = _BROWSER_EVALUATE_FIELD_ALIASES.get(snake_name)
        if explicit:
            return explicit
        canonical = cls._canonical_field_name(snake_name)
        if canonical != snake_name:
            return canonical
        for suffix in ("_raw", "_text", "_value"):
            if not snake_name.endswith(suffix):
                continue
            base_name = snake_name[: -len(suffix)]
            canonical_base = cls._canonical_field_name(base_name)
            if canonical_base in _BROWSER_FIELD_ALIASES:
                return canonical_base
        return snake_name[:80]

    @classmethod
    def _field_mentioned(cls, field_name: str, text: str) -> bool:
        aliases = _BROWSER_FIELD_ALIASES.get(str(field_name), (str(field_name),))
        return any(cls._contains_field_alias(text, alias) for alias in aliases)

    def _sync_semantic_progress(self, session: Any) -> None:
        if session is None:
            return
        progress = self._runtime.semantic_progress
        if BrowserWorkingContextStore.sync_semantic_progress(session, progress):
            self._runtime.acknowledge_semantic_replan()

    @staticmethod
    def _coerce_tool_args(tool_args: Any) -> Dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _classify_tool_phase(
        cls,
        kind: ToolKind,
        tool_args: Any,
        state: Dict[str, Any],
    ) -> str:
        args = cls._coerce_tool_args(tool_args)
        serialized = json.dumps(args, ensure_ascii=False).lower()
        if kind.effect is Effect.NAVIGATE:
            return "navigation"
        filter_terms = (
            "filter",
            "sort",
            "price",
            "rating",
            "category",
            "\u7b5b\u9009",
            "\u6392\u5e8f",
            "\u4ef7\u683c",
            "\u8bc4\u5206",
            "\u5206\u7c7b",
        )
        filter_terms_present = _contains_any_token(serialized, filter_terms)
        script_tool = isinstance(kind.action, RunScript)
        filter_intent = bool(
            filter_terms_present
            and (
                not script_tool
                or cls._operation_intent(kind, args) == "script_mutation"
            )
        )
        steps = args.get("steps")
        if kind.helper and isinstance(steps, list):
            operations = {str(step.get("op") or "").lower() for step in steps if isinstance(step, dict)}
            extraction_operations = {"extract_text", "extract_value"}
            mutating_operations = _BATCH_MUTATING_TARGET_OPS | {"press", "navigate", "navigate_back"}
            if operations & extraction_operations and not operations & mutating_operations:
                return "extraction"
            if filter_intent:
                return "filtering"
            if operations & {
                "fill",
                "type",
                "autocomplete",
                "select_option",
                "select_visible_text",
                "set_checked",
            }:
                return "form"
            if operations & {"extract_text", "extract_value"}:
                return "extraction"
        if filter_intent:
            return "filtering"
        if cls._command_name(kind) in _FORM_COMMAND_NAMES:
            return "form"
        if script_tool or (kind.read_only and not kind.exempt_from_budget):
            return "extraction"
        current = str(state.get("current_phase") or "navigation")
        return current if current in _BROWSER_PHASE_DEFINITIONS else "navigation"

    @staticmethod
    def _command_name(kind: ToolKind) -> str:
        return kind.command.__name__ if kind.command is not None else ""

    @classmethod
    def _phase_action_signature(
        cls,
        tool_name: str,
        tool_args: Any,
    ) -> str:
        args = cls._coerce_tool_args(tool_args)
        semantic_keys = (
            "url",
            "href",
            "name",
            "text",
            "query",
            "value",
            "values",
            "text_value",
            "label_value",
            "option_value",
            "option_label",
            "option_text",
            "choose_text",
            "checked",
            "key",
            "field",
            "action",
            "element",
        )
        actions: list[Dict[str, Any]] = []
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    compact = _copy_non_none_values(step, semantic_keys)
                    compact["op"] = str(step.get("op") or "").strip().lower()
                    actions.append(compact)
        else:
            compact = _copy_non_none_values(args, semantic_keys)
            normalized_name = str(tool_name or "").strip().lower()
            operation = normalized_name.rsplit("browser_", 1)[-1]
            compact["op"] = operation
            actions.append(compact)
        return json.dumps(
            actions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )[:600]

    @classmethod
    def _strategy_fingerprint(
        cls,
        state: Dict[str, Any],
        kind: ToolKind,
        tool_args: Any,
        action_class: str,
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Identify a material strategy without selector or generation noise."""

        args = cls._coerce_tool_args(tool_args)
        fields = cls._requested_strategy_fields(state, args)
        intent = cls._operation_intent(kind, args)
        descriptor = {
            "action_class": action_class,
            "page": cls._normalize_navigation_url(cls._current_page_url(state, current_page_state)),
            "target": cls._strategy_target(args, current_page_state),
            "fields": fields,
            "intent": intent,
        }
        serialized = json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        field_label = ",".join(fields[:4]) or "none"
        return (f"{action_class}:{intent}:{field_label}:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]}")[
            :240
        ]

    @staticmethod
    def _current_page_url(
        state: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]],
    ) -> str:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        return str(page_state.get("url") or last_page.get("url") or "")

    @classmethod
    def _strategy_target(
        cls,
        args: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        url = str(args.get("url") or args.get("href") or "").strip()
        if url:
            return cls._normalize_navigation_url(url)
        target_id = str(args.get("target_id") or "").strip()
        if target_id:
            return cls._target_semantic_identity(target_id, current_page_state)
        for key in ("name", "label", "placeholder", "text", "element"):
            value = " ".join(str(args.get(key) or "").split()).lower()
            if value:
                return f"semantic:{value[:120]}"
        steps = args.get("steps")
        if isinstance(steps, list):
            labels = []
            for step in steps[:12]:
                if not isinstance(step, dict):
                    continue
                step_target_id = str(step.get("target_id") or "").strip()
                if step_target_id:
                    labels.append(cls._target_semantic_identity(step_target_id, current_page_state))
                    continue
                label = next(
                    (
                        " ".join(str(step.get(key) or "").split()).lower()
                        for key in ("name", "label", "placeholder", "text", "element")
                        if str(step.get(key) or "").strip()
                    ),
                    "",
                )
                labels.append(label[:80] or "page_target")
            if labels:
                return "batch:" + "|".join(labels)
        return (
            "page_target" if _has_non_empty_mapping_value(args, ("target_id", "target", "ref", "selector")) else "page"
        )

    @staticmethod
    def _target_semantic_identity(
        target_id: str,
        current_page_state: Optional[Dict[str, Any]],
    ) -> str:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}

        def find_target(value: Any) -> Optional[Dict[str, Any]]:
            if isinstance(value, dict):
                if str(value.get("target_id") or "") == target_id:
                    return value
                for nested in value.values():
                    match = find_target(nested)
                    if match is not None:
                        return match
            elif isinstance(value, list):
                for nested in value:
                    match = find_target(nested)
                    if match is not None:
                        return match
            return None

        target = find_target(page_state)
        if target is None:
            return "registered_target"
        identity = {
            key: " ".join(str(target.get(key) or "").split()).lower()[:160]
            for key in ("href", "field", "role", "kind", "region", "text")
            if str(target.get(key) or "").strip()
        }
        return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:400]

    @classmethod
    def _requested_strategy_fields(cls, state: Dict[str, Any], args: Dict[str, Any]) -> list[str]:
        fields: set[str] = set()
        for value in (args.get("field"), args.get("fields")):
            values = value if isinstance(value, list) else [value]
            for item in values:
                canonical = cls._canonical_field_name(item)
                if canonical:
                    fields.add(canonical)
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                canonical = cls._canonical_field_name(step.get("field"))
                if canonical:
                    fields.add(canonical)
        serialized = json.dumps(args, ensure_ascii=False, default=str).lower()
        for field_name in state.get("required_fields") or []:
            if cls._field_mentioned(str(field_name), serialized):
                fields.add(str(field_name))
        return sorted(fields)

    @classmethod
    def _operation_intent(cls, kind: ToolKind, args: Dict[str, Any]) -> str:
        steps = args.get("steps")
        if isinstance(steps, list):
            operations = sorted(
                {
                    str(step.get("op") or "").strip().lower()
                    for step in steps
                    if isinstance(step, dict) and str(step.get("op") or "").strip()
                }
            )
            return "+".join(operations)[:120] or "batch"
        if isinstance(kind.action, RunScript):
            expression = str(
                args.get("function") or args.get("expression") or args.get("script") or args.get("code") or ""
            ).lower()
            if re.search(r"\.click\s*\(|dispatchEvent|\.value\s*=|setAttribute\s*\(", expression):
                return "script_mutation"
            if re.search(r"textContent|innerText|return|querySelector|document\.title", expression):
                return "script_extraction"
            return "script_inspection"
        return cls._command_name(kind).lower() or "other"

    @staticmethod
    def _price_interval_signature(kind: ToolKind, tool_args: Any) -> str:
        """Delegate cross-site filter normalization to semantic-state logic; a script carries no filter."""
        if isinstance(kind.action, RunScript):
            return ""
        return price_interval_signature("", tool_args)

    @classmethod
    def _classify_action_class(cls, kind: ToolKind, tool_name: str, tool_args: Any, state: Dict[str, Any]) -> str:
        name = str(tool_name or "").strip().lower()
        args = cls._coerce_tool_args(tool_args)
        serialized = json.dumps(args, ensure_ascii=False).lower()
        if kind.effect is Effect.NAVIGATE:
            return "navigation"
        if isinstance(kind.action, Scroll):
            return "viewport_exploration"
        if isinstance(kind.action, RunScript):
            return "script_exploration"
        if "probe_cards" in name:
            return "structured_extraction"
        if isinstance(kind.action, Observe) or (kind.helper and kind.read_only and not kind.exempt_from_budget):
            return "target_discovery"
        phase = cls._classify_tool_phase(kind, tool_args, state)
        if phase == "extraction":
            return "structured_extraction"
        if phase in {"form", "filtering"}:
            return phase
        if isinstance(kind.action, (Click, Hover, Drag)) or cls._command_name(kind) == "Drop":
            return "interaction"
        if any(token in serialized for token in ("filter", "sort", "price", "rating", "筛选", "排序", "价格")):
            return "filtering"
        return "other"

    @classmethod
    def _consume_phase_budget(
        cls,
        session: Any,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        *,
        current_page_state: Optional[Dict[str, Any]] = None,
        replan_group_admitted: bool = False,
    ) -> str:
        if session is None:
            return "other"
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return "other"
        cls._reject_terminal_state(state)
        if kind.exempt_from_budget:
            return cls._classify_action_class(kind, tool_name, tool_args, state)
        phases = state.setdefault("phases", {})
        phase = cls._classify_tool_phase(kind, tool_args, state)
        details = cls._phase_details(phases, phase)
        signature = cls._phase_action_signature(tool_name, tool_args)
        action_class = cls._classify_action_class(kind, tool_name, tool_args, state)
        strategy_fingerprint = cls._strategy_fingerprint(
            state,
            kind,
            tool_args,
            action_class,
            current_page_state,
        )

        attempts = int(details.get("attempts") or 0)
        budget = max(1, int(details.get("budget") or 1))
        if attempts >= budget:
            details["status"] = "replan_required"
            details["blocked_signature"] = signature
            state["current_phase"] = phase
            state["status"] = "partial"
            state["blockers"] = [f"{phase}_phase_budget_exhausted"]
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                f"Browser {phase} phase budget exhausted ({attempts}/{budget}). "
                "Finish with available evidence or return partial/blocked."
            )

        cls._reject_revisited_price_interval(details, phase, kind, tool_args)

        if cls._direct_navigation_is_required(state, phases, phase, current_page_state):
            state["direct_navigation_guidance_count"] = int(state.get("direct_navigation_guidance_count") or 0) + 1
            recommended_url = str((state.get("known_urls") or [""])[0])
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise ValueError(
                "The task already contains a known URL. Navigate to it directly before selector exploration: "
                f"{recommended_url}"
            )

        try:
            if not replan_group_admitted:
                cls._consume_replan_gate(
                    state,
                    kind,
                    tool_args,
                    action_class,
                    strategy_fingerprint,
                )
        except ValueError:
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            raise

        details["attempts"] = attempts + 1
        details["status"] = "in_progress"
        details["last_signature"] = signature
        details["last_semantic_signature"] = signature
        state["current_phase"] = phase
        state["last_action_class"] = action_class
        state["last_strategy_fingerprint"] = strategy_fingerprint
        state["next_action_class"] = ""
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return action_class

    @classmethod
    def _direct_navigation_is_required(
        cls,
        state: Dict[str, Any],
        phases: Dict[str, Any],
        phase: str,
        current_page_state: Optional[Dict[str, Any]],
    ) -> bool:
        if (
            not state.get("known_urls")
            or phase == "navigation"
            or int(state.get("direct_navigation_guidance_count") or 0) >= 1
        ):
            return False
        total_attempts = sum(int(item.get("attempts") or 0) for item in phases.values() if isinstance(item, dict))
        return total_attempts == 0 and not cls._known_url_navigation_satisfied(
            state,
            current_page_state,
        )

    @classmethod
    def _known_url_navigation_satisfied(
        cls,
        state: Dict[str, Any],
        current_page_state: Optional[Dict[str, Any]],
    ) -> bool:
        page_state = current_page_state if isinstance(current_page_state, dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        current_url = str(page_state.get("url") or last_page.get("url") or "").strip()
        if not current_url:
            return False
        current_normalized = cls._normalize_navigation_url(current_url)
        current_document = cls._normalize_navigation_url(current_url, include_query=False)
        try:
            current_parts = urlsplit(current_url)
        except ValueError:
            current_parts = None
        for known_url in state.get("known_urls") or []:
            known_normalized = cls._normalize_navigation_url(known_url)
            if known_normalized and known_normalized == current_normalized:
                return True
            known_document = cls._normalize_navigation_url(known_url, include_query=False)
            if known_document and known_document == current_document:
                return True
            try:
                known_parts = urlsplit(str(known_url))
            except ValueError:
                known_parts = None
            if current_parts is None or known_parts is None:
                continue
            if current_parts.netloc.lower() != known_parts.netloc.lower():
                continue
            known_path = (known_parts.path or "/").rstrip("/") or "/"
            current_path = (current_parts.path or "/").rstrip("/") or "/"
            if known_path == "/" and (current_path != "/" or bool(current_parts.query)):
                return True
            if known_path != "/" and current_path.startswith(f"{known_path}/"):
                return True
        return False

    @staticmethod
    def _normalize_navigation_url(value: Any, *, include_query: bool = True) -> str:
        raw = str(value or "").strip().rstrip(".,;，。；)")
        if not raw:
            return ""
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw.lower()
        if not parsed.netloc:
            return raw.lower().rstrip("/")
        path = unquote(parsed.path or "/").rstrip("/") or "/"
        query = ""
        if include_query:
            query_items = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
            ]
            query = urlencode(sorted(query_items))
        normalized = f"{parsed.netloc.lower()}{path}"
        return f"{normalized}?{query}" if query else normalized

    @staticmethod
    def _reject_terminal_state(state: Dict[str, Any]) -> None:
        status = str(state.get("status") or "in_progress").strip().lower()
        if status in _BROWSER_TERMINAL_STATUSES:
            raise ValueError(
                f"Browser task is already {status}; do not issue another browser action. "
                "Finish with the runtime evidence and blockers."
            )

    @staticmethod
    def _phase_details(phases: Dict[str, Any], phase: str) -> Dict[str, Any]:
        return phases.setdefault(
            phase,
            {
                "status": "pending",
                "attempts": 0,
                "budget": _BROWSER_PHASE_DEFINITIONS[phase]["budget"],
                "completion_condition": _BROWSER_PHASE_DEFINITIONS[phase]["completion_condition"],
                "blocked_signature": "",
                "visited_price_intervals": [],
            },
        )

    @classmethod
    def _consume_replan_gate(
        cls,
        state: Dict[str, Any],
        kind: ToolKind,
        tool_args: Any,
        action_class: str,
        strategy_fingerprint: str = "",
    ) -> None:
        cls._reject_terminal_state(state)
        if not state.get("replan_required"):
            return
        if kind.exempt_from_budget:
            return
        if state.get("replan_trial_pending"):
            cls._consume_replan_denial(state, "replan_trial_pending")
            raise ValueError(
                "A replan trial already ran without verified semantic progress. "
                "Wait for the runtime observation, or finish partial/blocked instead of trying "
                "another selector, generation, tool, or phase."
            )
        replan_count = int(state.get("replan_count") or 0)
        budget_s = float(state.get("deadline_budget_s") or 0.0)
        remaining_s = float(state.get("deadline_remaining_s") or 0.0)
        replan_limit = 4 if budget_s and remaining_s / budget_s > 0.5 else 2
        if replan_count >= replan_limit:
            state["status"] = "partial" if cls._has_task_evidence(state) else "blocked"
            state["blockers"] = ["semantic_replan_budget_exhausted"]
            state["terminal_reason"] = "semantic_replan_budget_exhausted"
            state["next_action_class"] = "finish"
            raise ValueError(
                f"Semantic progress remained blocked after {replan_limit} replan trials. "
                "Return blocked or partial with the available structured evidence."
            )
        blocked_strategy = str(
            state.get("blocked_strategy")
            or state.get("last_strategy_fingerprint")
            or state.get("last_action_class")
            or ""
        )
        failed_strategies = {str(item) for item in state.get("failed_strategies") or [] if str(item).strip()}
        if blocked_strategy:
            failed_strategies.add(blocked_strategy)
        current_strategy = strategy_fingerprint or action_class
        if current_strategy in failed_strategies or action_class in failed_strategies:
            if cls._allow_read_only_recovery(state, kind, tool_args, current_strategy):
                state["replan_count"] = replan_count + 1
                state["replan_trial_pending"] = True
                state["trial_strategy"] = current_strategy
                state["status"] = "replan_trial"
                return
            cls._consume_replan_denial(state, "repeated_strategy")
            raise ValueError(
                "Semantic loop detected. Changing selector, generation, tool name, or phase does not "
                f"change the {action_class} target/field/intent strategy. Re-plan materially or finish "
                "partial/blocked."
            )
        state["replan_count"] = replan_count + 1
        state["replan_trial_pending"] = True
        state["trial_strategy"] = current_strategy
        state["status"] = "replan_trial"

    @classmethod
    def _allow_read_only_recovery(
        cls,
        state: Dict[str, Any],
        kind: ToolKind,
        tool_args: Any,
        strategy_fingerprint: str,
    ) -> bool:
        if not cls._is_read_only_recovery(kind, tool_args):
            return False
        counts = state.setdefault("read_only_recovery_counts", {})
        count = int(counts.get(strategy_fingerprint) or 0)
        if count >= _BROWSER_READ_ONLY_RECOVERY_LIMIT:
            return False
        counts[strategy_fingerprint] = count + 1
        return True

    @classmethod
    def _is_read_only_recovery(cls, kind: ToolKind, tool_args: Any) -> bool:
        args = cls._coerce_tool_args(tool_args)
        if kind.read_only:
            return True
        # Switching tabs changes nothing on any page, so it may recover a stalled plan like a read.
        if isinstance(kind.action, SwitchContext):
            return True
        if isinstance(kind.action, RunScript):
            return cls._operation_intent(kind, args) != "script_mutation"
        steps = args.get("steps")
        if not kind.helper or not isinstance(steps, list) or not steps:
            return False
        read_only_ops = _BATCH_SAFE_READ_SELECTOR_OPS | _BATCH_EXPLICIT_SELECTOR_OPS | {
            "wait_for_text",
            "wait_for_load_state",
            "wait_for_url",
            "wait_for_tab",
        }
        return all(
            isinstance(step, dict) and str(step.get("op") or "").strip().lower() in read_only_ops
            for step in steps
        )

    @staticmethod
    def _consume_replan_denial(state: Dict[str, Any], reason: str) -> None:
        denial_count = int(state.get("replan_denial_count") or 0) + 1
        state["replan_denial_count"] = denial_count
        state["last_denial_reason"] = str(reason or "")[:120]
        if denial_count < _BROWSER_REPLAN_DENIAL_LIMIT:
            return
        blockers = list(state.get("blockers") or [])
        if "semantic_replan_denial_budget_exhausted" not in blockers:
            blockers.append("semantic_replan_denial_budget_exhausted")
        state["blockers"] = blockers[:10]
        state["status"] = "blocked"
        state["next_action_class"] = "finish"
        state["terminal_reason"] = "semantic_replan_denial_budget_exhausted"

    @classmethod
    def _reject_revisited_price_interval(
        cls,
        details: Dict[str, Any],
        phase: str,
        kind: ToolKind,
        tool_args: Any,
    ) -> None:
        price_interval = cls._price_interval_signature(kind, tool_args)
        visited_intervals = details.setdefault("visited_price_intervals", [])
        if phase == "filtering" and price_interval and price_interval in visited_intervals:
            raise ValueError(
                f"Price interval {price_interval} was already visited in this task. "
                "Use the existing evidence or choose a different interval."
            )

    @classmethod
    @classmethod
    def _record_phase_result(
        cls,
        session: Any,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
    ) -> Dict[str, Any]:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return {}
        result = tool_result if isinstance(tool_result, dict) else {"result": tool_result}
        succeeded = result.get("ok", True) is not False
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return {
                "phase": state.get("current_phase") or "unknown",
                "success": succeeded,
                "new_evidence_fields": [],
                "evidence_added": False,
                "recovered": False,
            }
        phase = cls._classify_tool_phase(kind, tool_args, state)
        phases = state.get("phases")
        if not isinstance(phases, dict):
            return {}
        details = phases.get(phase)
        if not isinstance(details, dict):
            return {}
        args = cls._coerce_tool_args(tool_args)
        if not succeeded:
            evidence_delta = cls._record_tool_evidence(
                state,
                result,
                kind=kind,
                tool_name=tool_name,
                tool_args=args,
            )
            completion_evidence = cls._phase_completion_evidence(phase, kind, result, state)
            cls._record_failed_phase_result(state, details, tool_result)
            missing_fields = cls._missing_completion_requirements(state) if phase == "extraction" else []
            if completion_evidence and not missing_fields:
                cls._complete_phase(state, phases, phase, details, completion_evidence)
            session.update_state({_BROWSER_PHASE_STATE_KEY: state})
            return {"phase": phase, "success": False, **evidence_delta}

        details["successes"] = int(details.get("successes") or 0) + 1
        details["last_error"] = ""
        cls._record_price_interval(details, phase, kind, args)
        evidence_delta = cls._record_tool_evidence(state, result, kind=kind, tool_name=tool_name, tool_args=args)
        if evidence_delta.get("recovered"):
            BrowserWorkingContextStore.mark_replan_recovered(state)
        completion_evidence = cls._phase_completion_evidence(phase, kind, result, state)
        missing_fields = cls._missing_completion_requirements(state) if phase == "extraction" else []
        if completion_evidence and not missing_fields:
            cls._complete_phase(state, phases, phase, details, completion_evidence)
        elif details.get("status") != "replan_required":
            details["status"] = "in_progress"
            details["missing_fields"] = missing_fields
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return {"phase": phase, "success": True, **evidence_delta}
    @staticmethod
    def _missing_required_fields(state: Dict[str, Any]) -> list[str]:
        required = {str(item) for item in state.get("required_fields") or [] if str(item).strip()}
        coverage = {str(item) for item in state.get("field_coverage") or [] if str(item).strip()}
        return sorted(required - coverage)

    @staticmethod
    def _has_task_evidence(state: Dict[str, Any]) -> bool:
        """Use evidence records, not compatibility coverage, as task truth."""

        return bool(state.get("evidence_slots") or state.get("structured_evidence"))

    @staticmethod
    def _task_observations(state: Dict[str, Any]) -> list[Dict[str, Any]]:
        return [
            item for item in state.get("structured_evidence") or []
            if isinstance(item, dict) and item.get("kind") == "page_observation"
        ]

    @classmethod
    def _advisory_missing_fields(cls, state: Dict[str, Any]) -> set[str]:
        """Do not mistake an incomplete field adapter for an unsuccessful page read."""
        if state.get("requirements_source") != "inferred" or not cls._task_observations(state):
            return set()
        slots = state.get("required_evidence_slots") or []
        # Comparison variants and typed rating/sort evidence remain strict.
        if any(isinstance(slot, dict) and slot.get("variant", "default") != "default" for slot in slots):
            return set()
        return set(cls._missing_required_fields(state)) - _BROWSER_STRICT_EVIDENCE_FIELDS

    @classmethod
    def _missing_completion_requirements(cls, state: Dict[str, Any]) -> list[str]:
        missing = cls._missing_required_fields(state)
        missing_field_names = set(missing)
        missing_slots = cls._missing_evidence_slots(state)
        missing.extend(
            f"evidence_slot:{slot['entity']}:{slot['variant']}:{slot['field']}"
            for slot in missing_slots
            if not (slot["variant"] == "default" and slot["field"] in missing_field_names)
        )
        requested_count = int(state.get("requested_result_count") or 0)
        observed_count = int(state.get("observed_result_count") or 0)
        if requested_count and observed_count < requested_count:
            missing.append(f"result_count:{observed_count}/{requested_count}")
        return missing

    @classmethod
    def _missing_evidence_slots(cls, state: Dict[str, Any]) -> list[Dict[str, str]]:
        required_slots = set()
        for slot in state.get("required_evidence_slots") or []:
            if isinstance(slot, dict):
                required_slots.add(cls._evidence_slot_key(slot))

        covered_slots = set()
        for slot in state.get("evidence_slots") or []:
            if not isinstance(slot, dict):
                continue
            status = str(slot.get("status") or "").strip().lower()
            if slot.get("value") not in (None, "") or status in {"missing", "unknown"}:
                covered_slots.add(cls._evidence_slot_key(slot))
        return [
            {"entity": entity, "variant": variant, "field": field_name}
            for entity, variant, field_name in sorted(required_slots - covered_slots)
        ]

    @classmethod
    def _unavailable_evidence_slots(cls, state: Dict[str, Any]) -> list[Dict[str, str]]:
        required_slots = set()
        for slot in state.get("required_evidence_slots") or []:
            if isinstance(slot, dict):
                required_slots.add(cls._evidence_slot_key(slot))

        unavailable = []
        for slot in state.get("evidence_slots") or []:
            if not isinstance(slot, dict):
                continue
            key = cls._evidence_slot_key(slot)
            status = str(slot.get("status") or "").strip().lower()
            if key not in required_slots or status not in {"missing", "unknown"}:
                continue
            unavailable.append(
                {
                    "entity": key[0],
                    "variant": key[1],
                    "field": key[2],
                    "status": status or "unknown",
                }
            )
        return unavailable

    @staticmethod
    def _evidence_slot_key(slot: Dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(slot.get("entity") or "").strip().lower(),
            str(slot.get("variant") or "").strip().lower(),
            str(slot.get("field") or "").strip().lower(),
        )

    @staticmethod
    def _record_failed_phase_result(state: Dict[str, Any], details: Dict[str, Any], tool_result: Any) -> None:
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return
        details["status"] = "pending"
        if isinstance(tool_result, dict):
            details["last_error"] = str(tool_result.get("error") or "")[:300]
        if not state.get("replan_trial_pending"):
            return
        trial_strategy = str(state.get("trial_strategy") or "")
        state["replan_trial_pending"] = False
        state["replan_required"] = True
        state["blocked_strategy"] = trial_strategy
        BrowserWorkingContextStore.record_failed_strategy(state, trial_strategy)
        state["status"] = "replan_required"

    @classmethod
    def _record_tool_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        previous_evidence = {
            cls._evidence_signature(item) for item in state.get("structured_evidence") or []
            if isinstance(item, dict)
        }
        previous_coverage = set(state.get("field_coverage") or [])
        cls._record_structured_evidence(state, result, kind=kind, tool_name=tool_name, tool_args=tool_args)
        evidence_added = any(
            cls._evidence_signature(item) not in previous_evidence
            for item in state.get("structured_evidence") or [] if isinstance(item, dict)
        )
        new_evidence_fields = sorted(set(state.get("field_coverage") or []) - previous_coverage)
        return {
            "new_evidence_fields": new_evidence_fields,
            "evidence_added": evidence_added,
            "recovered": bool(evidence_added or new_evidence_fields),
        }

    @classmethod
    def _record_price_interval(
        cls,
        details: Dict[str, Any],
        phase: str,
        kind: ToolKind,
        args: Dict[str, Any],
    ) -> None:
        price_interval = cls._price_interval_signature(kind, args)
        if phase != "filtering" or not price_interval:
            return
        visited_intervals = details.setdefault("visited_price_intervals", [])
        if price_interval not in visited_intervals:
            visited_intervals.append(price_interval)

    @classmethod
    def _record_structured_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> None:
        evidence = cls._structured_evidence(result)
        if not evidence and isinstance(kind.action, RunScript):
            evidence = cls._evaluate_evidence(
                result,
                tool_args,
                required_fields=state.get("required_fields") or [],
            )
        if not evidence and "probe_interactives" in str(tool_name or "").lower():
            evidence = cls._interactive_probe_evidence(result)
        stored_evidence = state.setdefault("structured_evidence", [])
        known_signatures = {cls._evidence_signature(item) for item in stored_evidence if isinstance(item, dict)}
        observation = cls._page_observation_evidence(state, result, kind, tool_name, tool_args)
        for item in (evidence, observation):
            if item and cls._evidence_signature(item) not in known_signatures:
                stored_evidence.append(item)
        del stored_evidence[:-20]
        if not evidence:
            return
        if evidence.get("kind") == "card_probe":
            state["observed_result_count"] = max(
                int(state.get("observed_result_count") or 0),
                int(evidence.get("observed_count") or 0),
            )
        cls._record_evidence_slots(
            state,
            evidence,
            result=result,
            tool_name=tool_name,
            tool_args=tool_args,
        )
        BrowserWorkingContextStore.refresh_field_coverage(state)

    @classmethod
    def _page_observation_evidence(
        cls,
        state: Dict[str, Any],
        result: Dict[str, Any],
        kind: ToolKind,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Retain source text without inventing field coverage or another memory store."""
        if result.get("ok", True) is False:
            return {}
        if not isinstance(kind.action, (Observe, RunScript)):
            return {}
        if not cls._is_read_only_recovery(kind, tool_args):
            return {}
        value = cls._evaluate_result_value(result)
        if value in (None, "", [], {}) or isinstance(value, (bool, int, float)):
            return {}
        if isinstance(value, dict) and (value.get("ok") is False or value.get("error")):
            return {}
        raw_text = value if isinstance(value, str) else cls._evidence_signature(value)
        # AX references and selector spelling are identity metadata, not new facts.
        normalized = re.sub(r"\s*\[(?:ref|target_id|generation_id)=[^\]]+\]", "", raw_text)
        normalized = " ".join(normalized.split())
        if len(normalized) < 16:
            return {}
        _, source = cls._evidence_variant_and_url(state, result, tool_args)
        if not source:
            return {}
        page = result.get("page_state") or {}
        generation = str(result.get("generation_id") or page.get("generation_id") or "")
        target = str(tool_args.get("target") or tool_args.get("selector") or "")[:240]
        return {
            "kind": "page_observation",
            "source": source,
            "generation_id": generation,
            "raw_text": normalized[:4_000],
            "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20],
            "provenance": {
                "tool": tool_name,
                **cls._evaluate_execution_provenance(tool_args, target),
            },
        }

    @classmethod
    def _record_evidence_slots(
        cls,
        state: Dict[str, Any],
        evidence: Dict[str, Any],
        *,
        result: Dict[str, Any],
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> None:
        if not state.get("required_evidence_slots"):
            return
        variant, source_url = cls._evidence_variant_and_url(state, result, tool_args)
        values, generation, provenance, field_status = cls._evidence_values(evidence)
        if not values and not field_status:
            return
        slots = state.setdefault("evidence_slots", [])
        covered = {
            cls._evidence_slot_key(slot): index
            for index, slot in enumerate(slots)
            if isinstance(slot, dict)
        }
        for required in state.get("required_evidence_slots") or []:
            if not isinstance(required, dict):
                continue
            key = cls._evidence_slot_key(required)
            if key[1] != "default" and key[1] != variant:
                continue
            slot = cls._build_evidence_slot(
                key,
                values=values,
                provenance=provenance,
                field_status=field_status,
                generation=generation,
                source_url=source_url,
                tool_name=tool_name,
            )
            if slot is None:
                continue
            existing_index = covered.get(key)
            if existing_index is not None:
                existing = slots[existing_index]
                existing_status = str(existing.get("status") or "present").strip().lower()
                if existing_status == "present" or slot.get("status") != "present":
                    continue
                slots[existing_index] = slot
            else:
                slots.append(slot)
                covered[key] = len(slots) - 1
        del state["evidence_slots"][:-20]

    @staticmethod
    def _build_evidence_slot(
        key: tuple[str, str, str],
        *,
        values: Dict[str, Any],
        provenance: Dict[str, Any],
        field_status: Dict[str, Any],
        generation: str,
        source_url: str,
        tool_name: str,
    ) -> Optional[Dict[str, Any]]:
        value = values.get(key[2])
        status = str(field_status.get(key[2]) or ("present" if value not in (None, "") else "")).lower()
        if status not in {"present", "missing", "unknown"}:
            return None
        if status == "present" and value in (None, ""):
            return None
        item = provenance.get(key[2]) if isinstance(provenance, dict) else None
        item = item if isinstance(item, dict) else {}
        slot = {
            "entity": key[0],
            "variant": key[1],
            "field": key[2],
            "status": status,
            "source": str(item.get("source") or source_url or tool_name or "")[:500],
            "generation": str(item.get("generation_id") or generation or ""),
        }
        if status == "present":
            slot["value"] = str(value)[:500]
        for provenance_key in ("selector", "raw_text"):
            provenance_value = str(item.get(provenance_key) or "").strip()
            if provenance_value:
                slot[provenance_key] = provenance_value[:600]
        return slot

    @staticmethod
    def _evidence_values(
        evidence: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str, Dict[str, Any], Dict[str, str]]:
        values = evidence.get("values")
        if isinstance(values, dict):
            provenance = evidence.get("provenance")
            statuses = evidence.get("field_status")
            return (
                values,
                str(evidence.get("generation_id") or ""),
                provenance if isinstance(provenance, dict) else {},
                dict(statuses) if isinstance(statuses, dict) else {},
            )
        cards = evidence.get("cards")
        if not isinstance(cards, list):
            return {}, str(evidence.get("generation_id") or ""), {}, {}
        first = next((card for card in cards if isinstance(card, dict)), {})
        if not first:
            return {}, "", {}, {}
        provenance = first.get("provenance")
        statuses = first.get("field_status")
        return (
            first,
            str(first.get("generation_id") or evidence.get("generation_id") or ""),
            provenance if isinstance(provenance, dict) else {},
            dict(statuses) if isinstance(statuses, dict) else {},
        )

    @staticmethod
    def _evidence_variant_and_url(
        state: Dict[str, Any],
        result: Dict[str, Any],
        tool_args: Dict[str, Any],
    ) -> tuple[str, str]:
        page_state = result.get("page_state") if isinstance(result.get("page_state"), dict) else {}
        last_page = state.get("last_page") if isinstance(state.get("last_page"), dict) else {}
        source_url = str(
            result.get("url")
            or page_state.get("url")
            or tool_args.get("url")
            or last_page.get("url")
            or ""
        ).strip()
        query: Dict[str, list[str]] = {}
        try:
            query = parse_qs(urlsplit(source_url).query)
        except ValueError:
            pass
        order = " ".join(query.get("order", []) + query.get("sort", [])).lower()
        serialized_args = json.dumps(tool_args, ensure_ascii=False, default=str).lower()
        if any(token in order for token in ("pubdate", "latest", "newest", "recent")) or any(
            token in serialized_args for token in ("最新", "latest", "newest", "pubdate")
        ):
            return "latest", source_url
        if any(token in order for token in ("totalrank", "relevance", "default", "comprehensive")) or any(
            token in serialized_args for token in ("综合", "comprehensive", "relevance", "totalrank")
        ):
            return "comprehensive", source_url
        required_variants = {
            str(slot.get("variant") or "")
            for slot in state.get("required_evidence_slots") or []
            if isinstance(slot, dict)
        }
        if required_variants == {"comprehensive", "latest"} and source_url and not order:
            return "comprehensive", source_url
        return "default", source_url

    @classmethod
    def _evidence_signature(cls, evidence: Dict[str, Any]) -> str:
        if isinstance(evidence, dict) and evidence.get("kind") == "page_observation":
            return json.dumps([evidence.get("source"), evidence.get("content_hash")], ensure_ascii=False)
        ignored_keys = {
            "generation",
            "generation_id",
            "sel",
            "selector",
            "selector_hint",
            "ref",
            "target_id",
            "target",
            "provenance",
            "execution_provenance",
        }

        def semantic_value(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): semantic_value(item) for key, item in value.items() if str(key) not in ignored_keys}
            if isinstance(value, list):
                return [semantic_value(item) for item in value]
            return value

        return json.dumps(
            semantic_value(evidence),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _evaluate_evidence(
        cls,
        result: Dict[str, Any],
        tool_args: Dict[str, Any],
        *,
        required_fields: Iterable[str] = (),
    ) -> Dict[str, Any]:
        value = cls._evaluate_result_value(result)
        if value is None:
            return {}
        trusted_fields, allowed_fields = cls._evaluate_field_scope(tool_args, required_fields)
        compact_values, fields, field_status, raw_values = cls._compact_evaluate_values(
            value,
            allowed_fields=allowed_fields,
            trusted_fields=trusted_fields,
        )
        if not compact_values and not field_status:
            return {}
        raw_preview = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        generation_id = str(result.get("generation_id") or tool_args.get("generation_id") or "")
        target = str(tool_args.get("target") or tool_args.get("element") or "")[:240]
        return {
            "kind": "targeted_evaluate",
            "generation_id": generation_id,
            "fields": fields,
            "values": compact_values,
            "field_status": field_status,
            "preview": " ".join(raw_preview.split())[:800],
            "provenance": cls._evaluate_field_provenance(
                compact_values,
                field_status=field_status,
                raw_values=raw_values,
                target=target,
                generation_id=generation_id,
            ),
            "execution_provenance": cls._evaluate_execution_provenance(tool_args, target),
        }

    @staticmethod
    def _evaluate_result_value(result: Dict[str, Any]) -> Any:
        value = next(
            (result.get(key) for key in ("result", "value", "data") if result.get(key) is not None),
            None,
        )
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return extract_json_object(value) or value

    @classmethod
    def _evaluate_field_scope(
        cls,
        tool_args: Dict[str, Any],
        required_fields: Iterable[str],
    ) -> tuple[set[str], set[str]]:
        trusted_fields = {
            cls._canonical_field_name(field_name)
            for field_name in tool_args.get("_runtime_evidence_fields") or []
        }
        trusted_fields.discard("")
        allowed_fields = {
            cls._canonical_field_name(field_name)
            for field_name in required_fields
        }
        allowed_fields.discard("")
        allowed_fields.update(trusted_fields)
        return trusted_fields, allowed_fields

    @staticmethod
    def _evaluate_execution_provenance(tool_args: Dict[str, Any], target: str) -> Dict[str, str]:
        expression = str(
            tool_args.get("function")
            or tool_args.get("expression")
            or tool_args.get("script")
            or tool_args.get("code")
            or ""
        )
        return {
            "target": target,
            "expression_sha256": hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16] if expression else "",
        }

    @classmethod
    def _compact_evaluate_values(
        cls,
        value: Any,
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[Dict[str, str], list[str], Dict[str, str], Dict[str, str]]:
        if isinstance(value, dict):
            return cls._compact_evaluate_mapping(
                value,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            return cls._compact_evaluate_list(
                value,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
        return {}, [], {}, {}

    @classmethod
    def _compact_evaluate_mapping(
        cls,
        value: Dict[str, Any],
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[Dict[str, str], list[str], Dict[str, str], Dict[str, str]]:
        compact_values: Dict[str, str] = {}
        field_status: Dict[str, str] = {}
        raw_values: Dict[str, str] = {}
        for key, item in list(value.items())[:20]:
            normalized = cls._normalize_evaluate_mapping_item(
                key,
                item,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
            if normalized is None:
                continue
            field_name, field_value, status, raw_value = normalized
            field_status[field_name] = status
            raw_values[field_name] = raw_value
            if status == "present":
                compact_values[field_name] = field_value
        return compact_values, sorted(compact_values), field_status, raw_values

    @classmethod
    def _normalize_evaluate_mapping_item(
        cls,
        key: Any,
        item: Any,
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[str, str, str, str] | None:
        selected_sort = cls._selected_sort_value(key, item)
        normalized: tuple[str, str, str, str] | None = None
        if selected_sort:
            if not allowed_fields or "sort_state" in allowed_fields:
                normalized = ("sort_state", selected_sort, "present", f"{key}={item}"[:600])
        else:
            snake_name = cls._snake_case_field_name(key)
            canonical = cls._canonical_evaluate_field_name(key)
            field_allowed = canonical in _BROWSER_FIELD_ALIASES and (
                not allowed_fields or canonical in allowed_fields
            )
            contextual_alias_allowed = (
                snake_name not in _BROWSER_CONTEXTUAL_EVALUATE_FIELD_ALIASES
                or canonical in trusted_fields
            )
            if field_allowed and contextual_alias_allowed and not (
                canonical == "sort_state" and isinstance(item, bool)
            ):
                normalized_value, status = cls._normalize_evaluate_field_value(canonical, item)
                normalized = (canonical, normalized_value, status, str(item)[:600])
        return normalized

    @staticmethod
    def _merge_first_values(target: Dict[str, str], source: Dict[str, str]) -> None:
        for field_name, field_value in source.items():
            target.setdefault(field_name, field_value)

    @classmethod
    def _compact_evaluate_list(
        cls,
        value: list[Dict[str, Any]],
        *,
        allowed_fields: set[str],
        trusted_fields: set[str],
    ) -> tuple[Dict[str, str], list[str], Dict[str, str], Dict[str, str]]:
        compact_values: Dict[str, str] = {}
        field_status: Dict[str, str] = {}
        raw_values: Dict[str, str] = {}
        fields: set[str] = set()
        for item in value[:5]:
            item_values, item_fields, item_status, item_raw = cls._compact_evaluate_mapping(
                item,
                allowed_fields=allowed_fields,
                trusted_fields=trusted_fields,
            )
            fields.update(item_fields)
            cls._merge_first_values(compact_values, item_values)
            cls._merge_first_values(field_status, item_status)
            cls._merge_first_values(raw_values, item_raw)
        if not fields and not field_status:
            return {}, [], {}, {}
        compact_values["items"] = json.dumps(value[:3], ensure_ascii=False, default=str)[:800]
        return compact_values, sorted(fields), field_status, raw_values

    @classmethod
    def _selected_sort_value(cls, key: Any, value: Any) -> str:
        snake_name = cls._snake_case_field_name(key)
        if snake_name in {"active_tab", "active_tabs", "selected_tab", "selected_tabs"}:
            if isinstance(value, bool):
                return ""
            text = " ".join(str(value or "").split())
            return text[:120] if text else ""
        if not snake_name.endswith(("_selected", "_tab_selected")) or value is not True:
            return ""
        base_name = re.sub(r"(?:_tab)?_selected$", "", snake_name)
        for token, label in _BROWSER_SELECTED_SORT_LABELS.items():
            if token in base_name:
                return label
        return ""

    @staticmethod
    def _normalize_evaluate_field_value(field_name: str, value: Any) -> tuple[str, str]:
        if value in (None, "", [], {}):
            return "", "missing"
        text = " ".join(str(value).split())[:300]
        if field_name == "comments" and _BROWSER_ZERO_COMMENT_RE.fullmatch(text):
            return "0", "present"
        if _BROWSER_UNKNOWN_VALUE_RE.fullmatch(text):
            return "", "unknown"
        return text, "present"

    @staticmethod
    def _evaluate_field_provenance(
        values: Dict[str, str],
        *,
        field_status: Dict[str, str],
        raw_values: Dict[str, str],
        target: str,
        generation_id: str,
    ) -> Dict[str, Dict[str, str]]:
        return {
            field_name: {
                "source": "browser_evaluate",
                "selector": target,
                "raw_text": str(raw_values.get(field_name) or values.get(field_name) or "")[:600],
                "generation_id": generation_id,
            }
            for field_name in set(values) | set(field_status)
        }

    @classmethod
    def _interactive_probe_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        elements = result.get("elements")
        if not isinstance(elements, list) or not elements:
            return {}
        targets = [
            {
                "target_id": str(item.get("target_id") or ""),
                "generation_id": str(item.get("generation_id") or result.get("generation_id") or ""),
                "kind": str(item.get("kind") or ""),
                "region": str(item.get("region") or ""),
                "name": str(item.get("accessible_name") or item.get("text") or "")[:160],
                "actionable": bool(item.get("actionable")),
                "selected": bool(item.get("selected")),
                "selected_source": str(item.get("selected_source") or "")[:40],
            }
            for item in elements[:12]
            if isinstance(item, dict)
        ]
        selected_sort = next(
            (
                target
                for target in targets
                if target["selected"] and cls._is_sort_interactive_target(target)
            ),
            None,
        )
        evidence = {
            "kind": "interactive_probe",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": [],
            "target_count": len(targets),
            "targets": targets,
        }
        if selected_sort and selected_sort["name"]:
            evidence["fields"] = ["sort_state"]
            evidence["values"] = {"sort_state": selected_sort["name"]}
            evidence["provenance"] = {
                "sort_state": {
                    "source": "browser_probe_interactives",
                    "selector": selected_sort["target_id"],
                    "raw_text": selected_sort["name"],
                    "generation_id": selected_sort["generation_id"],
                    "selection_source": selected_sort["selected_source"],
                }
            }
        return evidence

    @staticmethod
    def _is_sort_interactive_target(target: Dict[str, Any]) -> bool:
        if str(target.get("kind") or "") in {"sort", "sort_tab", "sort_option"}:
            return True
        name = str(target.get("name") or "").strip().lower()
        region = str(target.get("region") or "").strip().lower()
        return bool(
            region in {"sort", "filtering", "search_controls"}
            or any(token in name for token in _BROWSER_SELECTED_SORT_LABELS)
        )

    @classmethod
    def _record_recent_action(
        cls,
        session: Any,
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        action_class: str,
        elapsed_ms: int,
        progress_delta: Dict[str, Any],
    ) -> bool:
        state = session.get_state(_BROWSER_PHASE_STATE_KEY)
        if not isinstance(state, dict):
            return False
        state["action_sequence"] = int(state.get("action_sequence") or 0) + 1
        recent_actions = deque(state.get("recent_actions") or [], maxlen=6)
        recent_actions.append(
            cls._build_action_record(
                state,
                kind=kind,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                action_class=action_class,
                elapsed_ms=elapsed_ms,
                progress_delta=progress_delta,
            )
        )
        state["recent_actions"] = list(recent_actions)
        cls._update_last_page(state, tool_result)
        session.update_state({_BROWSER_PHASE_STATE_KEY: state})
        return bool(progress_delta.get("recovered"))

    @classmethod
    def _build_action_record(
        cls,
        state: Dict[str, Any],
        *,
        kind: ToolKind,
        tool_name: str,
        tool_args: Any,
        tool_result: Any,
        action_class: str,
        elapsed_ms: int,
        progress_delta: Dict[str, Any],
    ) -> Dict[str, Any]:
        succeeded = bool(progress_delta.get("success"))
        error = str(tool_result.get("error") or "").strip() if isinstance(tool_result, dict) else ""
        semantic_delta = "evidence_added" if progress_delta.get("evidence_added") else "pending"
        outcome_status = "success" if succeeded else "failed"
        if progress_delta.get("ambiguous"):
            outcome_status = "ambiguous"
            semantic_delta = "awaiting_observation"
        elif not succeeded:
            semantic_delta = "error"
        return {
            "seq": state["action_sequence"],
            "phase": progress_delta.get("phase") or state.get("current_phase"),
            "action_class": action_class or cls._classify_action_class(kind, tool_name, tool_args, state),
            "target_summary": cls._target_summary(tool_name, tool_args),
            "outcome": "success" if succeeded else f"error: {error[:180] or 'tool_failed'}",
            "outcome_status": outcome_status,
            "semantic_delta": semantic_delta,
            "new_evidence_fields": list(progress_delta.get("new_evidence_fields") or []),
            "elapsed_ms": max(0, int(elapsed_ms)),
        }

    @staticmethod
    def _update_last_page(state: Dict[str, Any], tool_result: Any) -> None:
        result = tool_result if isinstance(tool_result, dict) else {}
        page_state = result.get("page_state") if isinstance(result.get("page_state"), dict) else {}
        url = str(result.get("url") or page_state.get("url") or "")
        title = str(result.get("title") or page_state.get("title") or "")
        if url or title:
            last_page = state.setdefault("last_page", {})
            if url:
                last_page["url"] = url
            if title:
                last_page["title"] = title

    @classmethod
    def _target_summary(cls, tool_name: str, tool_args: Any) -> str:
        args = cls._coerce_tool_args(tool_args)
        name = str(tool_name or "").strip().rsplit(".", 1)[-1]
        summary: Dict[str, Any] = {"tool": name}
        for key in ("url", "target_id", "target", "ref", "element", "name", "label", "text", "value"):
            value = args.get(key)
            if value not in (None, "", [], {}):
                summary[key] = str(value)[:160]
        steps = args.get("steps")
        if isinstance(steps, list):
            summary["ops"] = [str(step.get("op") or "") for step in steps[:12] if isinstance(step, dict)]
        expression = str(args.get("function") or args.get("expression") or args.get("script") or args.get("code") or "")
        if expression:
            summary["expression_sha256"] = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]
            summary["expression_chars"] = len(expression)
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))[:600]

    @staticmethod
    def _phase_completion_evidence(
        phase: str,
        kind: ToolKind,
        result: Dict[str, Any],
        state: Dict[str, Any],
    ) -> str:
        successful_condition_operations = {
            str(condition.get("op") or "").strip().lower()
            for condition in (result.get("conditions") if isinstance(result.get("conditions"), list) else [])
            if isinstance(condition, dict) and condition.get("ok") is True
        }
        if phase == "navigation":
            result_url = str(result.get("url") or "")
            if result_url or kind.effect is Effect.NAVIGATE:
                return result_url or "navigation tool succeeded"
        if phase == "form" and successful_condition_operations & {
            "wait_for_url",
            "wait_for_selector",
            "wait_for_text",
            "wait_for_first_card_title",
            "wait_for_result_count",
            "wait_for_dom_text_change",
            "wait_for_stable",
            "wait_for_tab",
        }:
            return "form batch completed with an observable condition"
        if phase == "filtering" and successful_condition_operations & {
            "wait_for_sort_state",
            "wait_for_result_count",
            "wait_for_dom_text_change",
            "wait_for_first_card_title",
        }:
            return "requested state and changed results were observed"
        if phase == "extraction":
            extracted = result.get("extracted")
            cards = result.get("cards")
            if isinstance(extracted, dict) and extracted:
                return f"structured extraction returned {len(extracted)} field(s)"
            if isinstance(cards, list) and cards:
                # A card probe reads a results list, not the entity the task is about. A task
                # asking for one specific item (requested_result_count == 0) needs a real
                # `extracted` result from opening it; _missing_completion_requirements already
                # gates list tasks (requested_result_count >= 1) on having enough cards.
                if int(state.get("requested_result_count") or 0) < 1:
                    return ""
                excluded_regions = {"hot_search", "sidebar", "account", "chat"}
                excluded_kinds = {
                    "hot_search",
                    "paid_column",
                    "promotion",
                    "activity",
                    "account",
                    "shop",
                    "chat",
                }
                natural_cards = []
                for card in cards:
                    if not isinstance(card, dict) or card.get("is_ad") is True:
                        continue
                    if str(card.get("region") or "") in excluded_regions:
                        continue
                    if str(card.get("kind") or "") in excluded_kinds:
                        continue
                    natural_cards.append(card)
                if natural_cards:
                    return f"card probe returned {len(natural_cards)} structured result(s)"
        return ""

    @staticmethod
    def _complete_phase(
        state: Dict[str, Any],
        phases: Dict[str, Any],
        phase: str,
        details: Dict[str, Any],
        completion_evidence: str,
    ) -> None:
        if str(state.get("status") or "").strip().lower() in _BROWSER_TERMINAL_STATUSES:
            return
        details["status"] = "completed"
        details["completion_evidence"] = completion_evidence[:300]
        phase_order = list(phases)
        try:
            remaining_phases = phase_order[phase_order.index(phase) + 1:]
        except ValueError:
            remaining_phases = []
        next_phase = next(
            (
                candidate
                for candidate in remaining_phases
                if isinstance(phases.get(candidate), dict) and phases[candidate].get("status") != "completed"
            ),
            phase,
        )
        state["current_phase"] = next_phase
        if all(isinstance(item, dict) and item.get("status") == "completed" for item in phases.values()):
            state["status"] = "completed"
            state["next_action_class"] = "finish"
        else:
            state["next_action_class"] = next_phase

    @classmethod
    def _structured_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        extracted = result.get("extracted")
        if isinstance(extracted, dict) and extracted:
            return cls._structured_extraction_evidence(result, extracted)
        condition_evidence = cls._condition_evidence(result)
        if condition_evidence:
            return condition_evidence
        return cls._card_probe_evidence(result)

    @classmethod
    def _structured_extraction_evidence(
        cls,
        result: Dict[str, Any],
        extracted: Dict[str, Any],
    ) -> Dict[str, Any]:
        provenance = result.get("field_provenance")
        compact_values: Dict[str, str] = {}
        compact_provenance: Dict[str, Any] = {}
        for key, value in list(extracted.items())[:20]:
            if value in (None, "", [], {}):
                continue
            canonical = cls._canonical_field_name(key) or str(key)
            compact_values[canonical] = str(value)[:300]
            if isinstance(provenance, dict) and isinstance(provenance.get(key), dict):
                compact_provenance[canonical] = dict(provenance[key])
        condition_values, condition_provenance = cls._successful_condition_values(result)
        for key, value in condition_values.items():
            compact_values.setdefault(key, value)
            if key in condition_provenance:
                compact_provenance.setdefault(key, condition_provenance[key])
        return {
            "kind": "structured_extraction",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": sorted(compact_values),
            "values": compact_values,
            "provenance": compact_provenance,
        }

    @classmethod
    def _condition_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        values, provenance = cls._successful_condition_values(result)
        if not values:
            return {}
        return {
            "kind": "condition_observation",
            "generation_id": str(result.get("generation_id") or ""),
            "fields": sorted(values),
            "values": values,
            "provenance": provenance,
        }

    @staticmethod
    def _successful_condition_values(
        result: Dict[str, Any],
    ) -> tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
        field_by_op = {
            "wait_for_sort_state": "sort_state",
            "wait_for_result_count": "result_count",
            "wait_for_first_card_title": "title",
            "wait_for_url": "url",
            "wait_for_dom_text_change": "dom_text",
            "wait_for_tab": "tab_state",
        }
        values: Dict[str, str] = {}
        provenance: Dict[str, Dict[str, str]] = {}
        generation_id = str(result.get("generation_id") or "")
        conditions = result.get("conditions")
        for item in conditions if isinstance(conditions, list) else []:
            if not isinstance(item, dict) or item.get("ok") is not True:
                continue
            field_name = field_by_op.get(str(item.get("op") or "").strip().lower())
            if not field_name:
                continue
            observed = item.get("observed")
            if isinstance(observed, dict):
                value = next(
                    (
                        observed[key]
                        for key in ("value", "text", "url", "count", "tabs", "stable")
                        if observed.get(key) not in (None, "", [], {})
                    ),
                    None,
                )
            else:
                value = observed
            if value in (None, "", [], {}):
                continue
            rendered = (
                json.dumps(value, ensure_ascii=False, default=str)
                if isinstance(value, (dict, list))
                else str(value)
            )
            values[field_name] = rendered[:500]
            provenance[field_name] = {
                "source": "browser_batch_interact",
                "raw_text": rendered[:600],
                "generation_id": generation_id,
            }
        return values, provenance

    @classmethod
    def _card_probe_evidence(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        cards = result.get("cards")
        if not isinstance(cards, list) or not cards:
            return {}
        compact_cards: list[Dict[str, Any]] = []
        fields: set[str] = set()
        for card in cards:
            compact = cls._compact_evidence_card(card, result)
            if compact is None:
                continue
            metadata_fields = {
                "generation_id",
                "result_index",
                "kind",
                "provenance",
                "field_status",
            }
            field_status = compact.get("field_status", {})
            for field_name in compact:
                if field_name in metadata_fields:
                    continue
                if field_status.get(field_name, "present") == "present":
                    fields.add(field_name)
            compact_cards.append(compact)
            if len(compact_cards) >= 5:
                break
        if not compact_cards:
            return {}
        return {
            "kind": "card_probe",
            "fields": sorted(fields),
            "cards": compact_cards,
            "observed_count": int(result.get("observed_count") or len(compact_cards)),
        }

    @classmethod
    def _compact_evidence_card(
        cls,
        card: Any,
        result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(card, dict) or not cls._is_natural_evidence_card(card):
            return None
        compact: Dict[str, Any] = {
            "generation_id": str(card.get("generation_id") or result.get("generation_id") or ""),
            "result_index": card.get("result_index"),
            "kind": card.get("kind"),
        }
        for field_name in CARD_EVIDENCE_FIELDS:
            value = card.get(field_name)
            if value in (None, "", [], {}):
                continue
            compact.setdefault(cls._canonical_field_name(field_name) or field_name, str(value)[:300])
        provenance = card.get("field_provenance")
        if isinstance(provenance, dict):
            compact["provenance"] = {
                cls._canonical_field_name(field_name) or str(field_name): dict(item)
                for field_name, item in list(provenance.items())[:20]
                if isinstance(item, dict)
            }
        statuses = card.get("field_status")
        if isinstance(statuses, dict):
            compact["field_status"] = {
                cls._canonical_field_name(field_name) or str(field_name): str(status)
                for field_name, status in list(statuses.items())[:20]
                if str(status).strip().lower() in {"present", "missing", "unknown"}
            }
        return compact

    @staticmethod
    def _is_natural_evidence_card(card: Dict[str, Any]) -> bool:
        region = str(card.get("region") or "")
        kind = str(card.get("kind") or "")
        if not (region or kind or "is_ad" in card):
            return True
        return (
            card.get("is_ad") is not True
            and region
            not in {
                "hot_search",
                "sidebar",
                "account",
                "chat",
                "activity",
                "sponsored_result",
                "resource",
                "commercial_module",
                "ai_overview",
                "question_module",
                "related",
            }
            and kind
            not in {
                "hot_search",
                "paid_column",
                "promotion",
                "activity",
                "account",
                "shop",
                "chat",
                "download_resource",
                "commercial_module",
                "ai_overview",
                "people_also_ask",
                "related_links",
                "knowledge_panel",
            }
        )

    def _clear_progress_state(self, session: Any) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        self._runtime.service.clear_progress_state(session_id)
        session.update_state(
            {
                _BROWSER_PROGRESS_STATE_KEY: {},
                _BROWSER_PROGRESS_TASK_KEY: "",
                _BROWSER_PHASE_STATE_KEY: {},
            }
        )

    @staticmethod
    def _image_input_supported(agent: Any) -> bool:
        return should_enable_read_image_multimodal(agent)
