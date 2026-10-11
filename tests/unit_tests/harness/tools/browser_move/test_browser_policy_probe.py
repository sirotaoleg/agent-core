#!/usr/bin/env python
# coding: utf-8
"""Runtime hooks used by decision policies: probe_for_policy registers targets, activate_page fronts the tab."""
# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.contract import (
    ContextRef,
    DriverError,
    Observation,
    RunScript,
    SwitchContext,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import (
    fake_assembly,
    make_runtime,
    mcp_fake_driver,
    result,
    run,
)

_SOURCE = "(params) => ({})"
_PARAMS = {"max_items": 250, "stamp_attribute": "data-openjiuwen-policy"}
_ELEMENT = {
    "node": 7,
    "selector_hint": '[data-openjiuwen-policy="7"]',
    "selector_hint_validated": True,
    "match_count": 1,
    "role": "combobox",
    "label": "Where to?",
    "text": "Where to?",
    "editable": True,
    "visible": True,
    "enabled": True,
    "actionable": True,
    "clickable": True,
}
_TABS = (ContextRef("0", "tab", "Flights"), ContextRef("1", "tab", "Hotels"))
_FLIGHTS = "https://flights.test/"
_HOTELS = "https://hotels.test/"


def _scripts(driver) -> list[RunScript]:
    return [call.payload for call in driver.calls if isinstance(call.payload, RunScript)]


def test_probe_for_policy_registers_elements_as_targets_and_passes_params() -> None:
    probe = {"url": _FLIGHTS, "title": "Flights", "elements": [dict(_ELEMENT)]}
    driver = mcp_fake_driver(results=[result(value=probe)])
    runtime = make_runtime(driver)

    probed = run(runtime.probe_for_policy(_SOURCE, _PARAMS))

    element = probed["elements"][0]
    assert element["target_id"].startswith("t_g"), "probe elements must become PageState targets"
    assert probed["generation_id"] == runtime.generation_id
    assert _scripts(driver) == [RunScript(code=_SOURCE, args=_PARAMS)]
    target = runtime.resolve_model_target_id(element["target_id"])
    assert target.locator.get("selector") == _ELEMENT["selector_hint"]


def test_probe_for_policy_returns_a_failure_envelope_when_the_script_raises() -> None:
    driver = mcp_fake_driver()
    driver.fail_next(DriverError("detached frame", dispatched=True))

    probed = run(make_runtime(driver).probe_for_policy(_SOURCE, _PARAMS))

    assert probed["ok"] is False
    assert "detached frame" in probed["error"]
    assert probed["elements"] == []


def test_probe_for_policy_starts_the_driver_session_on_demand() -> None:
    runtime = BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=McpServerConfig(server_id="t", server_name="t", server_path="stdio://playwright", client_type="stdio"),
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )
    driver = mcp_fake_driver(results=[result(value={"url": _FLIGHTS, "elements": []})] * 2)
    assemble = AsyncMock(return_value=fake_assembly(driver))

    with patch.object(assembly_module, "assemble_driver", assemble):
        probed = run(runtime.probe_for_policy(_SOURCE, _PARAMS))
        run(runtime.probe_for_policy(_SOURCE, _PARAMS))

    assert probed["ok"] is True
    assemble.assert_awaited_once()
    assert runtime.session is not None


def _two_tabs(current: int) -> Observation:
    surface = BrowserContext(
        url=(_FLIGHTS, _HOTELS)[current], tabs=_TABS, tab_urls=(_FLIGHTS, _HOTELS), current_tab=current
    )
    return Observation(context=_TABS[current], native_text="", surface=surface)


def test_activate_page_brings_the_page_at_url_to_the_front() -> None:
    driver = mcp_fake_driver(observations=[_two_tabs(0)], results=[result(url=_HOTELS, context_changed=True)])

    assert run(make_runtime(driver).activate_page(_HOTELS)) is True

    switched = [call.payload for call in driver.calls if isinstance(call.payload, SwitchContext)]
    assert switched == [SwitchContext(_TABS[1])]


def test_activate_page_returns_false_when_the_script_fails() -> None:
    driver = mcp_fake_driver()
    driver.fail_next(DriverError("no page", dispatched=False))

    assert run(make_runtime(driver).activate_page(_HOTELS)) is False
