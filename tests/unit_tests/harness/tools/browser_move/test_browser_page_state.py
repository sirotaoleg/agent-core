#!/usr/bin/env python
# coding: utf-8
"""Regression tests for the PageState-to-Batch target contract."""
# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import make_runtime
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import run as _run


def _interactive(selector: str, text: str) -> dict:
    return {
        "role": "button",
        "text": text,
        "selector_hint": selector,
        "selector_hint_validated": True,
        "match_count": 1,
        "visible": True,
        "enabled": True,
        "actionable": True,
        "clickable": True,
    }


def test_probe_targets_and_compact_page_state_share_one_contract() -> None:
    state = BrowserPageState(page_id="page-test")
    payload = {
        "url": "https://example.test/search",
        "title": "Search",
        "elements": [
            _interactive("#query", "Search"),
            {
                "role": "tab",
                "text": "Sales",
                "selector_hint": "text=Sales",
                "selector_hint_validated": False,
                "match_count": 2,
                "visible": True,
                "enabled": True,
                "actionable": False,
                "clickable": False,
            },
        ],
    }

    state.register_interactives(payload)

    safe_target_id = payload["elements"][0]["target_id"]
    assert "target_id" not in payload["elements"][1]
    assert payload["elements"][0]["generation_id"] == "g0"
    assert state.export() == {
        "page_id": "page-test",
        "generation_id": "g0",
        "url": "https://example.test/search",
        "title": "Search",
        "interactives": [
            {
                "target_id": safe_target_id,
                "generation_id": "g0",
                "role": "button",
                "text": "Search",
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
            }
        ],
        "cards": [],
        "field_coverage": [],
        "blockers": [],
    }
    resolved = state.resolve_target(generation_id="g0", target_id=safe_target_id)
    assert resolved is not None
    assert resolved.locator == {"selector": "#query"}


def test_cards_add_primary_link_and_field_coverage_to_page_state() -> None:
    state = BrowserPageState(page_id="page-results", generation=4)
    payload = {
        "url": "https://shop.example.test/results",
        "title": "Headphones",
        "cards": [
            {
                "title": "Bluetooth Headphones",
                "price": "$99",
                "source": "Example Shop",
                "summary": "Low-latency wireless headphones",
                "primary_link": "https://shop.example.test/item/1",
                "selector_hint": ".card:nth-of-type(1)",
                "selector_hint_validated": True,
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": False,
                "clickable": False,
            }
        ],
    }

    state.register_cards(payload)

    card = payload["cards"][0]
    exported = state.export()
    assert card["generation_id"] == "g4"
    assert exported["cards"][0]["target_id"] == card["target_id"]
    assert exported["cards"][0]["href"] == "https://shop.example.test/item/1"
    assert exported["field_coverage"] == ["price", "primary_link", "source", "summary", "title"]


def test_card_detail_fields_register_generation_scoped_read_only_targets() -> None:
    state = BrowserPageState(page_id="page-detail", generation=2)
    state.set_requested_fields(["author", "likes"])
    payload = {
        "cards": [
            {
                "title": "Article",
                "author": "Alice",
                "likes": None,
                "field_status": {"author": "present", "likes": "missing"},
                "field_provenance": {
                    "author": {
                        "selector": ".article-author",
                        "raw_text": "Alice",
                        "generation_id": "g2",
                        "source": "browser_probe_cards",
                    }
                },
                "selector_metadata": {
                    "author": {
                        "selector_hint": ".article-author",
                        "match_count": 1,
                        "visible": True,
                        "enabled": True,
                        "generation_id": "g2",
                    }
                },
            }
        ]
    }

    state.register_cards(payload)

    exported_card = state.export()["cards"][0]
    author_target = exported_card["field_targets"]["author"]
    assert author_target["generation_id"] == "g2"
    assert author_target["field"] == "author"
    assert author_target["extractable"] is True
    assert author_target["read_only"] is True
    assert author_target["actionable"] is False
    resolved = state.resolve_target(generation_id="g2", target_id=author_target["target_id"])
    assert resolved is not None
    assert resolved.locator == {"selector": ".article-author"}
    assert exported_card["field_status"]["likes"] == "missing"


def test_page_state_preserves_probe_region_kind_and_dynamic_target_semantics() -> None:
    state = BrowserPageState(page_id="page-semantic")
    dynamic = _interactive("[data-date='2026-08-09']", "9")
    dynamic.update({"region": "main", "kind": "calendar_date"})
    state.register_interactives({"elements": [dynamic]})
    state.register_cards(
        {
            "cards": [
                {
                    "region": "main_result",
                    "kind": "product",
                    "result_index": 1,
                    "is_ad": False,
                    "title": "Bluetooth Headphones",
                    "primary_link": "https://item.taobao.com/item.htm?id=1",
                }
            ]
        }
    )

    exported = state.export()
    assert exported["interactives"][0]["kind"] == "calendar_date"
    assert exported["interactives"][0]["region"] == "main"
    assert exported["cards"][0]["region"] == "main_result"
    assert exported["cards"][0]["kind"] == "product"
    assert exported["cards"][0]["result_index"] == 1
    assert exported["cards"][0]["is_ad"] is False


def test_page_state_blockers_do_not_treat_a_login_link_as_login_required() -> None:
    state = BrowserPageState(page_id="page-login")
    state.register_interactives(
        {
            "title": "Public results",
            "elements": [_interactive("#login", "Login")],
        }
    )
    assert state.export()["blockers"] == []

    state.register_interactives(
        {
            "title": "Please sign in to continue",
            "elements": [],
        }
    )
    assert state.export()["blockers"] == ["login_required"]


def test_navigation_hard_invalidates_ax_refs_targets_and_selectors() -> None:
    state = BrowserPageState(page_id="page-ax")
    registered = state.register_ax_snapshot('- tab "Sales" [ref=f1e174]')
    state.register_interactives({"elements": [_interactive("#query", "Search")]})
    target_id = state.export()["interactives"][-1]["target_id"]

    assert registered == ("f1e174",)
    assert state.resolve_target(generation_id="g0", ref="f1e174") is not None
    assert state.resolve_target(generation_id="g0", selector="#query") is not None

    state.advance(url="https://example.test/results")

    with pytest.raises(ValueError, match="Stale PageState target|Stale target_id"):
        state.resolve_target(generation_id="g1", target_id=target_id)
    with pytest.raises(ValueError, match="Stale AX ref"):
        state.resolve_target(generation_id="g1", ref="f1e174")
    with pytest.raises(ValueError, match="Stale selector"):
        state.resolve_target(generation_id="g1", selector="#query")


def test_ax_ref_reused_after_navigation_gets_a_new_current_target() -> None:
    state = BrowserPageState(page_id="page-ref-reuse")
    state.register_ax_snapshot('- button "Old action" [ref=e1]')
    old_target_id = state.export()["interactives"][0]["target_id"]

    state.advance(url="https://example.test/new-page")
    state.register_ax_snapshot('- button "New action" [ref=e1]')

    new_target = state.resolve_target(generation_id="g1", ref="e1")
    assert new_target is not None
    assert new_target.generation_id == "g1"
    assert new_target.target_id != old_target_id


def test_stale_probe_target_refreshes_only_to_unique_current_equivalent() -> None:
    state = BrowserPageState(page_id="page-refresh")
    first = _interactive("[data-date='2026-08-26']", "26")
    first.update({"kind": "calendar_date", "region": "hotel_search"})
    state.register_interactives({"elements": [first]})
    stale_target_id = first["target_id"]

    state.advance(url="https://hotels.ctrip.com/list")
    current = _interactive("[data-date='2026-08-26']", "26")
    current.update({"kind": "calendar_date", "region": "hotel_search"})
    state.register_interactives({"elements": [current]})

    assert state.refresh_target_id(stale_target_id) == current["target_id"]


def test_stale_ax_target_and_ambiguous_probe_target_cannot_refresh() -> None:
    state = BrowserPageState(page_id="page-refresh-reject")
    state.register_ax_snapshot('- button "Continue" [ref=e1]')
    stale_ax_target = state.export()["interactives"][0]["target_id"]
    old = _interactive(".rating", "5 stars")
    old.update({"kind": "rating_filter", "region": "main"})
    state.register_interactives({"elements": [old]})
    stale_probe_target = old["target_id"]

    state.advance(url="https://example.test/results")
    first = _interactive("#rating-a", "5 stars")
    second = _interactive("#rating-b", "5 stars")
    for target in (first, second):
        target.update({"kind": "rating_filter", "region": "main"})
    state.register_interactives({"elements": [first, second]})

    with pytest.raises(ValueError, match="Stale AX target_id"):
        state.refresh_target_id(stale_ax_target)
    with pytest.raises(ValueError, match="ambiguous"):
        state.refresh_target_id(stale_probe_target)


def test_compact_page_state_never_exposes_probe_local_id_or_ax_ref() -> None:
    state = BrowserPageState(page_id="page-compact")
    payload = {"elements": [_interactive("#query", "Search")]}
    payload["elements"][0]["id"] = "local-probe-id"
    state.register_interactives(payload)
    state.register_ax_snapshot('textbox "Search" [ref=f1e2]')

    serialized = str(state.export())

    assert "local-probe-id" not in serialized
    assert "f1e2" not in serialized
    assert "target_id" in serialized


def test_replacing_ax_snapshot_preserves_present_refs_and_removes_missing_refs() -> None:
    state = BrowserPageState(page_id="page-ref-replacement")
    state.replace_ax_snapshot(
        "\n".join(
            [
                '- textbox "Email" [ref=e1]',
                '- button "Continue" [ref=e2]',
            ]
        )
    )
    email_target = state.resolve_target(generation_id="g0", ref="e1")
    assert email_target is not None

    state.replace_ax_snapshot(
        "\n".join(
            [
                '- textbox "Email" [ref=e1]',
                '- button "Submit" [ref=e3]',
            ]
        )
    )

    assert state.resolve_target(generation_id="g0", ref="e1") is email_target
    assert state.resolve_target(generation_id="g0", ref="e3") is not None
    with pytest.raises(ValueError, match="Unknown AX ref: e2"):
        state.resolve_target(generation_id="g0", ref="e2")


def test_runtime_reset_invalidates_current_page_state() -> None:
    runtime = make_runtime()
    runtime._service = SimpleNamespace(reset=AsyncMock(), page_state=runtime.page_state)
    payload = {"elements": [_interactive("#query", "Search")]}
    runtime.page_state.register_interactives(payload)
    target_id = payload["elements"][0]["target_id"]

    _run(runtime.reset())

    runtime._service.reset.assert_awaited_once()
    assert runtime.generation_id == "g1"
    with pytest.raises(ValueError, match="Stale target_id"):
        runtime.page_state.resolve_target(
            generation_id="g1",
            target_id=target_id,
        )


def test_runtime_reset_failure_still_invalidates_current_page_state() -> None:
    runtime = make_runtime()
    runtime._service = SimpleNamespace(
        reset=AsyncMock(side_effect=RuntimeError("reset failed")), page_state=runtime.page_state
    )

    with pytest.raises(RuntimeError, match="reset failed"):
        _run(runtime.reset())

    assert runtime.generation_id == "g1"
