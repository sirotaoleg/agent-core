# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``browser_batch_interact`` over commands on a scripted driver: the step contract, one step, several steps."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionIntercepted,
    ActionResult,
    Click,
    ConnectionLost,
    ContextRef,
    DriverError,
    DriverInfo,
    DriverRef,
    Effect,
    Locator,
    Observation,
    Observe,
    PressKey,
    RunScript,
    SwitchContext,
    TypeText,
    Wait,
)
from openjiuwen.harness.tools.browser_move.contract.refs import REF_KINDS
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS, FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import BrowserBatchInteractTool
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolContext, ToolFlags, command_record
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    FieldKind,
    FillForm,
    Navigate,
    SelectOption,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers import BatchTool
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

INFO = DriverInfo(
    name="fake",
    surfaces=frozenset({"browser"}),
    actions={**DEFAULT_ACTIONS, **{command: frozenset(REF_KINDS) for command in (Navigate, SelectOption, FillForm)}},
)
OLD = ContextRef("0", "tab", "Old tab")
NEW = ContextRef("1", "tab", "New tab")
OLD_URL = "https://example.test/old"
NEW_URL = "https://example.test/new"
READY = {"count": 1, "visible": True, "enabled": True}
DONE = ActionResult(ok=True)


class ScriptedDriver(FakeDriver):
    """Answers commands from the queue in order; a queued exception is raised for its command instead."""

    async def execute(self, action: Action) -> ActionResult:
        answer = await super().execute(action)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _context(*answers: Any, observations: Iterable[Observation] = ()) -> ToolContext:
    driver = ScriptedDriver(observations, answers, info=INFO)
    page_state = BrowserPageState()
    alive = SimpleNamespace(is_alive=lambda device: True)
    session = DriverSession(
        DriverAssembly(stack=driver, device=None, provisioner=alive, surface_settings=None), page_state, MESSAGES
    )
    return ToolContext(session, page_state, BrowserRenderer(), INFO, BrowserSettings(), False)


def _driver(context: ToolContext) -> ScriptedDriver:
    return context.driver.assembly.stack


def _targets(context: ToolContext, *selectors: str, **flags: Any) -> list[str]:
    elements = [
        {
            "selector_hint": selector,
            "selector_hint_validated": True,
            "match_count": 1,
            "role": "button",
            "text": selector,
            "visible": True,
            "enabled": True,
            "actionable": True,
            **flags,
        }
        for selector in selectors
    ]
    context.page_state.register_interactives({"elements": elements})
    return [element["target_id"] for element in elements]


def _located(*found: dict[str, Any]) -> ActionResult:
    return ActionResult(ok=True, value=list(found) or [READY])


def _condition(ok: bool, value: Any) -> ActionResult:
    return ActionResult(ok=True, value={"ok": ok, "value": value})


def _tabs(*urls: str, current: int = 0) -> Observation:
    tabs = (OLD, NEW)[: len(urls)]
    surface = BrowserContext(
        url=urls[current], title=tabs[current].title, tabs=tabs, tab_urls=urls, current_tab=current
    )
    return Observation(context=tabs[current], surface=surface)


def _batch(context: ToolContext, steps: list[dict[str, Any]], **inputs: Any) -> Any:
    return _run(BatchTool(context).invoke({"steps": steps, "generation_id": "g0", **inputs}))


def _sent(context: ToolContext) -> list[Any]:
    return [call.payload for call in _driver(context).calls if call.kind in ("execute", "observe")]


def _commands(context: ToolContext) -> list[Any]:
    return [action for action in _sent(context) if not isinstance(action, RunScript)]


def _marked(action: Any) -> bool:
    return isinstance(action.target, Locator) and action.target.css.startswith("[data-openjiuwen-target-id=")


# --- card, flags, step contract --------------------------------------------------------------------------------------


def test_the_batch_card_equals_the_old_tool_card() -> None:
    assert BatchTool(_context()).card.tool_info() == BrowserBatchInteractTool(None).card.tool_info()


def test_the_batch_declares_that_it_acts() -> None:
    assert BatchTool.flags == ToolFlags(effect=Effect.ACT)


def test_batch_interact_action_rejects_more_than_25_steps() -> None:
    context = _context()
    steps = [{"op": "click", "selector": f"#button-{index}"} for index in range(30)]

    output = _batch(context, steps)

    assert output.success is False
    assert "at most 25" in output.data["content"]["error"]
    assert _driver(context).calls == []


@pytest.mark.parametrize("steps", [[], "click #save", None])
def test_batch_interact_action_rejects_empty_or_non_list_steps_before_running_code(steps: Any) -> None:
    context = _context()

    output = _batch(context, steps)

    assert "non-empty list" in output.data["content"]["error"]
    assert _driver(context).calls == []


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"op": "press"}], "requires key"),
        ([{"op": "unknown", "selector": "#x"}, {"op": "press", "key": "Enter"}], "unsupported"),
        ([{"op": "fill", "value": "query"}, {"op": "press", "key": "Enter"}], "requires a target"),
        (
            [{"op": "autocomplete", "selector": "#from", "value": "SIN"}, {"op": "press", "key": "Enter"}],
            "requires an option target",
        ),
    ],
)
def test_batch_interact_validates_schema_before_executor(steps: list[dict[str, Any]], message: str) -> None:
    context = _context()

    payload = _batch(context, steps).data["content"]

    assert payload["ok"] is False
    assert message in payload["error"]
    assert _driver(context).calls == []


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [{"op": "click", "selector": "text=销量", "role": "tab"}, {"op": "press", "key": "Enter"}],
            "exactly one locator strategy",
        ),
        (
            [{"op": "type", "selector": "#toolbar-search", "text": "query"}, {"op": "press", "key": "Enter"}],
            "use value, not text",
        ),
        (
            [
                {
                    "op": "autocomplete",
                    "target_id": "t_g2_1",
                    "value": "Shanghai",
                    "option_target_id": "t_g2_2",
                    "option_selector": ".autocomplete",
                },
                {"op": "press", "key": "Enter"},
            ],
            "exactly one option locator strategy",
        ),
        (
            [
                {"op": "wait_for_text", "text": "Results", "selector": ".guessed-result"},
                {"op": "press", "key": "Enter"},
            ],
            "does not accept a primary locator",
        ),
    ],
)
def test_batch_interact_rejects_known_bad_case_schema_before_executor(
    steps: list[dict[str, Any]], message: str
) -> None:
    context = _context()

    payload = _batch(context, steps).data["content"]

    assert (payload["ok"], payload["status"]) == (False, "failed")
    assert any(message in error for error in payload["validation_errors"])
    assert _driver(context).calls == []


def test_batch_target_contract_requires_target_id_for_mutations() -> None:
    context = _context()

    payload = _batch(context, [{"op": "click", "selector": "#submit"}]).data["content"]

    assert payload["validation_errors"] == ["steps[0] op=click requires target_id from the current PageState"]


def test_a_batch_refused_before_any_command_leaves_no_record() -> None:
    output = _batch(_context(), [{"op": "press"}])

    assert command_record(output) is None and "command" not in output.data


# --- one step: one command -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "command"),
    [
        ({"op": "click"}, Click(Locator("#save", None, "g0"))),
        ({"op": "fill", "value": "John"}, TypeText(Locator("#save", None, "g0"), "John")),
        ({"op": "type", "value": "John"}, TypeText(Locator("#save", None, "g0"), "John", slowly=True)),
        ({"op": "fill", "value": "John", "delay_ms": 30}, TypeText(Locator("#save", None, "g0"), "John", slowly=True)),
        ({"op": "select_option", "values": ["m"]}, SelectOption(Locator("#save", None, "g0"), ("m",))),
        ({"op": "select_option", "value": "m"}, SelectOption(Locator("#save", None, "g0"), ("m",))),
    ],
)
def test_one_step_on_a_target_runs_as_one_command(step: dict[str, Any], command: Action) -> None:
    context = _context(DONE)
    [target_id] = _targets(context, "#save")

    payload = _batch(context, [{**step, "target_id": target_id}]).data["content"]

    assert _sent(context) == [command]
    assert (payload["ok"], payload["execution_mode"], payload["metrics"]["command"]) == (
        True,
        "primitive",
        type(command).__name__,
    )


@pytest.mark.parametrize(
    ("step", "command"),
    [
        ({"op": "press", "key": "Enter"}, PressKey("Enter")),
        ({"op": "wait_for_text", "text": "Saved"}, Wait(until_text="Saved")),
        ({"op": "sleep", "ms": 250}, Wait(seconds=0.25)),
    ],
)
def test_one_step_without_a_target_runs_as_one_command(step: dict[str, Any], command: Action) -> None:
    context = _context(DONE)

    payload = _batch(context, [step]).data["content"]

    assert _sent(context) == [command]
    [result] = payload["steps"]
    assert {key: result[key] for key in ("index", "op", "ok", "status")} == {
        "index": 0,
        "op": step["op"],
        "ok": True,
        "status": "completed",
    }


def test_one_screenshot_step_observes_with_a_screenshot() -> None:
    context = _context(observations=[_tabs(OLD_URL)])

    _batch(context, [{"op": "screenshot", "full_page": True}])

    assert _sent(context) == [Observe(include_screenshot=True, full_page=True)]


def test_one_step_on_a_snapshot_ref_clicks_the_ref_itself() -> None:
    context = _context(DONE)
    context.page_state.register_ax_snapshot('- button "Save order" [ref=e9]')

    _batch(context, [{"op": "click", "target_id": "t_g0_1"}])

    assert _sent(context) == [Click(DriverRef("e9", "g0"))]


def test_a_card_link_click_navigates_alone_and_is_refused_in_a_batch() -> None:
    context = _context(DONE)
    card = {"title": "Mouse", "primary_link": "https://shop.test/mouse", "visible": True}
    context.page_state.register_cards({"cards": [card]})
    link = card["primary_link_target_id"]

    _batch(context, [{"op": "click", "target_id": link}])
    refused = _batch(context, [{"op": "click", "target_id": link}, {"op": "press", "key": "Enter"}]).data["content"]

    assert _sent(context) == [Navigate("https://shop.test/mouse")]
    assert "call browser_navigate directly" in refused["error"]


def test_one_wait_for_text_step_reports_its_condition() -> None:
    context = _context(DONE)

    payload = _batch(context, [{"op": "wait_for_text", "text": "Saved"}]).data["content"]

    assert payload["conditions"][0]["observed"] == {"text": "Saved"}


def test_one_step_the_page_refused_reports_the_error_without_its_heading() -> None:
    context = _context(ActionResult(ok=False, detail="### Error\nno such key"))

    payload = _batch(context, [{"op": "press", "key": "Nope"}]).data["content"]

    assert (payload["ok"], payload["status"], payload["error"]) == (False, "failed", "no such key")
    assert payload["steps"][0]["error"] == "no such key"


# --- several steps ---------------------------------------------------------------------------------------------------


def _booking(ids: list[str]) -> list[dict[str, Any]]:
    first_name, origin, destination, nationality, male, search, extracted = ids
    return [
        {"op": "fill", "target_id": first_name, "value": "John"},
        {"op": "autocomplete", "target_id": origin, "value": "Singapore", "choose_text": "Singapore (SIN)"},
        {
            "op": "autocomplete",
            "target_id": destination,
            "value": "Kuala Lumpur",
            "option_role": "option",
            "option_name": "Kuala Lumpur (KUL)",
        },
        {"op": "select_option", "target_id": nationality, "option_text": "Singapore"},
        {"op": "set_checked", "target_id": male, "checked": True},
        {"op": "click", "target_id": search},
        {"op": "wait_for_text", "text": "Select"},
        {"op": "extract_value", "target_id": extracted},
    ]


def test_realistic_booking_steps_run_as_commands_one_after_another() -> None:
    answers = [
        _located(*[READY] * 7),  # preflight of every target
        _located(),
        DONE,  # fill
        _located(),
        DONE,
        DONE,
        DONE,
        _located(),
        DONE,  # autocomplete by text
        _located(),
        DONE,
        DONE,
        DONE,
        _located(),
        DONE,  # autocomplete by role
        _located(),
        DONE,  # select
        _located(),
        DONE,  # set_checked
        _located(),
        DONE,  # click
        _condition(True, "Select"),
        _located({**READY, "value": "John"}),
    ]
    context = _context(*answers)
    ids = _targets(context, "#first", "#from", "#to", "#nationality", "#male", "#search", "#first-again")

    payload = _batch(context, _booking(ids)).data["content"]

    assert payload["ok"] is True, payload
    assert [step["op"] for step in payload["steps"]] == [step["op"] for step in _booking(ids)]
    commands = _commands(context)
    assert [type(command) for command in commands] == [
        TypeText,
        *(Click, TypeText, TypeText, Click) * 2,
        SelectOption,
        FillForm,
        Click,
    ]
    assert all(_marked(command) for command in commands if not isinstance(command, FillForm))
    assert [command.text for command in commands if isinstance(command, TypeText)] == [
        "John",
        "",
        "Singapore",
        "",
        "Kuala Lumpur",
    ]
    assert commands[2] == TypeText(commands[2].target, "", clear=True)
    assert commands[3] == TypeText(commands[3].target, "Singapore", clear=False, slowly=True)
    assert commands[9].values == ("Singapore",)
    assert (commands[10].fields[0].kind, commands[10].fields[0].value) == (FieldKind.CHECKBOX, "true")
    located = [action.args for action in _sent(context)[1:] if isinstance(action, RunScript)]
    options = [args["queries"][0] for args in located if "queries" in args]
    assert {"text": "Singapore (SIN)", "exact": False}.items() <= options[2].items()
    assert {"role": "option", "name": "Kuala Lumpur (KUL)", "exact": False}.items() <= options[4].items()
    assert payload["extracted"] == {"field_8": "John"}


def test_batch_click_retries_only_transient_actionability_failures_within_same_timeout() -> None:
    intercepted = ActionIntercepted("<div class=overlay> intercepts pointer events", by="div.overlay")
    context = _context(
        _located(READY, READY), _located(), intercepted, _located(), DONE, _located(), DriverError("boom")
    )
    first, second = _targets(context, "#search", "#next")

    payload = _batch(context, [{"op": "click", "target_id": first}, {"op": "click", "target_id": second}]).data[
        "content"
    ]

    assert [type(command) for command in _commands(context)] == [Click, Click, Click]
    assert [step["ok"] for step in payload["steps"]] == [True, False]
    assert (payload["status"], payload["error"]) == ("partial", "boom")


def test_an_unready_target_is_waited_for_within_the_step_timeout() -> None:
    hidden = {"count": 1, "visible": False, "enabled": True}
    context = _context(_located(), _located({"count": 0}), _located(hidden), _located(), DONE, DONE)
    [target_id] = _targets(context, "#search")

    payload = _batch(context, [{"op": "click", "target_id": target_id}, {"op": "press", "key": "Enter"}]).data[
        "content"
    ]

    assert payload["ok"] is True
    assert [type(command) for command in _commands(context)] == [Click, PressKey]


def test_press_on_a_target_focuses_the_target_first() -> None:
    context = _context(_located(), _located(), DONE, DONE)

    _batch(context, [{"op": "press", "label": "Search", "key": "Enter"}, {"op": "sleep", "ms": 0}])

    focus = _sent(context)[1].args["queries"][0]
    assert ({"label": "Search", "focus": True}.items() <= focus.items(), _commands(context)[0]) == (
        True,
        PressKey("Enter"),
    )


def test_batch_extraction_provenance_keeps_selector_raw_text_and_generation() -> None:
    not_a_field = {**READY, "error": "Not an input, textarea or select element"}
    context = _context(_located(READY, READY), _located({**READY, "text": "Original\n title"}), _located(not_a_field))

    payload = _batch(
        context,
        [
            {"op": "extract_text", "selector": "article:nth-of-type(1) h2", "field": "title"},
            {"op": "extract_value", "selector": "#price", "field": "price"},
        ],
        continue_on_error=True,
    ).data["content"]

    assert payload["extracted"] == {"title": "Original title"}
    assert payload["field_provenance"] == {
        "title": {"selector": "article:nth-of-type(1) h2", "raw_text": "Original\n title", "generation_id": "g0"}
    }
    assert payload["steps"][1]["error"] == "Not an input, textarea or select element"
    assert context.page_state.field_coverage == {"title"}


def test_batch_interact_runs_commands_and_returns_the_compact_result() -> None:
    context = _context(_located(), _located(), DONE, DONE)
    [target_id] = _targets(context, "#q")

    payload = _batch(
        context,
        [{"op": "fill", "target_id": target_id, "value": "OpenJiuwen"}, {"op": "press", "key": "Enter"}],
        session_id="sess-test",
        request_id="req-test",
    ).data["content"]

    assert set(payload) == {
        "ok",
        "status",
        "error",
        "action",
        "execution_mode",
        "session_id",
        "request_id",
        "generation_id",
        "steps",
        "extracted",
        "field_provenance",
        "conditions",
        "metrics",
        "page_state",
    }
    assert (payload["action"], payload["execution_mode"]) == ("browser_batch_interact", "commands")
    assert (payload["session_id"], payload["request_id"]) == ("sess-test", "req-test")
    assert payload["metrics"]["command_count"] == 4
    assert payload["metrics"]["preflight_target_count"] == 1


def test_batch_interact_reports_partial_when_any_executed_step_failed() -> None:
    context = _context(_located(), _located(), DONE, DriverError("the page script failed", dispatched=True))
    [target_id] = _targets(context, "#q")

    payload = _batch(
        context,
        [
            {"op": "fill", "target_id": target_id, "value": "OpenJiuwen"},
            {"op": "wait_for_text", "text": "Results", "optional": True},
        ],
    ).data["content"]

    assert (payload["ok"], payload["status"]) == (False, "partial")
    assert payload["error"] == "one or more batch steps failed"


def test_a_failed_step_stops_the_batch_unless_errors_continue() -> None:
    steps = [{"op": "press", "key": "Tab"}, {"op": "press", "key": "Enter"}]
    stopped = _context(DriverError("no keyboard"), DONE)
    continued = _context(DriverError("no keyboard"), DONE)

    stopped_payload = _batch(stopped, steps).data["content"]
    continued_payload = _batch(continued, steps, continue_on_error=True).data["content"]

    assert (len(stopped_payload["steps"]), stopped_payload["status"], stopped_payload["error"]) == (
        1,
        "failed",
        "no keyboard",
    )
    assert (len(continued_payload["steps"]), continued_payload["status"]) == (2, "partial")


def test_a_lost_connection_stops_the_batch_even_when_errors_continue() -> None:
    context = _context(ConnectionLost("gone", dispatched=True), DONE)

    payload = _batch(
        context, [{"op": "press", "key": "Tab"}, {"op": "press", "key": "Enter"}], continue_on_error=True
    ).data["content"]

    assert len(payload["steps"]) == 1
    assert "restarted" in payload["error"]


def test_batch_interact_returns_compact_condition_observations() -> None:
    results = _tabs("https://example.test/results")
    context = _context(_condition(True, 10), observations=[results])

    payload = _batch(
        context,
        [
            {"op": "wait_for_url", "url_contains": "/results"},
            {"op": "wait_for_result_count", "selector": ".result", "min_count": 1},
        ],
    ).data["content"]

    assert payload["conditions"][0]["observed"] == {"url": "https://example.test/results"}
    assert payload["conditions"][1]["observed"] == {"count": 10}
    assert "url" not in payload and "title" not in payload
    count = _sent(context)[1].args
    assert (count["kind"], count["selector"], count["min"], count["count"]) == ("result_count", ".result", 1, None)


def test_a_condition_waits_until_its_value_holds_still_for_the_stable_time() -> None:
    answers = [_condition(True, "A"), _condition(True, "B"), _condition(True, "B"), _condition(True, "B")]
    context = _context(*answers)

    payload = _batch(
        context,
        [
            {"op": "wait_for_stable", "selector": "#list", "stable_ms": 60, "poll_interval_ms": 50},
            {"op": "sleep", "ms": 0},
        ],
    ).data["content"]

    assert payload["conditions"][0]["observed"] == {"stable": True}
    assert len([script for script in _sent(context) if isinstance(script, RunScript)]) >= 3


def test_a_condition_that_never_holds_times_out_after_the_condition_timeout() -> None:
    context = _context(*[_condition(False, "")] * 20)

    payload = _batch(
        context,
        [{"op": "wait_for_selector", "selector": "#done"}, {"op": "sleep", "ms": 0}],
        timeout_ms=250,
        condition_timeout_ms=300,
    ).data["content"]

    assert payload["error"] == "condition timed out after 300ms"


def test_wait_for_tab_is_a_valid_condition_and_activates_new_tab() -> None:
    context = _context(_located(), _located(), DONE, DONE, observations=[_tabs(OLD_URL), _tabs(OLD_URL, NEW_URL)])
    [opener] = _targets(context, "#open")

    payload = _batch(
        context, [{"op": "click", "target_id": opener}, {"op": "wait_for_tab", "url_contains": "/new"}]
    ).data["content"]

    assert payload["ok"] is True, payload
    assert payload["conditions"][0]["observed"]["count"] == 2
    assert payload["conditions"][0]["observed"]["url"] == NEW_URL
    assert _commands(context)[-1] == SwitchContext(NEW)


def test_wait_for_url_switches_to_matching_new_tab() -> None:
    context = _context(_located(), _located(), DONE, DONE, observations=[_tabs(OLD_URL, NEW_URL)])
    [opener] = _targets(context, "#open")

    payload = _batch(
        context, [{"op": "click", "target_id": opener}, {"op": "wait_for_url", "url_contains": "/new"}]
    ).data["content"]

    assert payload["conditions"][0]["observed"] == {"url": NEW_URL}
    assert _commands(context)[-1] == SwitchContext(NEW)


def test_batch_preflight_rejects_later_ambiguous_target_before_any_click() -> None:
    context = _context(_located(READY, {"count": 2, "visible": True, "enabled": True}))
    first, duplicate = _targets(context, "#first", ".duplicate")

    payload = _batch(context, [{"op": "click", "target_id": first}, {"op": "click", "target_id": duplicate}]).data[
        "content"
    ]

    assert (payload["ok"], payload["status"]) == (False, "failed")
    assert payload["steps"][0]["phase"] == "preflight"
    assert payload["error"] == "batch_preflight_failed: steps[1] target must match exactly one element; matched 2"
    assert _commands(context) == []


def test_safe_read_selector_preflight_requires_one_visible_match() -> None:
    context = _context(_located({"count": 2, "visible": True, "enabled": True}))

    payload = _batch(
        context,
        [{"op": "extract_text", "selector": ".title", "field": "title"}, {"op": "sleep", "ms": 0}],
    ).data["content"]

    assert "matched 2" in payload["error"]
    [preflight] = _sent(context)
    assert preflight.args["queries"] == [{"css": ".title", "enabled": False}]


def test_target_ids_from_a_snapshot_are_marked_on_the_page_before_a_multi_step_batch() -> None:
    context = _context(DONE, _located(), _located(), DONE, DONE)
    context.page_state.register_ax_snapshot('- button "Save order" [ref=e9]')

    _batch(context, [{"op": "click", "target_id": "t_g0_1"}, {"op": "press", "key": "Enter"}])

    stamp, preflight = _sent(context)[:2]
    assert (stamp.target, stamp.args) == (DriverRef("e9", "g0"), "t_g0_1")
    assert preflight.args["queries"][0]["css"] == '[data-openjiuwen-target-id="t_g0_1"]'


def test_a_stale_generation_refreshes_probe_targets_by_their_selector() -> None:
    validated = {"ok": True, "match_count": 1, "visible": True, "enabled": True, "actionable": True}
    refreshed = ActionResult(ok=True, value=validated)
    context = _context(refreshed, _located(), _located(), DONE, DONE)
    [stale] = _targets(context, "#save")
    context.page_state.advance()

    payload = _batch(context, [{"op": "click", "target_id": stale}, {"op": "press", "key": "Enter"}]).data["content"]

    assert payload["ok"] is True, payload
    assert payload["generation_recovered_from"] == "g0"
    assert _sent(context)[0].args == "#save"


def test_a_stale_generation_without_targets_to_refresh_is_refused() -> None:
    context = _context()
    context.page_state.advance()

    payload = _batch(context, [{"op": "press", "key": "Tab"}, {"op": "press", "key": "Enter"}]).data["content"]

    assert payload["error"].startswith("batch_target_validation_failed: Stale PageState generation g0")
    assert _sent(context) == []


def test_a_target_that_is_not_actionable_is_refused_before_anything_runs() -> None:
    context = _context()
    [target_id] = _targets(context, "#save", actionable=False)

    payload = _batch(context, [{"op": "click", "target_id": target_id}]).data["content"]

    assert payload["error"] == f"batch_target_validation_failed: PageState target {target_id} is not actionable in g0"


# --- the record ------------------------------------------------------------------------------------------------------


def test_the_batch_record_carries_the_last_command_the_strongest_effect_and_the_step_results() -> None:
    context = _context(_located(), _located(), ActionResult(ok=True, context_changed=True), DONE)
    [target_id] = _targets(context, "#save")

    output = _batch(context, [{"op": "click", "target_id": target_id}, {"op": "sleep", "ms": 0}])

    record = command_record(output)
    assert (record.command, record.effect, record.ok, record.dispatched, record.context_changed) == (
        Wait,
        Effect.ACT,
        True,
        True,
        True,
    )
    assert record.value is output.data["content"]
    assert (record.action, record.generation) == (Wait(seconds=0.0), "g1")


def test_a_batch_that_only_reads_records_a_read() -> None:
    context = _context(_located(), _located({**READY, "text": "Title"}), _condition(True, "complete"))

    output = _batch(
        context,
        [{"op": "extract_text", "selector": "h1", "field": "title"}, {"op": "wait_for_load_state"}],
    )

    record = command_record(output)
    assert (record.command, record.effect) == (RunScript, Effect.READ)
    assert record.message_metadata["state_changed"] is False


def test_a_failed_batch_records_that_it_failed() -> None:
    context = _context(DriverError("no keyboard", dispatched=False))

    record = command_record(_batch(context, [{"op": "press", "key": "Tab"}]))

    assert (record.command, record.ok, record.dispatched) == (PressKey, False, False)
