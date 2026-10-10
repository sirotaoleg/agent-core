# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Page probes run as page scripts on the driver session; their targets go into the page state."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, AsyncIterator

from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.tools.browser_move.contract import ActionResult, DriverError, RunScript
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
    write_browser_agent_audit_artifact,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.probe_semantics import normalize_card_probe_payload
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
    build_interactive_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
    get_selector_cache,
    site_profiles_for_url,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import CommandRecord, ToolContext, ToolFlags
from openjiuwen.harness.tools.browser_move.utils.parsing import extract_json_object

_PROBE_INTERACTIVES_DESC = (
    "Return a compact list of visible, high-value interactive elements on the current page. "
    "Use this for page-level controls such as buttons, links, inputs, forms, navigation, login, "
    "pagination, menus, calendar dates, sort tabs, rating filters, and visible actions. "
    "Dynamic controls are returned as generation-scoped targets with region/kind semantics. "
    "The optional query filter is alias-aware for common "
    "search/input terms, including placeholders, aria labels, input type/name/id/class, and Chinese "
    "search text such as 搜索/关键词. Prefer max_items around 20-30 unless a larger inventory "
    "is needed. For product/search/listing card data, prefer browser_probe_cards first. "
    "The result includes compact PageState and generation-scoped target_id values that can be "
    "passed directly to browser_batch_interact without rebuilding CSS. It also includes "
    "role/text/region/kind plus match_count/visible/enabled/actionable. The model-facing "
    "result does not expose Probe-local ids or internal selectors."
)
_PROBE_INTERACTIVES_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_items": {
            "type": "integer",
            "description": "Maximum number of elements to return. Default 30, hard-capped at 40.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only return elements currently visible in the viewport. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional text filter, e.g. 'cart', 'search', 'next', or 'login'.",
        },
    },
    "required": [],
}
_PROBE_CARDS_DESC = (
    "Return compact repeated card/listing structures from the current page. "
    "Use this first on product pages, marketplace pages, search-result pages, catalog pages, "
    "article-list pages, table/list-row result pages, or any page with repeated visible cards/listings. "
    "The result includes compact PageState with generation-scoped card/control target_id values, "
    "candidate card title, author/source, summary/snippet, price, rating, "
    "review count, availability, primary link, and visible controls, "
    "region/kind/result_index/is_ad semantics that distinguish main results, accounts, sidebars, "
    "hot searches, shops, chats, and product links, "
    "match_count/visible/enabled/clickable/generation_id, recurring structure signatures, and "
    "cache diagnostics. Navigate primary_link/href directly instead of clicking a card hint. "
    "If this returns the fields needed "
    "for the task, including article/search-result title/link/author/source/summary fields, "
    "use the compact card result directly instead of taking screenshots/snapshots or running "
    "broad DOM evaluation. Only evaluate again when a required field is missing."
)
_PROBE_CARDS_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_cards": {
            "type": "integer",
            "description": "Maximum number of cards to return. Default 12, hard-capped at 20.",
        },
        "viewport_only": {
            "type": "boolean",
            "description": "When true, only inspect cards visible in the current viewport. Default true.",
        },
        "include_buttons": {
            "type": "boolean",
            "description": "When true, include visible buttons/links inside each card. Default true.",
        },
        "query": {
            "type": "string",
            "description": "Optional text filter, e.g. 'mouse', 'book', 'laptop', or 'cart'.",
        },
    },
    "required": [],
}

# The probe builders wrap the page function for a code runner that hands over the page; the driver runs page JS.
_WRAPPED_PROBE = re.compile(
    r"\Aasync \(page\) => \{\n  const params = (?P<params>.*?);\n\n"
    r"  return await page\.evaluate\((?P<function>\(params\) => \{.*\}), params\);\n\}\Z",
    re.DOTALL,
)
_FALSE_WORDS = frozenset({"0", "false", "no"})


def _page_script(wrapped: str) -> RunScript:
    match = _WRAPPED_PROBE.match(wrapped)
    if match is None:
        raise ValueError("not a wrapped page probe")
    return RunScript(code=match.group("function"), args=json.loads(match.group("params")))


async def _run_probe(
    context: ToolContext, action: RunScript, audit_kind: str
) -> tuple[dict[str, Any], dict[str, Any], int, ActionResult]:
    for attempt in range(2):
        result = await context.execute(action)
        audit = write_browser_agent_audit_artifact(audit_kind, result.value)
        if not result.ok:
            error = str(result.detail or "the page script did not run")[:1_000]
            return {"ok": False, "error": error}, audit, attempt, result
        parsed = extract_json_object(result.value)
        if parsed:
            return parsed, audit, attempt, result
    return {}, audit, 1, result


def _register_probe(page_state: BrowserPageState, payload: dict[str, Any]) -> None:
    _follow_address(page_state, payload)
    page_state.register_interactives(payload)


async def probe_for_policy(context: ToolContext, source: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Run a decision policy's ``(params) => ...`` page function and register its elements as targets."""
    page_state = context.page_state
    try:
        action = RunScript(code=source, args=dict(params))
        parsed, _audit, _retries, _result = await _run_probe(context, action, "policy_probe")
    except DriverError as error:
        tool_logger.warning("policy probe failed: %s", error)
        return _policy_failure(page_state, f"policy probe failed: {error}")
    if not parsed or parsed.get("ok") is False:
        return _policy_failure(page_state, str(parsed.get("error") or "policy probe returned no result"))
    parsed.setdefault("ok", True)
    parsed.setdefault("elements", [])
    _register_probe(page_state, parsed)
    parsed["generation_id"] = page_state.generation_id
    return parsed


class ProbeInteractivesTool(Tool):
    """``browser_probe_interactives`` as one page script."""

    flags = ToolFlags()

    def __init__(self, context: ToolContext) -> None:
        super().__init__(
            ToolCard(
                name="browser_probe_interactives",
                description=_PROBE_INTERACTIVES_DESC,
                input_params=_PROBE_INTERACTIVES_PARAMS,
            )
        )
        self._context = context

    async def invoke(self, inputs: Mapping[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        inputs = inputs or {}
        page_state = self._context.page_state
        query = str(inputs.get("query") or "").strip()
        action = _page_script(
            build_interactive_probe_js(
                max_items=_count(inputs.get("max_items"), default=30, maximum=30),
                viewport_only=_flag(inputs.get("viewport_only", True)),
                query=query,
                site_profiles=site_profiles_for_url(page_state.url),
                generation_id=page_state.generation_id,
            )
        )
        try:
            parsed, audit, retries, result = await _run_probe(self._context, action, "interactive_probe")
        except DriverError as error:
            failure = _failure(f"browser_probe_interactives failed: {error}", "elements", page_state.export())
            return _output(failure, CommandRecord.failed(action, error, page_state, effect=self.flags.effect))
        record = CommandRecord.of(action, result, effect=self.flags.effect, value=result.value)
        if not parsed or parsed.get("ok") is False:
            reason = parsed.get("error") or "Could not parse browser_probe_interactives result JSON"
            return _output({**_failure(reason, "elements", page_state.export_summary()), "audit": audit}, record)
        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("elements", [])
        _register_probe(page_state, parsed)
        exported = page_state.export()
        return _output(
            {
                "ok": bool(parsed.get("ok")),
                "error": parsed.get("error"),
                "url": parsed.get("url") or exported.get("url"),
                "title": parsed.get("title") or exported.get("title"),
                "generation_id": exported["generation_id"],
                "count": len(exported["interactives"]),
                "elements": exported["interactives"],
                "page_state": page_state.export_summary(),
                "diagnostics": {
                    "query": query[:160],
                    "query_widened": bool(parsed.get("query_widened")),
                    "total_candidates": int(parsed.get("total_candidates") or 0),
                    "parse_retry_count": retries,
                },
                "audit": audit,
            },
            record,
        )

    async def stream(self, inputs: Mapping[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)


class ProbeCardsTool(Tool):
    """``browser_probe_cards`` as one page script; results feed the selector cache as before."""

    flags = ToolFlags()

    def __init__(self, context: ToolContext) -> None:
        super().__init__(
            ToolCard(name="browser_probe_cards", description=_PROBE_CARDS_DESC, input_params=_PROBE_CARDS_PARAMS)
        )
        self._context = context

    async def invoke(self, inputs: Mapping[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        inputs = inputs or {}
        page_state = self._context.page_state
        selector_cache = get_selector_cache(self._context.settings.selector_cache_path)
        action = _page_script(
            build_card_probe_js(
                max_cards=_count(inputs.get("max_cards"), default=12, maximum=12),
                viewport_only=_flag(inputs.get("viewport_only", True)),
                include_buttons=_flag(inputs.get("include_buttons", True)),
                query=str(inputs.get("query") or "").strip(),
                site_profiles=site_profiles_for_url(page_state.url),
                selector_cache_records=selector_cache.export_for_probe(),
                generation_id=page_state.generation_id,
            )
        )
        try:
            parsed, audit, retries, result = await _run_probe(self._context, action, "card_probe")
        except DriverError as error:
            failure = _failure(f"browser_probe_cards failed: {error}", "cards", page_state.export())
            return _output(failure, CommandRecord.failed(action, error, page_state, effect=self.flags.effect))
        record = CommandRecord.of(action, result, effect=self.flags.effect, value=result.value)
        if not parsed or parsed.get("ok") is False:
            reason = parsed.get("error") or "Could not parse browser_probe_cards result JSON"
            return _output({**_failure(reason, "cards", page_state.export_summary()), "audit": audit}, record)
        parsed.setdefault("ok", True)
        parsed.setdefault("error", None)
        parsed.setdefault("cards", [])
        _follow_address(page_state, parsed)
        normalize_card_probe_payload(parsed)
        page_state.register_cards(parsed)
        _register_primary_links(page_state, parsed)
        parsed["page_state"] = page_state.export()
        _remember_selectors(selector_cache, parsed)
        exported = page_state.export()
        return _output(
            {
                "ok": bool(parsed.get("ok")),
                "error": parsed.get("error"),
                "url": parsed.get("url") or exported.get("url"),
                "title": parsed.get("title") or exported.get("title"),
                "generation_id": exported["generation_id"],
                "count": len(exported["cards"]),
                "observed_count": int(parsed.get("observed_count") or 0),
                "cards": exported["cards"],
                "page_state": page_state.export_summary(),
                "diagnostics": {
                    **(parsed.get("diagnostics") or parsed.get("cache_diagnostics") or {}),
                    "parse_retry_count": retries,
                },
                "audit": audit,
            },
            record,
        )

    async def stream(self, inputs: Mapping[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)


def _follow_address(page_state: BrowserPageState, payload: dict[str, Any]) -> None:
    # Not every driver reports the address with a script result; the address the page reports moves the generation.
    address = str(payload.get("url") or "").strip()
    if address and page_state.url not in ("", address):
        page_state.reset()
    _stamp_generation(payload, page_state.generation_id)


def _stamp_generation(value: Any, generation_id: str) -> None:
    if isinstance(value, dict):
        if "selector_hint" in value or "generation_id" in value:
            value["generation_id"] = generation_id
        for nested in value.values():
            _stamp_generation(nested, generation_id)
    elif isinstance(value, list):
        for nested in value:
            _stamp_generation(nested, generation_id)


def _register_primary_links(page_state: BrowserPageState, payload: dict[str, Any]) -> None:
    for card in payload.get("cards") or ():
        if not isinstance(card, dict):
            continue
        href = str(card.get("primary_link") or card.get("href") or "").strip()
        if not href:
            continue
        for key in ("selector_hint", "primary_link_selector_hint"):
            selector = str(card.get(key) or "").strip()
            if selector:
                page_state.selector_primary_links[selector] = (page_state.generation, href)


def _remember_selectors(selector_cache: Any, parsed: dict[str, Any]) -> None:
    # The cache is an optimisation; a failure to record must not fail the probe.
    records = [selector_cache.record_card_probe_cache_rejection]
    if parsed.get("cards"):
        records.append(selector_cache.record_card_probe_result)
    for record in records:
        try:
            record(parsed)
        except Exception:  # noqa: BLE001
            tool_logger.debug("card probe selector cache not updated", exc_info=True)


def _policy_failure(page_state: BrowserPageState, error: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "elements": [], "page_state": page_state.export()}


def _failure(error: Any, items: str, page_state: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "error": error, items: [], "page_state": page_state}


def _output(payload: dict[str, Any], record: CommandRecord) -> ToolOutput:
    data = {"content": payload, "command": record}
    return ToolOutput(success=bool(payload.get("ok", True)), data=data, error=payload.get("error"))


def _count(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        number = default
    return max(1, min(maximum, number))


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_WORDS
    return bool(value)
