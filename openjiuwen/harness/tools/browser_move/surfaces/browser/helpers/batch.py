# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``browser_batch_interact``: the steps a model plans, each run as commands on the driver session."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    Click,
    ConnectionLost,
    DriverError,
    DriverRef,
    Effect,
    Locator,
    Observation,
    Observe,
    PressKey,
    Ref,
    RunScript,
    SwitchContext,
    TypeText,
    Unsupported,
    Wait,
)
from openjiuwen.harness.tools.browser_move.contract.masking import masked
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import CommandRecord, ToolContext, ToolFlags
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    FieldKind,
    FillForm,
    FormField,
    Navigate,
    SelectOption,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers.batch_steps import (
    CONDITION_OPS,
    DESCRIPTION,
    MODEL_LOCATOR_KEYS,
    MUTATING_OPS,
    OPTION_VALUE_KEYS,
    PARAMETERS,
    PRIMARY_TARGET_OPS,
    READ_OPS,
    given,
    op_of,
    step_errors,
    text_of,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState, BrowserTarget
from openjiuwen.harness.tools.browser_move.surfaces.browser.policy import policy_denial, policy_rules

_ACTION = "browser_batch_interact"
_OBSERVED_KEYS = ("url", "text", "value", "count", "stable", "tabs")
_EFFECT_ORDER = (Effect.READ, Effect.SCRIPT, Effect.ACT, Effect.NAVIGATE)
_ERROR_HEADING = "### Error\n"
_POLL_S = 0.1
_TRANSIENT = re.compile(r"detached|not attached|intercept|not stable|outside of the viewport", re.IGNORECASE)
_MARK = "data-openjiuwen-target-id"
_NOTHING = object()

_STAMP_JS = "(element, mark) => { element.setAttribute('data-openjiuwen-target-id', mark); return true; }"

_LOCATE_JS = """(input) => {
  const SKIPPED = new Set(['HTML', 'HEAD', 'SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TITLE']);
  const BUTTON_INPUTS = new Set(['button', 'submit', 'reset', 'image']);
  const FIELD_TAGS = ['INPUT', 'TEXTAREA', 'SELECT'];
  const INPUT_ROLES = {checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton', search: 'searchbox',
    hidden: '', file: ''};
  const TAG_ROLES = {BUTTON: 'button', TEXTAREA: 'textbox', OPTION: 'option', IMG: 'img', LI: 'listitem', UL: 'list',
    OL: 'list', NAV: 'navigation', MAIN: 'main', FORM: 'form', TABLE: 'table', TR: 'row', TD: 'cell',
    TH: 'columnheader', DIALOG: 'dialog', H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading', H5: 'heading',
    H6: 'heading'};
  const norm = (value) => String(value ?? '').replace(/\\s+/g, ' ').trim();
  const matches = (actual, wanted, exact) => exact
    ? norm(actual) === norm(wanted)
    : norm(actual).toLowerCase().includes(norm(wanted).toLowerCase());
  const shown = (element) => {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const label = (element) => {
    const ids = element.getAttribute('aria-labelledby');
    if (ids) return ids.split(/\\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ');
    return element.getAttribute('aria-label')
      || Array.from(element.labels || []).map((item) => item.textContent).join(' ');
  };
  const role = (element) => {
    const explicit = (element.getAttribute('role') || '').trim().split(/\\s+/)[0];
    if (explicit) return explicit;
    if (element.tagName === 'INPUT') {
      if (BUTTON_INPUTS.has(element.type)) return 'button';
      if (element.type in INPUT_ROLES) return INPUT_ROLES[element.type];
      return element.hasAttribute('list') ? 'combobox' : 'textbox';
    }
    if (element.tagName === 'A') return element.hasAttribute('href') ? 'link' : '';
    if (element.tagName === 'SELECT') return element.multiple || element.size > 1 ? 'listbox' : 'combobox';
    return TAG_ROLES[element.tagName] || '';
  };
  const text = (element) => element.tagName === 'INPUT' && BUTTON_INPUTS.has(element.type)
    ? element.value
    : element.innerText;
  const name = (element) => norm(label(element)) || norm(
    FIELD_TAGS.includes(element.tagName) && !BUTTON_INPUTS.has(element.type)
      ? element.getAttribute('placeholder') || element.getAttribute('title')
      : element.getAttribute('alt') || text(element) || element.getAttribute('title'));
  const elements = () => Array.from(document.querySelectorAll('*')).filter((element) => !SKIPPED.has(element.tagName));
  const find = (query) => {
    if (query.css) return Array.from(document.querySelectorAll(query.css));
    if (query.testid) return elements().filter((element) => element.getAttribute('data-testid') === query.testid);
    if (query.placeholder) {
      return elements().filter((element) => element.hasAttribute('placeholder')
        && matches(element.getAttribute('placeholder'), query.placeholder, query.exact));
    }
    if (query.label) {
      return elements().filter((element) => element.tagName !== 'LABEL'
        && matches(label(element), query.label, query.exact));
    }
    if (query.role) {
      return elements().filter((element) => role(element) === query.role && shown(element)
        && !element.closest('[aria-hidden="true"]')
        && (!query.name || matches(name(element), query.name, query.exact)));
    }
    const hits = elements().filter((element) => matches(text(element), query.text, query.exact));
    return hits.filter((element) => !hits.some((other) => other !== element && element.contains(other)));
  };
  return input.queries.map((query) => {
    let found;
    try { found = find(query); } catch (error) { return {count: 0, error: String(error)}; }
    const element = found[0];
    const visible = Boolean(element) && shown(element);
    const enabled = Boolean(element) && !element.disabled && element.getAttribute('aria-disabled') !== 'true';
    const result = {count: found.length, visible, enabled};
    if (found.length !== 1 || !visible || (query.enabled && !enabled)) return result;
    if (query.mark) element.setAttribute('data-openjiuwen-target-id', query.mark);
    if (query.focus) element.focus();
    if (query.read === 'text') result.text = element.innerText;
    if (query.read === 'value') {
      if (FIELD_TAGS.includes(element.tagName)) result.value = element.value;
      else result.error = 'Not an input, textarea or select element';
    }
    if (query.option !== null && query.option !== undefined) {
      const option = element.options ? element.options[query.option] : undefined;
      if (option) result.value = option.value;
      else result.error = 'no option at index ' + query.option;
    }
    return result;
  });
}"""

_CONDITION_JS = """(input) => {
  const norm = (value, limit) => String(value ?? '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const shown = (element) => {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const nodes = input.selector ? Array.from(document.querySelectorAll(input.selector)) : [];
  if (input.kind === 'selector') {
    const visible = nodes.length > 0 && shown(nodes[0]);
    const states = {attached: nodes.length > 0, detached: nodes.length === 0, visible, hidden: !visible};
    return {ok: Boolean(states[input.state]), value: input.state};
  }
  if (input.kind === 'text') {
    const wanted = norm(input.text);
    if (!input.exact) {
      const body = document.body ? norm(document.body.innerText) : '';
      return {ok: body.toLowerCase().includes(wanted.toLowerCase()), value: wanted};
    }
    const found = Array.from(document.querySelectorAll('body *'))
      .some((element) => norm(element.innerText) === wanted && shown(element));
    return {ok: found, value: wanted};
  }
  if (input.kind === 'load') {
    const state = document.readyState;
    const ok = ['load', 'networkidle'].includes(input.state) ? state === 'complete' : state !== 'loading';
    return {ok, value: state};
  }
  if (input.kind === 'first_card_title') {
    if (!nodes.length) return {ok: false, value: ''};
    const title = norm(nodes[0].innerText, 300);
    return {ok: Boolean(title) && (!input.expected || title.includes(input.expected)), value: title};
  }
  if (input.kind === 'sort_state') {
    if (nodes.length !== 1) return {ok: false, value: ''};
    const value = input.attribute === 'text'
      ? norm(nodes[0].innerText, 300)
      : String(nodes[0].getAttribute(input.attribute) || '');
    return {ok: value === input.expected, value};
  }
  if (input.kind === 'result_count') {
    const count = nodes.length;
    const ok = input.count !== null
      ? count === input.count
      : (input.min === null || count >= input.min) && (input.max === null || count <= input.max);
    return {ok, value: count};
  }
  if (input.kind === 'dom_text_change') {
    if (nodes.length !== 1) return {ok: false, value: ''};
    const value = norm(nodes[0].innerText, input.max_chars);
    return {ok: value !== input.previous, value};
  }
  if (input.selector && !nodes.length) return {ok: false, value: ''};
  const source = input.selector ? nodes[0] : document.body;
  return {ok: true, value: norm(source ? source.innerText : '', input.max_chars)};
}"""

_REFRESH_JS = """(selector) => {
  let nodes = [];
  try { nodes = Array.from(document.querySelectorAll(selector)); }
  catch (error) { return {ok: false, error: String(error)}; }
  const element = nodes[0];
  if (!element) return {ok: true, match_count: nodes.length, visible: false};
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  const visible = Boolean(element.isConnected && rect.width > 0 && rect.height > 0 &&
    style.display !== 'none' && style.visibility !== 'hidden');
  const enabled = !element.disabled && element.getAttribute('aria-disabled') !== 'true';
  const hit = visible ? document.elementFromPoint(
    Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2)),
    Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
  ) : null;
  const actionable = Boolean(visible && enabled && hit && (hit === element || element.contains(hit)));
  return {ok: true, match_count: nodes.length, visible, enabled, actionable};
}"""


class _StepError(Exception):
    def __init__(self, message: str, position: int = 0) -> None:
        super().__init__(message)
        self.position = position


@dataclass(frozen=True)
class _Aim:
    query: Mapping[str, Any] | None = None
    ref: Ref | None = None
    target_id: str = ""


@dataclass(frozen=True)
class _Step:
    index: int
    op: str
    spec: Mapping[str, Any]
    primary: _Aim | None = None
    option: _Aim | None = None
    navigate_to: str = ""


@dataclass(frozen=True)
class _Timing:
    action_ms: int
    condition_ms: int
    after_ms: int
    total_ms: int

    @classmethod
    def of(cls, inputs: Mapping[str, Any], steps: list[_Step]) -> _Timing:
        action_ms = max(250, min(30_000, _int(inputs.get("timeout_ms"), 2500)))
        condition_ms = max(action_ms, min(30_000, _int(inputs.get("condition_timeout_ms"), 10_000)))
        after_ms = max(0, min(5000, _int(inputs.get("wait_after_each_ms"), 0)))
        if inputs.get("global_timeout_ms") is None:
            conditions = sum(step.op in CONDITION_OPS for step in steps)
            computed = (len(steps) - conditions) * (action_ms + after_ms + 250)
            computed += conditions * (condition_ms + after_ms + 250) + 5000
            total_ms = min(120_000, max(5000, computed))
        else:
            total_ms = max(1000, min(180_000, _int(inputs.get("global_timeout_ms"), 120_000)))
        return cls(action_ms, condition_ms, after_ms, total_ms)

    def step_s(self, step: _Step) -> float:
        fallback = self.condition_ms if step.op in CONDITION_OPS else self.action_ms
        return max(250, _int(step.spec.get("timeout_ms"), fallback)) / 1000


class BatchTool(Tool):
    """``browser_batch_interact``: one command per step, after the step targets were checked on the page."""

    flags = ToolFlags(
        effect=Effect.ACT,
        requires=(Click, TypeText, PressKey, SelectOption, FillForm, Navigate, Wait, SwitchContext, RunScript),
    )

    def __init__(self, context: ToolContext) -> None:
        super().__init__(ToolCard(name=_ACTION, description=DESCRIPTION, input_params=PARAMETERS))
        self._context = context

    async def invoke(self, inputs: Mapping[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        run = _Run(self._context, inputs or {})
        payload = await run.payload()
        record = run.record(payload)
        data = {"content": payload} if record is None else {"content": payload, "command": record}
        return ToolOutput(success=bool(payload.get("ok")), data=data, error=payload.get("error"))

    async def stream(self, inputs: Mapping[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)


class _Run:
    def __init__(self, context: ToolContext, inputs: Mapping[str, Any]) -> None:
        self._context = context
        self._inputs = inputs
        self._page_state = context.page_state
        self._last: Action | None = None
        self._effect = Effect.READ
        self._dispatched = False
        self._context_changed = False
        self._commands = 0

    async def payload(self) -> dict[str, Any]:
        page_state = self._page_state
        steps = self._inputs.get("steps")
        errors = step_errors(steps)
        if errors:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_validation_failed: {errors[0]}",
                "validation_errors": errors,
                "page_state": page_state.export(),
            }
        try:
            specs, recovered = await self._refreshed(steps, str(self._inputs.get("generation_id") or ""))
            planned = [self._planned(index, spec, single=len(specs) == 1) for index, spec in enumerate(specs)]
            command = _single_command(planned[0]) if len(planned) == 1 else None
            if command is None:
                planned = [await self._materialized(step) for step in planned]
        except (ValueError, DriverError, _StepError) as error:
            return {
                "ok": False,
                "status": "failed",
                "error": f"batch_target_validation_failed: {error}",
                "generation_id": page_state.generation_id,
                "current_generation": page_state.generation_id,
                "candidate_fresh_targets": _candidates(page_state, steps),
                "page_state": page_state.export(),
            }
        payload = await (self._single(planned[0], command) if command is not None else self._steps(planned))
        page_state.add_field_coverage(payload["extracted"].keys())
        payload["generation_id"] = page_state.generation_id
        if recovered:
            payload["generation_recovered_from"] = recovered
        payload["page_state"] = page_state.export()
        return payload

    def record(self, payload: Mapping[str, Any]) -> CommandRecord | None:
        if self._last is None:
            return None
        return CommandRecord(
            command=type(self._last),
            effect=self._effect,
            ok=bool(payload.get("ok")),
            dispatched=self._dispatched,
            context_changed=self._context_changed,
            surface=self._page_state.surface_context,
            value=payload,
            generation=self._page_state.generation_id,
            action=masked(self._last),
        )

    async def _execute(self, action: Action) -> ActionResult:
        denial = policy_denial(action, self._page_state.surface_context, policy_rules(self._context.settings))
        if denial:
            raise _StepError(denial)
        self._last = action
        try:
            result = await self._context.execute(action)
        except DriverError as error:
            if error.dispatched:
                self._ran(action)
            raise
        self._ran(action)
        self._context_changed = self._context_changed or result.context_changed
        if not result.ok:
            raise _StepError(_message(result.detail))
        return result

    async def _observe(self, command: Observe) -> Observation:
        self._last = command
        try:
            observation = await self._context.observe(command)
        except DriverError as error:
            if error.dispatched:
                self._ran(command)
            raise
        self._ran(command)
        return observation

    async def _tabs(self) -> BrowserContext:
        surface = (await self._observe(Observe())).surface
        if not isinstance(surface, BrowserContext):
            raise _StepError("the observation reported no browser tabs")
        return surface

    def _ran(self, action: Action) -> None:
        effect = Effect.READ if isinstance(action, RunScript) else action.effect
        self._effect = max(self._effect, effect, key=_EFFECT_ORDER.index)
        self._dispatched = True
        self._commands += 1

    async def _script(self, code: str, args: Any) -> Any:
        value = (await self._execute(RunScript(code=code, args=args))).value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    async def _refreshed(self, steps: list[Any], generation_id: str) -> tuple[list[dict[str, Any]], str]:
        specs = [dict(step) for step in steps]
        if generation_id.strip() == self._page_state.generation_id:
            return specs, ""
        refreshed_any = False
        for spec in specs:
            op = op_of(spec)
            refreshed = False
            for aliases in (("target_id",), ("option_target_id", "choose_target_id")):
                alias = next((key for key in aliases if text_of(spec, key)), "")
                if not alias:
                    continue
                try:
                    spec[alias] = self._page_state.refresh_target_id(str(spec[alias]))
                except ValueError:
                    spec[alias] = await self._rebound(str(spec[alias]), actionable=op in MUTATING_OPS)
                refreshed = True
            if any(given(spec, key) for key in ("ref", "option_ref", "choose_ref")):
                raise ValueError(
                    "Native AX refs cannot be refreshed across PageState generations; "
                    "capture the current snapshot again"
                )
            selected = any(given(spec, key) for key in ("selector", "option_selector", "choose_selector"))
            if selected and not refreshed and op not in CONDITION_OPS:
                raise ValueError(
                    "Model-authored selectors cannot refresh a stale PageState generation; "
                    "use a current generation-scoped target_id"
                )
            refreshed_any = refreshed_any or refreshed
        if not refreshed_any and not all(op_of(spec) in CONDITION_OPS for spec in specs):
            raise ValueError(
                f"Stale PageState generation {generation_id}; current generation is "
                f"{self._page_state.generation_id}. Probe or snapshot the current page again."
            )
        return specs, generation_id.strip()

    async def _rebound(self, target_id: str, *, actionable: bool) -> str:
        page_state = self._page_state
        stale = page_state.get_target(target_id)
        if stale is None:
            raise ValueError(f"Unknown PageState target_id: {target_id}")
        if stale.source == "ax":
            raise ValueError(f"Stale AX target_id {target_id} cannot cross generations; capture a current snapshot")
        if stale.href and not stale.selector:
            return page_state.rebind_target(target_id, visible=True, enabled=True, actionable=True)
        selector = str(stale.selector or stale.locator.get("selector") or "").strip()
        if not selector:
            raise ValueError(f"Stale target_id {target_id} has no runtime-owned selector to refresh")
        found = await self._script(_REFRESH_JS, selector)
        valid = (
            isinstance(found, Mapping)
            and found.get("ok") is True
            and found.get("match_count") == 1
            and found.get("visible") is True
            and (not actionable or found.get("actionable") is True)
        )
        if not valid:
            raise ValueError(
                f"Stale target_id {target_id} could not be refreshed in {page_state.generation_id}: "
                f"{found or 'validation unavailable'}"
            )
        return page_state.rebind_target(
            target_id,
            visible=bool(found.get("visible")),
            enabled=bool(found.get("enabled")),
            actionable=bool(found.get("actionable")),
        )

    def _planned(self, index: int, spec: Mapping[str, Any], *, single: bool) -> _Step:
        op = op_of(spec)
        primary, navigate_to = self._primary(spec, op, single=single)
        return _Step(index, op, spec, primary, self._option(spec, op), navigate_to)

    def _primary(self, spec: Mapping[str, Any], op: str, *, single: bool) -> tuple[_Aim | None, str]:
        selector = text_of(spec, "selector")
        target = self._target(op, text_of(spec, "target_id"), text_of(spec, "ref"), selector)
        if target is not None:
            if target.href and target.source in ("card", "card_primary_link") and op == "click":
                if single:
                    return None, target.href
                raise ValueError(
                    f"Target {target.target_id} has primary_link={target.href}; "
                    "call browser_navigate directly instead of clicking it in a batch"
                )
            return _aim_at(_actionable(target, op)), ""
        if selector:
            return _css_aim(selector, self._page_state.generation_id), ""
        query = _primary_query(spec)
        return (_Aim(query) if query else None), ""

    def _option(self, spec: Mapping[str, Any], op: str) -> _Aim | None:
        target_id = text_of(spec, "option_target_id") or text_of(spec, "choose_target_id")
        ref = text_of(spec, "option_ref") or text_of(spec, "choose_ref")
        selector = text_of(spec, "option_selector") or text_of(spec, "choose_selector")
        target = self._target(op, target_id, ref, selector)
        if target is not None:
            return _aim_at(_actionable(target, op))
        if selector:
            return _css_aim(selector, self._page_state.generation_id)
        query = _option_query(spec)
        return _Aim(query) if query else None

    def _target(self, op: str, target_id: str, ref: str, selector: str) -> BrowserTarget | None:
        if not (target_id or ref or selector):
            return None
        return self._page_state.resolve_target(
            generation_id=self._page_state.generation_id,
            target_id=target_id,
            ref=ref,
            selector=selector,
            require_registered_selector=op in MUTATING_OPS,
        )

    async def _materialized(self, step: _Step) -> _Step:
        return _Step(
            step.index,
            step.op,
            step.spec,
            await self._marked(step.primary),
            await self._marked(step.option),
            step.navigate_to,
        )

    async def _marked(self, aim: _Aim | None) -> _Aim | None:
        if aim is None or aim.query is not None or not isinstance(aim.ref, DriverRef):
            return aim
        try:
            await self._execute(RunScript(code=_STAMP_JS, args=aim.target_id, target=aim.ref))
        except (DriverError, _StepError) as error:
            raise ValueError(f"Failed to resolve AX ref {aim.ref.ref} for batch execution: {error}") from error
        selector = f'[{_MARK}="{aim.target_id}"]'
        self._page_state.update_target_locator(aim.target_id, {"selector": selector})
        return _Aim({"css": selector}, aim.ref, aim.target_id)

    async def _typed(self, action: TypeText) -> ActionResult:
        try:
            return await self._execute(action)
        except Unsupported as error:
            if error.dispatched or not action.slowly:
                raise
            return await self._execute(dataclasses.replace(action, slowly=False, clear=False))

    async def _single(self, step: _Step, command: Action) -> dict[str, Any]:
        started = time.perf_counter()
        error = ""
        try:
            if isinstance(command, Observe):
                await self._observe(command)
            elif isinstance(command, TypeText):
                await self._typed(command)
            else:
                await self._execute(command)
        except (DriverError, _StepError) as failure:
            error = str(failure)
        elapsed_ms = _elapsed_ms(started)
        ok = not error
        result: dict[str, Any] = {"index": 0, "op": step.op, "ok": ok, "status": _status(ok), "elapsed_ms": elapsed_ms}
        if error:
            result["error"] = error
        conditions = []
        if step.op == "wait_for_text":
            conditions.append({**result, "observed": {"text": str(step.spec.get("text") or "")}})
        return {
            "ok": ok,
            "status": _status(ok),
            "error": error or None,
            "action": _ACTION,
            "execution_mode": "primitive",
            "generation_id": self._page_state.generation_id,
            "steps": [result],
            "extracted": {},
            "conditions": conditions,
            "metrics": {"command": type(command).__name__, "executor_elapsed_ms": elapsed_ms},
        }

    async def _steps(self, steps: list[_Step]) -> dict[str, Any]:
        timing = _Timing.of(self._inputs, steps)
        generation_id = self._page_state.generation_id
        started = time.perf_counter()
        checked = [(step, aim) for step in steps for aim in _preflighted(step)]
        try:
            await self._ready(
                [_wanted(aim, enabled=step.op not in READ_OPS) for step, aim in checked],
                [time.monotonic() + timing.step_s(step) for step, _ in checked],
            )
        except (DriverError, _StepError) as error:
            step = checked[getattr(error, "position", 0)][0]
            item = {"index": step.index, "op": step.op, "ok": False, "phase": "preflight", "error": str(error)}
            item["elapsed_ms"] = _elapsed_ms(started)
            failure = f"batch_preflight_failed: steps[{step.index}] {error}"
            return self._summary([item], failure, generation_id, started, _elapsed_ms(started), len(checked))
        preflight_ms = _elapsed_ms(started)
        initial_tabs = 0
        if any(step.op == "wait_for_tab" and not step.spec.get("min_tabs") for step in steps):
            try:
                initial_tabs = len((await self._tabs()).tabs)
            except (DriverError, _StepError) as error:
                return self._summary([], str(error), generation_id, started, preflight_ms, len(checked))
        items: list[dict[str, Any]] = []
        error = None
        continue_on_error = bool(self._inputs.get("continue_on_error", False))
        for step in steps:
            if _elapsed_ms(started) > timing.total_ms:
                error = f"browser_batch_interact timed out after {timing.total_ms}ms"
                break
            item, failure = await self._item(step, timing, initial_tabs)
            items.append(item)
            if failure is None:
                continue
            if isinstance(failure, ConnectionLost) or not (step.spec.get("optional") or continue_on_error):
                error = item["error"]
                break
        return self._summary(items, error, generation_id, started, preflight_ms, len(checked))

    def _summary(
        self,
        items: list[dict[str, Any]],
        error: str | None,
        generation_id: str,
        started: float,
        preflight_ms: int,
        preflight_count: int,
    ) -> dict[str, Any]:
        ok = error is None and all(item["ok"] for item in items)
        status = "completed" if ok else "partial" if any(item["ok"] for item in items) else "failed"
        done = [item for item in items if item["ok"] and item.get("field")]
        return {
            "ok": ok,
            "status": status,
            "error": None if ok else error or "one or more batch steps failed",
            "action": _ACTION,
            "execution_mode": "commands",
            "session_id": str(self._inputs.get("session_id") or "").strip(),
            "request_id": str(self._inputs.get("request_id") or "").strip(),
            "generation_id": generation_id,
            "steps": [_compact(item) for item in items],
            "extracted": {item["field"]: item.get("text", item.get("value")) for item in done},
            "field_provenance": {
                item["field"]: {
                    "selector": item["selector"][:600],
                    "raw_text": item["raw_text"][:1000],
                    "generation_id": generation_id,
                }
                for item in done
            },
            "conditions": [_condition_entry(item) for item in items if item["op"] in CONDITION_OPS],
            "metrics": {
                "executor_elapsed_ms": _elapsed_ms(started),
                "internal_steps_elapsed_ms": sum(item["elapsed_ms"] for item in items if "phase" not in item),
                "preflight_elapsed_ms": preflight_ms,
                "preflight_target_count": preflight_count,
                "command_count": self._commands,
            },
        }

    async def _item(
        self, step: _Step, timing: _Timing, initial_tabs: int
    ) -> tuple[dict[str, Any], DriverError | _StepError | None]:
        item: dict[str, Any] = {"index": step.index, "op": step.op, "ok": False}
        started = time.perf_counter()
        failure: DriverError | _StepError | None = None
        try:
            await self._perform(step, timing.step_s(step), item, initial_tabs)
            item["ok"] = True
            after = step.spec.get("wait_after_ms")
            pause_ms = max(0, _int(after, 0)) if after is not None else timing.after_ms
            if pause_ms:
                await asyncio.sleep(pause_ms / 1000)
        except (DriverError, _StepError) as error:
            failure = error
            item["error"] = str(error)
        item["elapsed_ms"] = _elapsed_ms(started)
        return item, failure

    async def _perform(self, step: _Step, timeout_s: float, item: dict[str, Any], initial_tabs: int) -> None:
        op, spec = step.op, step.spec
        if op == "click":
            await self._click(step.primary, timeout_s)
        elif op in ("fill", "type"):
            locator, _ = await self._locate(step.primary, timeout_s)
            value = str(_first(spec, "value", "text_value") or "")
            if op == "type" or spec.get("mode") == "type":
                await self._type_slowly(locator, value)
            else:
                await self._execute(TypeText(locator, value))
        elif op == "autocomplete":
            locator, _ = await self._locate(step.primary, timeout_s)
            value = str(_first(spec, "value", "query", "text_value") or "")
            if spec.get("fill_first"):
                await self._execute(Click(locator))
                await self._execute(TypeText(locator, value))
            else:
                await self._type_slowly(locator, value)
            if spec.get("wait_after_type_ms") is not None:
                await asyncio.sleep(max(0, _int(spec.get("wait_after_type_ms"), 0)) / 1000)
            await self._click(step.option, timeout_s, missing=_NO_OPTION)
        elif op == "select_visible_text":
            await self._click(step.option, timeout_s, missing=_NO_OPTION)
        elif op == "press":
            if step.primary is not None:
                await self._locate(step.primary, timeout_s, focus=True)
            await self._execute(PressKey(str(spec.get("key") or "Enter")))
        elif op == "select_option":
            await self._select(step, timeout_s)
        elif op == "set_checked":
            locator, _ = await self._locate(step.primary, timeout_s)
            checked = "false" if spec.get("checked") is not None and not spec.get("checked") else "true"
            await self._execute(FillForm((FormField(locator, FieldKind.CHECKBOX, checked),)))
        elif op == "wait_for_url":
            await self._wait_for_url(spec, timeout_s, item)
        elif op == "wait_for_tab":
            await self._wait_for_tab(spec, timeout_s, item, initial_tabs)
        elif op in CONDITION_OPS:
            await self._wait(op, spec, timeout_s, item)
        elif op == "sleep":
            await self._execute(Wait(seconds=max(0, _int(spec.get("ms") or spec.get("time_ms"), 0)) / 1000))
        elif op in READ_OPS:
            read = "text" if op == "extract_text" else "value"
            _, found = await self._locate(step.primary, timeout_s, enabled=False, read=read)
            raw = str(found.get(read) or "")
            if op == "extract_text":
                item["text"] = _compact_text(raw, _int(spec.get("max_chars"), 500))
            else:
                item["value"] = found.get(read)
            item["raw_text"] = raw[:1000]
            item["selector"] = str(spec.get("selector") or "")
            item["field"] = str(
                spec.get("field") or spec.get("name") or spec.get("description") or f"field_{step.index + 1}"
            )
        else:
            await self._observe(Observe(include_screenshot=True, full_page=bool(spec.get("full_page"))))

    async def _click(self, aim: _Aim | None, timeout_s: float, *, missing: str = "") -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        for attempt in range(2):
            locator, _ = await self._locate(aim, max(0.25, deadline - loop.time()), missing=missing)
            try:
                await self._execute(Click(locator))
                return
            except (DriverError, _StepError) as error:
                if attempt or not _TRANSIENT.search(str(error)) or loop.time() >= deadline:
                    raise
            await asyncio.sleep(min(_POLL_S, max(0.0, deadline - loop.time())))

    async def _type_slowly(self, locator: Locator, value: str) -> None:
        await self._execute(Click(locator))
        await self._execute(TypeText(locator, ""))
        await self._typed(TypeText(locator, value, clear=False, slowly=True))

    async def _select(self, step: _Step, timeout_s: float) -> None:
        spec = step.spec
        values = spec.get("values")
        by_index = all(spec.get(key) is None for key in OPTION_VALUE_KEYS if key != "index")
        locator, found = await self._locate(
            step.primary, timeout_s, option=_int(spec.get("index"), 0) if by_index else None
        )
        if values is not None:
            chosen = tuple(str(value) for value in values) if isinstance(values, list) else (str(values),)
        elif _first(spec, "option_value", "value") is not None:
            chosen = (str(_first(spec, "option_value", "value")),)
        elif _first(spec, "label_value", "option_label", "option_text", "choose_text") is not None:
            chosen = (str(_first(spec, "label_value", "option_label", "option_text", "choose_text")),)
        else:
            chosen = (str(found.get("value")),)
        await self._execute(SelectOption(locator, chosen))

    async def _wait(self, op: str, spec: Mapping[str, Any], timeout_s: float, item: dict[str, Any]) -> None:
        query, stable_ms, field = _condition_query(op, spec)
        value = await self._poll(lambda: self._condition(query), timeout_s, _interval_s(spec), stable_ms / 1000)
        if field:
            item[field] = True if field == "stable" else value

    async def _condition(self, query: Mapping[str, Any]) -> tuple[bool, Any]:
        found = await self._script(_CONDITION_JS, dict(query))
        if not isinstance(found, Mapping):
            raise _StepError("the page script returned no condition result")
        return bool(found.get("ok")), found.get("value")

    async def _wait_for_url(self, spec: Mapping[str, Any], timeout_s: float, item: dict[str, Any]) -> None:
        wanted = _url_matcher(spec)
        seen: dict[str, Any] = {}

        async def check() -> tuple[bool, Any]:
            surface = seen["surface"] = await self._tabs()
            for index, url in _tab_candidates(surface):
                if wanted(url):
                    return True, (index, url)
            return False, surface.url

        index, url = await self._poll(check, timeout_s, _interval_s(spec))
        surface = seen["surface"]
        if index is not None and index != surface.current_tab:
            await self._execute(SwitchContext(surface.tabs[index]))
            item["tab_switched"] = True
        item["url"] = url

    async def _wait_for_tab(
        self, spec: Mapping[str, Any], timeout_s: float, item: dict[str, Any], initial_tabs: int
    ) -> None:
        min_tabs = max(1, _int(spec.get("min_tabs"), 0) or initial_tabs + 1)
        wanted = _url_matcher(spec)
        title = str(spec.get("title_contains") or "")
        seen: dict[str, Any] = {}

        async def check() -> tuple[bool, Any]:
            surface = seen["surface"] = await self._tabs()
            tabs = [
                {"index": index, "url": _tab_url(surface, index), "title": tab.title or ""}
                for index, tab in enumerate(surface.tabs)
            ]
            matches = [tab["index"] for tab in tabs if wanted(tab["url"]) and title in tab["title"]]
            matched = len(tabs) >= min_tabs and bool(matches)
            return matched, (tabs, matches[-1] if matched else -1)

        stable_s = _int(spec.get("stable_ms"), 0) / 1000
        tabs, index = await self._poll(check, timeout_s, _interval_s(spec), stable_s)
        item["tabs"] = tabs
        item["count"] = len(tabs)
        if spec.get("activate") is not False and index >= 0:
            await self._execute(SwitchContext(seen["surface"].tabs[index]))
            item["url"] = tabs[index]["url"]

    async def _poll(
        self, check: Callable[[], Awaitable[tuple[bool, Any]]], timeout_s: float, interval_s: float, stable_s: float = 0
    ) -> Any:
        loop = asyncio.get_running_loop()
        started = loop.time()
        last: Any = _NOTHING
        since = started
        while loop.time() - started <= timeout_s:
            ok, value = await check()
            now = loop.time()
            if ok and stable_s <= 0:
                return value
            if ok and value == last:
                if now - since >= stable_s:
                    return value
            elif ok:
                last, since = value, now
            else:
                last = _NOTHING
            await asyncio.sleep(interval_s)
        raise _StepError(f"condition timed out after {round(timeout_s * 1000)}ms")

    async def _locate(
        self,
        aim: _Aim | None,
        timeout_s: float,
        *,
        enabled: bool = True,
        focus: bool = False,
        read: str | None = None,
        option: int | None = None,
        missing: str = "",
    ) -> tuple[Locator, Mapping[str, Any]]:
        if aim is None or aim.query is None:
            raise _StepError(missing or "step needs selector, role, label, placeholder, testid, or text")
        mark = uuid.uuid4().hex[:12]
        wanted = {**_wanted(aim, enabled=enabled), "mark": mark, "focus": focus, "read": read, "option": option}
        found = (await self._ready([wanted], [time.monotonic() + timeout_s]))[0]
        generation = self._page_state.generation_id
        return Locator(css=f'[{_MARK}="{mark}"]', text=None, generation=generation), found

    async def _ready(self, queries: list[Mapping[str, Any]], deadlines: list[float]) -> list[Mapping[str, Any]]:
        while queries:
            found = await self._script(_LOCATE_JS, {"queries": queries})
            if not isinstance(found, list) or len(found) != len(queries):
                raise _StepError("the page script returned no target result")
            problems = [_problem(query, item) for query, item in zip(queries, found)]
            for position, (problem, final) in enumerate(problems):
                if problem and (final or time.monotonic() >= deadlines[position]):
                    raise _StepError(problem, position)
            if not any(problem for problem, _ in problems):
                return found
            await asyncio.sleep(_POLL_S)
        return []


_NO_OPTION = "autocomplete/select_visible_text needs choose_text/option_text or option selector"


def _single_command(step: _Step) -> Action | None:
    op, spec = step.op, step.spec
    ref = step.primary.ref if step.primary is not None else None
    if op == "click" and step.navigate_to:
        return Navigate(step.navigate_to)
    if op == "click" and ref is not None:
        return Click(ref)
    if op in ("fill", "type") and ref is not None:
        slowly = op == "type" or _int(spec.get("delay_ms"), 0) > 0
        return TypeText(ref, str(spec.get("value") or ""), slowly=slowly)
    if op == "select_option" and ref is not None:
        values = spec.get("values")
        if values is None:
            values = spec.get("option_value", spec.get("value"))
        if values is not None:
            return SelectOption(ref, tuple(str(value) for value in (values if isinstance(values, list) else [values])))
    if op == "press" and not any(spec.get(key) for key in MODEL_LOCATOR_KEYS):
        return PressKey(str(spec.get("key") or "Enter"))
    if op == "wait_for_text":
        return Wait(until_text=str(spec.get("text") or ""))
    if op == "sleep":
        return Wait(seconds=max(0, _int(spec.get("ms", spec.get("time_ms", 0)), 0)) / 1000)
    if op == "screenshot":
        return Observe(include_screenshot=True, full_page=bool(spec.get("full_page", False)))
    return None


def _actionable(target: BrowserTarget, op: str) -> BrowserTarget:
    available = target.visible and target.enabled and target.actionable
    if op in MUTATING_OPS and target.source != "ax" and not available:
        raise ValueError(f"PageState target {target.target_id} is not actionable in {target.generation_id}")
    return target


def _aim_at(target: BrowserTarget) -> _Aim:
    if target.ref:
        return _Aim(None, DriverRef(target.ref, target.generation_id), target.target_id)
    selector = target.selector or target.locator.get("selector", "")
    if not selector:
        raise ValueError(f"PageState target has no executable locator: {target.target_id}")
    return _Aim({"css": selector}, Locator(css=selector, text=None, generation=target.generation_id), target.target_id)


def _css_aim(selector: str, generation: str) -> _Aim:
    return _Aim({"css": selector}, Locator(css=selector, text=None, generation=generation))


def _primary_query(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    exact = bool(spec.get("exact"))
    if given(spec, "role"):
        return {"role": str(spec["role"]), "name": str(spec.get("name") or ""), "exact": exact}
    for key in ("label", "placeholder", "testid", "text"):
        if given(spec, key):
            return {key: str(spec[key]), "exact": exact}
    return None


def _option_query(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    exact = bool(spec.get("exact"))
    role = _first(spec, "option_role", "choose_role")
    if role:
        name = _first(spec, "option_name", "choose_name", "choose_text", "option_text")
        return {"role": str(role), "name": "" if name is None else str(name), "exact": exact}
    chosen = _first(spec, "choose_text", "option_text", "text_to_choose", "value")
    return {"text": str(chosen), "exact": exact} if chosen not in (None, "") else None


def _preflighted(step: _Step) -> list[_Aim]:
    aims = []
    if step.op in PRIMARY_TARGET_OPS or (step.op == "press" and step.primary is not None):
        aims.append(step.primary)
    if step.op == "select_visible_text" or (step.op == "autocomplete" and step.option and step.option.target_id):
        aims.append(step.option)
    return [aim for aim in aims if aim is not None and aim.query is not None]


def _wanted(aim: _Aim, *, enabled: bool) -> dict[str, Any]:
    return {**(aim.query or {}), "enabled": enabled}


def _problem(query: Mapping[str, Any], found: Any) -> tuple[str, bool]:
    if not isinstance(found, Mapping):
        return "the page script returned no target result", True
    if found.get("error"):
        return str(found["error"]), True
    count = int(found.get("count") or 0)
    if count != 1:
        return f"target must match exactly one element; matched {count}", count > 1
    if not found.get("visible"):
        return "target is not visible", False
    if query.get("enabled") and not found.get("enabled"):
        return "target is not enabled", False
    return "", False


def _condition_query(op: str, spec: Mapping[str, Any]) -> tuple[dict[str, Any], int, str]:
    selector = str(spec.get("selector") or "")
    stable_ms = _int(spec.get("stable_ms"), 0)
    if op == "wait_for_selector":
        return {"kind": "selector", "selector": selector, "state": str(spec.get("state") or "visible")}, 0, ""
    if op == "wait_for_text":
        return {"kind": "text", "text": str(spec.get("text")), "exact": bool(spec.get("exact"))}, 0, ""
    if op == "wait_for_load_state":
        return {"kind": "load", "state": str(spec.get("state") or "domcontentloaded")}, 0, ""
    if op == "wait_for_first_card_title":
        expected = str(_first(spec, "expected_text", "text") or "")
        return {"kind": "first_card_title", "selector": selector, "expected": expected}, stable_ms, "text"
    if op == "wait_for_sort_state":
        query = {
            "kind": "sort_state",
            "selector": selector,
            "attribute": str(spec.get("attribute") or "aria-sort"),
            "expected": str(_first(spec, "expected_value", "value", "text") or ""),
        }
        return query, 0, "value"
    if op == "wait_for_result_count":
        bounds = {"count": "count", "min": "min_count", "max": "max_count"}
        query = {"kind": "result_count", "selector": selector}
        query.update({name: _number(spec.get(key)) for name, key in bounds.items()})
        return query, stable_ms, "count"
    if op == "wait_for_dom_text_change":
        query = {
            "kind": "dom_text_change",
            "selector": selector,
            "previous": str(_first(spec, "previous_text", "initial_text") or ""),
            "max_chars": _int(spec.get("max_chars"), 1000),
        }
        return query, stable_ms, "text"
    max_chars = _int(spec.get("max_chars"), 1200) if selector else 1200
    return {"kind": "stable", "selector": selector, "max_chars": max_chars}, _int(spec.get("stable_ms"), 500), "stable"


def _url_matcher(spec: Mapping[str, Any]) -> Callable[[str], bool]:
    exact = str(_first(spec, "url", "expected_url") or "")
    contains = str(spec.get("url_contains") or "")
    pattern = str(spec.get("url_pattern") or "")
    try:
        compiled = re.compile(pattern) if pattern else None
    except re.error as error:
        raise _StepError(f"invalid url_pattern: {error}") from error

    def matches(url: str) -> bool:
        return (
            (not exact or url == exact) and contains in url and (compiled is None or compiled.search(url) is not None)
        )

    return matches


def _tab_candidates(surface: BrowserContext) -> list[tuple[int | None, str]]:
    others = [(index, _tab_url(surface, index)) for index in range(len(surface.tabs)) if index != surface.current_tab]
    return [(surface.current_tab, surface.url), *reversed(others)]


def _tab_url(surface: BrowserContext, index: int) -> str:
    if index < len(surface.tab_urls):
        return surface.tab_urls[index]
    return surface.url if index == surface.current_tab else ""


def _candidates(page_state: BrowserPageState, steps: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, Mapping):
            continue
        for key in ("target_id", "option_target_id", "choose_target_id"):
            target_id = text_of(step, key)
            if not target_id:
                continue
            for candidate in page_state.recovery_candidates(target_id):
                candidate_id = str(candidate.get("target_id") or "")
                if candidate_id and candidate_id not in seen:
                    candidates.append(candidate)
                    seen.add(candidate_id)
            if len(candidates) >= 5:
                return candidates
    return candidates


def _compact(item: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        "index": item["index"],
        "op": item["op"],
        "ok": item["ok"],
        "status": _status(item["ok"]),
        "elapsed_ms": item["elapsed_ms"],
    }
    compact.update({key: item[key] for key in ("phase", "error") if item.get(key) not in (None, "")})
    return compact


def _condition_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    entry = _compact(item)
    observed = {key: item[key] for key in _OBSERVED_KEYS if key in item}
    if observed:
        entry["observed"] = observed
    return entry


def _status(ok: bool) -> str:
    return "completed" if ok else "failed"


def _message(detail: str | None) -> str:
    text = str(detail or "the command failed")
    return text[len(_ERROR_HEADING) :] if text.startswith(_ERROR_HEADING) else text


def _first(step: Mapping[str, Any], *keys: str) -> Any:
    return next((step[key] for key in keys if step.get(key) is not None), None)


def _int(value: Any, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _interval_s(spec: Mapping[str, Any]) -> float:
    return max(50, min(1000, _int(spec.get("poll_interval_ms"), 100))) / 1000


def _compact_text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _elapsed_ms(started: float) -> int:
    return int(max(0.0, (time.perf_counter() - started) * 1000))


__all__ = ["BatchTool"]
