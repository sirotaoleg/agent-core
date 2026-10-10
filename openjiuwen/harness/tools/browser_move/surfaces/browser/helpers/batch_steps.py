# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``browser_batch_interact`` card and the step contract checked before any step runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DESCRIPTION = (
    "Execute deterministic browser interactions in one standalone runtime tool call. "
    "Use after browser_probe_interactives/browser_probe_cards when a page-level flow has multiple "
    "known targets, such as three or more form fields, click+type+choose autocomplete, dropdown or "
    "date-picker selection, filter panels, search submit plus result wait, or compact extraction. "
    "This is a first-class helper like the probe tools; do not route this through browser_custom_action. "
    "Pass the current PageState generation_id and use target_id from probes for actions. "
    "Only read-only extraction and explicit wait operations accept a validated selector. "
    "Locator strategies are mutually exclusive. "
    "A one-step call is rewritten by the runtime to an equivalent official browser primitive when "
    "one exists, so it does not fail and require another model turn. Multi-step calls are preflighted "
    "before side effects. Results use status=completed/partial/failed and contain only compact step "
    "status, extracted fields, condition observations, metrics, and PageState."
)
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": (
                "Ordered browser steps to execute in one batch. Supported ops: click, fill, type, "
                "autocomplete, select_visible_text, press, select_option, set_checked, "
                "wait_for_selector, wait_for_text, wait_for_load_state, wait_for_url, "
                "wait_for_first_card_title, wait_for_sort_state, wait_for_result_count, "
                "wait_for_dom_text_change, wait_for_stable, wait_for_tab, extract_text, "
                "extract_value, screenshot."
            ),
            "minItems": 1,
            "maxItems": 25,
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "description": "Step operation name.",
                        "enum": [
                            "click",
                            "fill",
                            "type",
                            "autocomplete",
                            "select_visible_text",
                            "press",
                            "select_option",
                            "set_checked",
                            "wait_for_selector",
                            "wait_for_text",
                            "wait_for_load_state",
                            "wait_for_url",
                            "wait_for_first_card_title",
                            "wait_for_sort_state",
                            "wait_for_result_count",
                            "wait_for_dom_text_change",
                            "wait_for_stable",
                            "wait_for_tab",
                            "extract_text",
                            "extract_value",
                            "screenshot",
                        ],
                    },
                    "target_id": {
                        "type": "string",
                        "description": (
                            "Generation-scoped target_id returned by the current PageState/probe. "
                            "Preferred for actions and mutually exclusive with ref/selector/role/"
                            "label/placeholder/text/testid."
                        ),
                    },
                    "ref": {
                        "type": "string",
                        "description": (
                            "Native AX ref returned by browser_snapshot/browser_find in the current generation. "
                            "The runtime resolves it without model-generated CSS."
                        ),
                    },
                    "selector": {
                        "type": "string",
                        "description": (
                            "Current-generation selector_hint from a probe, or an explicit selector only "
                            "for a wait condition. "
                            "Do not combine with another locator strategy."
                        ),
                    },
                    "role": {
                        "type": "string",
                        "description": "ARIA role to locate, e.g. button/textbox/link.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Accessible name for role-based locating.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Label text for labeled form controls.",
                    },
                    "placeholder": {
                        "type": "string",
                        "description": "Placeholder text for form controls.",
                    },
                    "testid": {
                        "type": "string",
                        "description": "Exact data-testid value for a target.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Visible text for locate/click/wait_for_text.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to fill/type/select, or autocomplete query.",
                    },
                    "option_text": {
                        "type": "string",
                        "description": (
                            "Visible option text for autocomplete/select_visible_text/native select label."
                        ),
                    },
                    "choose_text": {
                        "type": "string",
                        "description": "Alias for option_text in autocomplete flows.",
                    },
                    "option_selector": {
                        "type": "string",
                        "description": "CSS selector for an autocomplete/dropdown option to choose.",
                    },
                    "choose_selector": {
                        "type": "string",
                        "description": "Alias for option_selector.",
                    },
                    "option_role": {
                        "type": "string",
                        "description": ("ARIA role for an autocomplete/dropdown option, e.g. option/menuitem."),
                    },
                    "choose_role": {
                        "type": "string",
                        "description": "Alias for option_role.",
                    },
                    "option_name": {
                        "type": "string",
                        "description": "Accessible name for option_role.",
                    },
                    "choose_name": {
                        "type": "string",
                        "description": "Alias for option_name.",
                    },
                    "option_value": {
                        "type": "string",
                        "description": "Native select option value. Alias for value when op=select_option.",
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "MCP/Playwright-style native select values list. Also accepted for single-select fields."
                        ),
                    },
                    "option_label": {
                        "type": "string",
                        "description": ("Native select visible label. Alias for option_text when op=select_option."),
                    },
                    "label_value": {
                        "type": "string",
                        "description": "Native select visible label alias.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Native select option index.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Keyboard key for press, e.g. Enter or Escape.",
                    },
                    "checked": {
                        "type": "boolean",
                        "description": "Desired checked state for set_checked.",
                    },
                    "state": {
                        "type": "string",
                        "description": (
                            "Wait state for wait_for_selector/load_state, e.g. visible/attached/domcontentloaded."
                        ),
                    },
                    "option_target_id": {
                        "type": "string",
                        "description": "PageState target_id for an autocomplete/dropdown option.",
                    },
                    "option_ref": {
                        "type": "string",
                        "description": "Current-generation AX ref for an autocomplete/dropdown option.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Exact URL expected by wait_for_url.",
                    },
                    "expected_url": {
                        "type": "string",
                        "description": "Alias for the exact URL expected by wait_for_url.",
                    },
                    "url_contains": {
                        "type": "string",
                        "description": "URL substring expected by wait_for_url.",
                    },
                    "url_pattern": {
                        "type": "string",
                        "description": "Regular expression expected to match the current URL.",
                    },
                    "title_contains": {
                        "type": "string",
                        "description": "Title substring expected on a newly opened tab.",
                    },
                    "min_tabs": {
                        "type": "integer",
                        "description": (
                            "Minimum context tab count for wait_for_tab. By default the runtime waits "
                            "for one more tab than existed when the batch started."
                        ),
                    },
                    "activate": {
                        "type": "boolean",
                        "description": (
                            "For wait_for_tab, activate the matched new tab for subsequent steps. Default true."
                        ),
                    },
                    "expected_text": {
                        "type": "string",
                        "description": "Expected first-card title text.",
                    },
                    "attribute": {
                        "type": "string",
                        "description": "Attribute inspected by wait_for_sort_state; defaults to aria-sort.",
                    },
                    "expected_value": {
                        "type": "string",
                        "description": "Expected attribute or text value for a wait condition.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Exact result count expected.",
                    },
                    "min_count": {
                        "type": "integer",
                        "description": "Minimum result count expected.",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum result count expected.",
                    },
                    "previous_text": {
                        "type": "string",
                        "description": "Previous DOM text used by wait_for_dom_text_change.",
                    },
                    "poll_interval_ms": {
                        "type": "integer",
                        "description": (
                            "Dynamic-condition polling interval, clamped to 50..1000ms; default 100ms. "
                            "Polling always shares the step's total timeout."
                        ),
                    },
                    "stable_ms": {
                        "type": "integer",
                        "description": "How long a matched value must remain unchanged.",
                    },
                    "field": {
                        "type": "string",
                        "description": "Structured output field name for extraction steps.",
                    },
                    "exact": {
                        "type": "boolean",
                        "description": "Use exact matching for role/label/text locators.",
                    },
                    "optional": {
                        "type": "boolean",
                        "description": "When true, failure records the step but continues.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Optional per-step timeout override.",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "Optional typing delay in milliseconds.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters for extract_text.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Screenshot path for op=screenshot.",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "When true, screenshot the full page.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable purpose for logs/debugging.",
                    },
                },
                "required": ["op"],
            },
        },
        "timeout_ms": {
            "type": "integer",
            "description": ("Default per-step timeout in milliseconds. Default 2500, clamped to 250..30000."),
        },
        "generation_id": {
            "type": "string",
            "pattern": "^g[0-9]+$",
            "description": (
                "Current PageState generation_id returned by navigate, probe, snapshot, or the previous batch. "
                "Targets from older generations are rejected before browser execution."
            ),
        },
        "condition_timeout_ms": {
            "type": "integer",
            "description": (
                "Default timeout for condition waits in milliseconds. "
                "Default 10000, clamped to the action timeout..30000."
            ),
        },
        "continue_on_error": {
            "type": "boolean",
            "description": "When true, continue after failed steps and return per-step errors.",
        },
        "global_timeout_ms": {
            "type": "integer",
            "description": (
                "Hard timeout for the whole batch. Default is computed from step count, capped at "
                "120000; explicit values are capped at 180000."
            ),
        },
        "session_id": {
            "type": "string",
            "description": "Optional browser task session id.",
        },
        "request_id": {
            "type": "string",
            "description": "Optional browser task request id.",
        },
    },
    "required": ["steps", "generation_id"],
}

_MAX_STEPS = 25
CONDITION_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_text",
        "wait_for_load_state",
        "wait_for_url",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
        "wait_for_tab",
    }
)
_SELECTOR_CONDITION_OPS = frozenset(
    {
        "wait_for_selector",
        "wait_for_first_card_title",
        "wait_for_sort_state",
        "wait_for_result_count",
        "wait_for_dom_text_change",
        "wait_for_stable",
    }
)
READ_OPS = frozenset({"extract_text", "extract_value"})
MUTATING_OPS = frozenset(
    {"click", "fill", "type", "autocomplete", "select_option", "set_checked", "select_visible_text"}
)
PRIMARY_TARGET_OPS = (MUTATING_OPS - {"select_visible_text"}) | READ_OPS
_OPTION_TARGET_OPS = frozenset({"autocomplete", "select_visible_text"})
_SUPPORTED_OPS = PRIMARY_TARGET_OPS | _OPTION_TARGET_OPS | CONDITION_OPS | {"press", "sleep", "screenshot"}
_TEXT_IS_NO_LOCATOR = frozenset({"wait_for_text", "wait_for_sort_state", "wait_for_first_card_title"})
_LOCATOR_KEYS = ("target_id", "ref", "selector", "role", "label", "placeholder", "testid")
MODEL_LOCATOR_KEYS = (*_LOCATOR_KEYS, "name", "text")
_OPTION_KEYS = (
    ("option_target_id", "choose_target_id"),
    ("option_ref", "choose_ref"),
    ("option_selector", "choose_selector"),
    ("option_role", "choose_role"),
    ("option_text", "choose_text", "text_to_choose"),
)
OPTION_VALUE_KEYS = (
    "value",
    "values",
    "option_value",
    "option_text",
    "option_label",
    "label_value",
    "choose_text",
    "index",
)
_URL_KEYS = ("url", "expected_url", "url_contains", "url_pattern")


def step_errors(steps: Any) -> list[str]:
    """Every way ``steps`` breaks the contract, in the order the model should read them; empty when it holds."""
    if not isinstance(steps, list) or not steps:
        return ["steps is required and must be a non-empty list"]
    if len(steps) > _MAX_STEPS:
        return [f"browser_batch_interact accepts at most {_MAX_STEPS} steps"]
    errors: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            errors.append(f"steps[{index}] must be an object")
        elif op_of(step) not in _SUPPORTED_OPS:
            errors.append(f"steps[{index}].op is unsupported: {op_of(step) or '<missing>'}")
        else:
            errors.extend(_requirement_errors(index, op_of(step), step))
            errors.extend(_locator_errors(index, op_of(step), step))
    for index, step in enumerate(steps):
        if isinstance(step, Mapping):
            errors.extend(_contract_errors(index, op_of(step), step))
    return errors


def _requirement_errors(index: int, op: str, step: Mapping[str, Any]) -> list[str]:
    where = f"steps[{index}]"
    checks = (
        (
            op in ("fill", "type", "autocomplete") and "value" not in step,
            f"{where} op={op} requires value" + ("; use value, not text" if "text" in step else ""),
        ),
        (
            op == "select_option" and all(step.get(key) is None for key in OPTION_VALUE_KEYS),
            f"{where} select_option requires an option value",
        ),
        (op == "press" and not text_of(step, "key"), f"{where} press requires key"),
        (op == "wait_for_selector" and not step.get("selector"), f"{where} wait_for_selector requires selector"),
        (op == "wait_for_text" and not step.get("text"), f"{where} wait_for_text requires text"),
        (
            op == "wait_for_url" and not any(step.get(key) for key in _URL_KEYS),
            f"{where} wait_for_url requires a URL condition",
        ),
        (
            op == "wait_for_first_card_title" and not step.get("selector"),
            f"{where} wait_for_first_card_title requires selector",
        ),
        (
            op == "wait_for_sort_state"
            and not (step.get("selector") and _any_set(step, ("expected_value", "value", "text"))),
            f"{where} wait_for_sort_state requires selector and expected value",
        ),
        (
            op == "wait_for_result_count"
            and not (step.get("selector") and _any_set(step, ("count", "min_count", "max_count"))),
            f"{where} wait_for_result_count requires selector and count",
        ),
        (
            op == "wait_for_dom_text_change" and not step.get("selector"),
            f"{where} wait_for_dom_text_change requires selector",
        ),
        (op == "sleep" and _sleep_ms(step) < 0, f"{where} sleep requires non-negative ms"),
    )
    return [message for failed, message in checks if failed]


def _locator_errors(index: int, op: str, step: Mapping[str, Any]) -> list[str]:
    where = f"steps[{index}]"
    strategies = [key for key in _LOCATOR_KEYS if given(step, key)]
    if op not in _TEXT_IS_NO_LOCATOR and given(step, "text"):
        strategies.append("text")
    received = ", ".join(strategies)
    options = [key for aliases in _OPTION_KEYS for key in aliases if given(step, key)]
    errors = []
    if given(step, "name") and not given(step, "role"):
        errors.append(f"{where}.name requires role")
    if op in PRIMARY_TARGET_OPS and not strategies:
        errors.append(f"{where} op={op} requires a target")
    elif op in PRIMARY_TARGET_OPS and len(strategies) > 1:
        errors.append(f"{where} op={op} requires exactly one locator strategy; received {received}")
    if op == "press" and len(strategies) > 1:
        errors.append(f"{where} op=press accepts at most one locator strategy; received {received}")
    if op in _SELECTOR_CONDITION_OPS:
        invalid = [key for key in strategies if key != "selector"]
        if invalid:
            errors.append(f"{where} op={op} accepts only selector; received {', '.join(invalid)}")
    elif op not in PRIMARY_TARGET_OPS and op != "press" and strategies:
        errors.append(f"{where} op={op} does not accept a primary locator; received {received}")
    if op in _OPTION_TARGET_OPS and not options:
        errors.append(f"{where} {op} requires an option target")
    elif op in _OPTION_TARGET_OPS and len(options) > 1:
        errors.append(f"{where} op={op} requires exactly one option locator strategy; received {', '.join(options)}")
    if (step.get("option_name") or step.get("choose_name")) not in (None, "") and not (
        step.get("option_role") or step.get("choose_role")
    ):
        errors.append(f"{where} option_name requires option_role")
    return errors


def _contract_errors(index: int, op: str, step: Mapping[str, Any]) -> list[str]:
    errors = []
    if op in MUTATING_OPS and not text_of(step, "target_id"):
        errors.append(f"steps[{index}] op={op} requires target_id from the current PageState")
    if op in READ_OPS and len([key for key in ("target_id", "selector") if text_of(step, key)]) != 1:
        errors.append(f"steps[{index}] op={op} requires exactly one of target_id or selector")
    return errors


def op_of(step: Mapping[str, Any]) -> str:
    return str(step.get("op") or "").strip().lower()


def text_of(step: Mapping[str, Any], key: str) -> str:
    return str(step.get(key) or "").strip()


def given(step: Mapping[str, Any], key: str) -> bool:
    return step.get(key) not in (None, "")


def _any_set(step: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(step.get(key) is not None for key in keys)


def _sleep_ms(step: Mapping[str, Any]) -> int:
    try:
        return int(step.get("ms", step.get("time_ms", 0)))
    except (TypeError, ValueError):
        return -1
