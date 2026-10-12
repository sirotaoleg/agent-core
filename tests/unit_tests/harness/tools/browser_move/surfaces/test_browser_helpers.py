# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser helpers over commands on FakeDriver: probes, page metadata, browser_find, the policy probe and activate."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.foundation.tool.base import render_tool_output
from openjiuwen.harness.tools.browser_move.contract import (
    ActionResult,
    ContextRef,
    DriverInfo,
    Effect,
    NativeTool,
    Observation,
    Observe,
    RunScript,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS, FakeDriver
from openjiuwen.harness.tools.browser_move.offload_recall import BrowserOffloadRecallTool
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.runtime.tool_factory import ToolContext, ToolFlags, command_record
from openjiuwen.harness.tools.browser_move.surfaces.browser import site_profiles
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.helpers import (
    EXTRA_TOOLS,
    BatchTool,
    FindTool,
    ProbeCardsTool,
    ProbeInteractivesTool,
    RecallOffloadTool,
    activate_page,
    page_metadata,
    probe_for_policy,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import MESSAGES
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

DEVICE = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9222", extras={})
TABS = (ContextRef("0", "tab", "Start"), ContextRef("1", "tab", "Second"))
START = "http://scene.test/index.html"
SECOND = "http://scene.test/second.html"
SURFACE = BrowserContext(url=START, title="Start", tabs=TABS, tab_urls=(START, SECOND), current_tab=0)
SAVE = {
    "selector_hint": "#save",
    "selector_hint_validated": True,
    "match_count": 1,
    "role": "button",
    "text": "Save order",
    "visible": True,
    "enabled": True,
    "actionable": True,
}
SNAPSHOT = """### Page
- Page URL: http://scene.test/index.html
### Snapshot
```yaml
- generic [ref=e1]:
  - heading "Conformance scene" [level=1] [ref=e2]
  - navigation [ref=e3]:
    - link "Go to second" [ref=e4]
  - form [ref=e5]:
    - textbox "Full name" [ref=e6]
    - checkbox "Gift wrap" [ref=e7]
    - combobox "Size" [ref=e8]
    - option "Small"
    - option "Medium"
    - option "Large"
    - button "Save order" [ref=e9]
```
"""
FIND_INFO = NativeTool(
    "Server find.", {"type": "object", "properties": {"text": {"type": "string"}, "regex": {"type": "string"}}}
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _context(driver: FakeDriver, info: DriverInfo | None = None, tmp_path: Path | None = None) -> ToolContext:
    page_state = BrowserPageState()
    session = DriverSession(
        DriverAssembly(stack=driver, device=DEVICE, provisioner=None), page_state, MESSAGES
    )
    settings = BrowserSettings(selector_cache_path=(tmp_path / "selectors.json") if tmp_path else None)
    return ToolContext(session, page_state, BrowserRenderer(), info or driver.info, settings, False)


def _script(value: Any, *, ok: bool = True) -> ActionResult:
    return ActionResult(ok=ok, value=value, detail=None if ok else "### Error\nblocked")


def _observation(text: str = SNAPSHOT, surface: BrowserContext = SURFACE) -> Observation:
    return Observation(context=TABS[0], native_text=text, surface=surface)


def _scripts(driver: FakeDriver) -> list[RunScript]:
    return [call.payload for call in driver.calls if isinstance(call.payload, RunScript)]


@pytest.fixture(autouse=True)
def _fresh_selector_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(site_profiles, "_SELECTOR_CACHE", None)


# --- cards -----------------------------------------------------------------------------------------------------------


def test_the_extra_tools_are_the_probes_the_batch_find_and_recall() -> None:
    assert EXTRA_TOOLS == (ProbeInteractivesTool, ProbeCardsTool, BatchTool, FindTool, RecallOffloadTool)


def test_the_helpers_declare_read_progress_and_only_recall_is_exempt_from_budgets() -> None:
    assert ToolFlags() == ToolFlags(effect=Effect.READ, exempt_from_budget=False, progress=True, worker=True)
    assert [tool.flags for tool in EXTRA_TOOLS] == [
        ToolFlags(),
        ToolFlags(),
        ToolFlags(effect=Effect.ACT),
        ToolFlags(),
        ToolFlags(exempt_from_budget=True, worker=False),
    ]


# --- the shared command step and records -----------------------------------------------------------------------------


def test_the_tool_context_runs_commands_through_the_session_and_records_them() -> None:
    driver = FakeDriver(observations=[_observation()], results=[ActionResult(ok=True, context_changed=True)])
    context = _context(driver)

    observation = _run(context.observe(Observe()))
    result = _run(context.execute(RunScript(code="() => 1")))

    assert driver.kinds() == ["observe", "execute"]
    assert (observation.generation, result.generation, context.page_state.generation_id) == ("g0", "g1", "g1")


def test_a_probe_result_carries_a_read_record_of_its_page_script() -> None:
    driver = FakeDriver(results=[_script({"url": START, "elements": [dict(SAVE)]})])

    output = _run(ProbeInteractivesTool(_context(driver)).invoke({}))

    record = command_record(output)
    [script] = _scripts(driver)
    assert (record.command, record.effect, record.ok, record.dispatched) == (RunScript, Effect.READ, True, True)
    assert (record.action, record.generation) == (script, "g0")
    assert record.message_metadata["state_changed"] is False


def test_a_probe_the_driver_failed_carries_the_failed_record() -> None:
    driver = FakeDriver()
    driver.fail_next(dispatched=True)

    record = command_record(_run(ProbeInteractivesTool(_context(driver)).invoke({})))

    assert (record.command, record.effect, record.ok, record.dispatched) == (RunScript, Effect.READ, False, True)
    assert record.error == "DriverError"


def test_the_model_reads_the_probe_payload_as_before() -> None:
    driver = FakeDriver(results=[_script({"url": START, "elements": [dict(SAVE)]})])

    output = _run(ProbeInteractivesTool(_context(driver)).invoke({}))

    assert render_tool_output(output) == json.dumps(output.data["content"], ensure_ascii=False)


# --- recall ----------------------------------------------------------------------------------------------------------


def test_recall_reads_the_workspace_in_the_language_of_the_tool_context(tmp_path: Path) -> None:
    handle = "a" * 32
    offload = tmp_path / "context" / "s1_context" / "offload"
    offload.mkdir(parents=True)
    payload = {"offload_handle": handle, "messages": [{"name": "browser_snapshot", "content": "hello world"}]}
    (offload / f"ToolResultWindowProcessor_{handle}.json").write_text(json.dumps(payload), encoding="utf-8")
    tool = RecallOffloadTool(dataclasses.replace(_context(FakeDriver()), workspace=tmp_path, language="en"))

    output = _run(tool.invoke({"handle": handle}, session=SimpleNamespace(get_session_id=lambda: "s1")))

    assert tool.card.tool_info() == BrowserOffloadRecallTool(tmp_path, language="en").card.tool_info()
    assert output.success and output.data["content"] == "hello world"


# --- probes ----------------------------------------------------------------------------------------------------------


def test_probe_interactives_runs_the_page_function_once_with_its_params() -> None:
    driver = FakeDriver(results=[_script({"url": START, "title": "Start", "elements": [dict(SAVE)]})])
    context = _context(driver)

    output = _run(ProbeInteractivesTool(context).invoke({"max_items": 99, "query": " Save ", "viewport_only": "no"}))

    [script] = _scripts(driver)
    assert driver.kinds() == ["execute"]
    assert script.code.startswith("(params) => {") and "page.evaluate" not in script.code
    assert script.args["max_items"] == 30 and script.args["query"] == "save"
    assert script.args["viewport_only"] is False and script.args["generation_id"] == "g0"
    assert output.success and output.data["content"]["count"] == 1
    element = output.data["content"]["elements"][0]
    assert context.page_state.get_target(element["target_id"]).locator == {"selector": "#save"}
    assert output.data["content"]["diagnostics"]["parse_retry_count"] == 0


def test_probe_runs_a_second_time_after_a_malformed_result() -> None:
    driver = FakeDriver(results=[_script("not json"), _script({"elements": [dict(SAVE)]})])

    output = _run(ProbeInteractivesTool(_context(driver)).invoke({}))

    assert len(_scripts(driver)) == 2
    assert output.success and output.data["content"]["diagnostics"]["parse_retry_count"] == 1


def test_probe_reports_a_result_it_cannot_parse_twice() -> None:
    driver = FakeDriver(results=[_script("not json"), _script("still not json")])

    output = _run(ProbeInteractivesTool(_context(driver)).invoke({}))

    assert not output.success
    assert output.error == "Could not parse browser_probe_interactives result JSON"
    assert output.data["content"]["elements"] == [] and "audit" in output.data["content"]


def test_probe_reports_a_script_the_page_refused() -> None:
    driver = FakeDriver(results=[_script(None, ok=False)])

    output = _run(ProbeInteractivesTool(_context(driver)).invoke({}))

    assert not output.success and "blocked" in output.error
    assert len(_scripts(driver)) == 1


def test_probe_turns_a_driver_error_into_a_failure_envelope() -> None:
    driver = FakeDriver()
    driver.fail_next(dispatched=True)

    output = _run(ProbeInteractivesTool(_context(driver)).invoke({}))

    assert not output.success and output.error.startswith("browser_probe_interactives failed: ")
    assert output.data["content"]["elements"] == [] and output.data["content"]["page_state"]["generation_id"] == "g0"


def test_probe_on_a_new_address_advances_the_generation() -> None:
    driver = FakeDriver(
        observations=[_observation()], results=[_script({"url": SECOND, "elements": [dict(SAVE, generation_id="g0")]})]
    )
    context = _context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))

    output = _run(ProbeInteractivesTool(context).invoke({}))

    assert output.data["content"]["generation_id"] == "g1"
    assert output.data["content"]["elements"][0]["target_id"].startswith("t_g1_")
    assert context.page_state.url == SECOND


def test_probe_cards_registers_cards_and_their_primary_links(tmp_path: Path) -> None:
    card = {
        "selector_hint": ".card",
        "title": "Blue mouse",
        "primary_link": "http://scene.test/mouse",
        "match_count": 1,
    }
    driver = FakeDriver(results=[_script({"url": START, "cards": [card]})])
    context = _context(driver, tmp_path=tmp_path)

    output = _run(ProbeCardsTool(context).invoke({"max_cards": 50, "include_buttons": False}))

    [script] = _scripts(driver)
    assert script.args["max_cards"] == 12 and script.args["include_buttons"] is False
    assert output.success and output.data["content"]["count"] == 1
    assert output.data["content"]["cards"][0]["title"] == "Blue mouse"
    assert context.page_state.selector_primary_links[".card"] == (0, "http://scene.test/mouse")


# --- policy probe and activate ---------------------------------------------------------------------------------------


def test_probe_for_policy_runs_the_source_with_its_params_and_registers_targets() -> None:
    source, params = "(params) => ({})", {"max_items": 250, "stamp_attribute": "data-openjiuwen-policy"}
    driver = FakeDriver(results=[_script({"url": START, "title": "Start", "elements": [dict(SAVE)]})])
    context = _context(driver)

    result = _run(probe_for_policy(context, source, params))

    [script] = _scripts(driver)
    assert (script.code, script.args) == (source, params)
    assert result["generation_id"] == context.page_state.generation_id
    target = context.page_state.resolve_target(generation_id="g0", target_id=result["elements"][0]["target_id"])
    assert target.locator == {"selector": "#save"}


def test_probe_for_policy_returns_a_failure_envelope_when_the_script_fails() -> None:
    driver = FakeDriver()
    driver.fail_next(dispatched=True)

    result = _run(probe_for_policy(_context(driver), "(params) => ({})", {}))

    assert result["ok"] is False and result["error"].startswith("policy probe failed: ")
    assert result["elements"] == [] and "page_state" in result


def test_probe_for_policy_reports_a_script_that_says_not_ok() -> None:
    driver = FakeDriver(results=[_script({"ok": False, "error": "no form"})])

    result = _run(probe_for_policy(_context(driver), "(params) => ({})", {}))

    assert result == {"ok": False, "error": "no form", "elements": [], "page_state": result["page_state"]}


def test_activate_page_switches_to_the_tab_at_the_address() -> None:
    driver = FakeDriver(observations=[_observation()], results=[ActionResult(ok=True, context_changed=True)])

    assert _run(activate_page(_context(driver), SECOND)) is True
    assert driver.kinds() == ["observe", "execute"]
    assert driver.calls[1].payload == SwitchContext(TABS[1])


def test_activate_page_fronts_the_current_tab_for_an_unknown_address() -> None:
    driver = FakeDriver(observations=[_observation()], results=[ActionResult(ok=True, context_changed=True)])
    context = _context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))

    assert _run(activate_page(context, "http://elsewhere.test/")) is True
    assert driver.calls[-1].payload == SwitchContext(TABS[0])


def test_activate_page_returns_false_when_the_driver_fails() -> None:
    driver = FakeDriver()
    driver.fail_next(dispatched=False)

    assert _run(activate_page(_context(driver), START)) is False


# --- page metadata ---------------------------------------------------------------------------------------------------


def test_page_metadata_measures_the_page_in_one_script_and_takes_tabs_from_the_context() -> None:
    measured = {"page_position": {"scroll_y": 10}, "semantic_state": {"result_count": 4}}
    driver = FakeDriver(observations=[_observation()], results=[_script(measured)])
    context = _context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))

    metadata, error = _run(page_metadata(context))

    [script] = _scripts(driver)
    assert error is None and script.args is None
    assert script.code.startswith("() => {") and "page.context" not in script.code
    assert metadata == {
        "ok": True,
        "url": START,
        "title": "Start",
        "tabs": [
            {"index": 0, "current": True, "url": START, "title": "Start"},
            {"index": 1, "current": False, "url": SECOND, "title": "Second"},
        ],
        **measured,
    }


def test_page_metadata_marks_the_focused_tab_when_the_driver_gives_no_index() -> None:
    surface = BrowserContext(url=START, title="Start", tabs=TABS)
    driver = FakeDriver(observations=[_observation(surface=surface)], results=[_script({"page_position": {}})])
    context = _context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))

    metadata, _error = _run(page_metadata(context))

    assert [tab["current"] for tab in metadata["tabs"]] == [True, False]
    assert [tab["url"] for tab in metadata["tabs"]] == [START, ""]


def test_page_metadata_reports_a_driver_error() -> None:
    driver = FakeDriver()
    driver.fail_next(dispatched=True)

    metadata, error = _run(page_metadata(_context(driver)))

    assert metadata == {} and error.startswith("browser state metadata capture failed: ")


# --- browser_find ----------------------------------------------------------------------------------------------------


def _find_context(driver: FakeDriver, native: bool = True) -> ToolContext:
    info = DriverInfo(
        name="fake",
        surfaces=frozenset({"browser"}),
        actions=DEFAULT_ACTIONS,
        native_tools={"browser_find": FIND_INFO} if native else {},
        tool_name_prefix="srv_" if native else "",
    )
    return _context(driver, info)


def _found(context: ToolContext, args: dict[str, Any]) -> Any:
    return _run(FindTool(context).invoke(args))


def test_find_takes_name_description_and_schema_from_the_driver_tool() -> None:
    card = FindTool(_find_context(FakeDriver())).card
    assert (card.name, card.description, card.input_params) == ("srv_browser_find", "Server find.", FIND_INFO.schema)


def test_find_without_a_driver_tool_offers_its_own_schema() -> None:
    card = FindTool(_find_context(FakeDriver(), native=False)).card
    assert card.name == "browser_find"
    assert card.input_params["required"] == ["query"]
    assert set(card.input_params["properties"]) == {"query", "regex", "limit", "generation_id"}


def test_find_shows_each_match_in_context_under_its_path() -> None:
    driver = FakeDriver(observations=[_observation()])
    context = _find_context(driver)

    output = _found(context, {"text": "save ORDER"})

    assert output.success
    assert output.data["content"] == (
        '### Result\nFound 1 match for "save ORDER":\n\n'
        "- generic [ref=e1]:\n"
        "  - form [ref=e5]:\n"
        '    - option "Small"\n'
        '    - option "Medium"\n'
        '    - option "Large"\n'
        '    - button "Save order" [ref=e9]'
    )
    assert driver.kinds() == ["observe"]


def test_find_searches_the_last_observation_without_observing_again() -> None:
    driver = FakeDriver(observations=[_observation()])
    context = _find_context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))

    output = _found(context, {"regex": "/^\\s*- link/i"})

    assert driver.kinds() == ["observe"]
    assert output.data["content"].startswith("### Result\nFound 1 match for /^\\s*- link/i:\n\n")
    assert '    - link "Go to second" [ref=e4]' in output.data["content"]


def test_find_observes_again_after_a_command_sent_without_an_observation() -> None:
    driver = FakeDriver(observations=[_observation('- button "Old" [ref=e1]'), _observation()])
    context = _find_context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))
    context.page_state.record(RunScript(code="() => 1"), _script(1))

    output = _found(context, {"text": "save order"})

    assert driver.kinds() == ["observe", "observe"]
    assert output.data["content"].startswith('### Result\nFound 1 match for "save order":')


def test_find_observes_again_when_the_generation_moved_on() -> None:
    driver = FakeDriver(observations=[_observation(), _observation()])
    context = _find_context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))
    context.page_state.advance()

    _found(context, {"text": "save order"})

    assert driver.kinds() == ["observe", "observe"]


def test_find_records_the_observation_it_took_as_a_read() -> None:
    output = _found(_find_context(FakeDriver(observations=[_observation()])), {"text": "save"})

    record = command_record(output)
    assert (record.command, record.effect, record.ok, record.dispatched) == (Observe, Effect.READ, True, True)
    assert (record.value, record.generation) == (output.data["content"], "g0")


def test_find_over_the_last_observation_records_that_nothing_was_sent() -> None:
    driver = FakeDriver(observations=[_observation()])
    context = _find_context(driver)
    context.page_state.record(Observe(), _run(driver.observe(Observe())))

    assert command_record(_found(context, {"text": "save"})).dispatched is False


def test_find_that_cannot_observe_reports_and_records_the_failure() -> None:
    driver = FakeDriver()
    driver.fail_next(dispatched=False)

    output = _found(_find_context(driver), {"text": "save"})

    record = command_record(output)
    assert not output.success and output.error == "### Error\nscripted"
    assert (record.command, record.effect, record.ok, record.dispatched) == (Observe, Effect.READ, False, False)


def test_find_merges_nearby_matches_into_one_snippet() -> None:
    output = _found(_find_context(FakeDriver(observations=[_observation()])), {"regex": "option"})

    assert output.data["content"].startswith("### Result\nFound 3 matches for /option/:\n\n")
    assert "----" not in output.data["content"]


def test_find_reports_no_match() -> None:
    output = _found(_find_context(FakeDriver(observations=[_observation()])), {"text": "checkout"})
    assert output.data["content"] == '### Result\nNo matches found for "checkout".'


@pytest.mark.parametrize(
    "args, error",
    [
        ({}, 'Provide either "text" or "regex" to search for.'),
        ({"text": "a", "regex": "b"}, 'Provide only one of "text" or "regex", not both.'),
        ({"regex": "("}, "Invalid regular expression"),
        ({"query": "Save", "generation_id": "g7"}, "Stale PageState generation g7"),
    ],
)
def test_find_refuses_bad_arguments_without_observing(args: dict[str, Any], error: str) -> None:
    driver = FakeDriver()

    output = _found(_find_context(driver), args)

    assert not output.success and output.error.startswith("### Error\n") and error in output.error
    assert driver.calls == []


def test_find_takes_the_query_form_with_a_limit() -> None:
    context = _find_context(FakeDriver(observations=[_observation()]), native=False)

    plain = _found(context, {"query": "option", "limit": 2})
    regex = _found(context, {"query": "^\\s*- (link|button)", "regex": True, "generation_id": "g0"})

    assert plain.data["content"].startswith('### Result\nFound 2 matches for "option":')
    assert regex.data["content"].startswith("### Result\nFound 2 matches for /^\\s*- (link|button)/:")
