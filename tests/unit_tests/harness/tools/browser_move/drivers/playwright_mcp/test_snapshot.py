# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The Playwright MCP snapshot parser on recorded @playwright/mcp answers."""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.harness.tools.browser_move.contract import ContextRef, Element
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import (
    parse_elements,
    parse_snapshot,
    parse_tabs,
    split_sections,
    without_boxes,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "mcp"


def _text(case: str) -> str:
    fixture = json.loads((FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8"))
    return "\n".join(block["text"] for block in fixture["raw_result"]["content"] if block["type"] == "text")


def _by_ref(elements: tuple[Element, ...]) -> dict[str, Element]:
    return {element.ref.ref: element for element in elements}


def test_form_page_has_every_ref_including_the_iframe() -> None:
    elements, _ = parse_snapshot(_text("snapshot_form_page"))

    assert [element.ref.ref for element in elements] == [f"e{n}" for n in range(1, 19)] + ["f1e1", "f1e2", "f1e3"]
    assert all(element.ref.generation == "" for element in elements)


def test_form_page_roles_and_names() -> None:
    elements = _by_ref(parse_snapshot(_text("snapshot_form_page"))[0])

    assert [(e.role, e.name) for e in elements.values()][:6] == [
        ("generic", ""),
        ("heading", "Fixture form page"),
        ("navigation", ""),
        ("link", "Second page"),
        ("link", "Blocked page"),
        ("link", "Download report"),
    ]
    assert (elements["e18"].role, elements["f1e3"].role, elements["f1e3"].name) == ("iframe", "button", "Frame button")


def test_form_page_attributes_properties_and_text_go_to_extra() -> None:
    elements = _by_ref(parse_snapshot(_text("snapshot_form_page"))[0])

    assert elements["e2"].extra == {"level": "1"}
    assert elements["e4"].extra == {"cursor": "pointer", "url": "second.html"}
    assert elements["e8"].extra == {"placeholder": "Your name"}
    assert elements["e13"].extra == {"text": "Not saved"}
    assert elements["f1e2"].extra == {"text": "Inside the frame"}


def test_form_page_values_focus_and_bounds() -> None:
    elements = _by_ref(parse_snapshot(_text("snapshot_form_page"))[0])

    assert elements["e8"].value is None
    assert elements["e9"].value == "Small"
    assert elements["e1"].focused is True
    assert not any(element.focused for ref, element in elements.items() if ref != "e1")
    assert all(element.enabled and element.bounds is None for element in elements.values())


def test_form_page_header() -> None:
    _, header = parse_snapshot(_text("snapshot_form_page"))

    tab = ContextRef(id="0", kind="tab", title="Fixture form page")
    assert header is not None
    assert (header.url, header.title, header.tabs, header.focused) == (
        "http://127.0.0.1:<PORT>/index.html",
        "Fixture form page",
        (tab,),
        tab,
    )


def test_boxes_become_bounds() -> None:
    elements = _by_ref(parse_snapshot(_text("snapshot_with_boxes"))[0])

    assert len(elements) == 21
    assert elements["f7e2"].bounds == (8.0, 21.0, 1264.0, 37.0)
    assert elements["f8e3"].bounds == (8.0, 50.0, 95.0, 21.0)
    assert "box" not in elements["f7e2"].extra


def test_without_boxes_removes_only_the_boxes() -> None:
    text = _text("snapshot_with_boxes")

    plain = without_boxes(text)

    assert "[box=" not in plain
    assert plain.count("[ref=") == text.count("[ref=")
    assert '  - heading "Fixture form page" [level=1] [ref=f7e2]\n' in plain


def test_filled_form_values_and_checked_state() -> None:
    elements = _by_ref(parse_snapshot(_text("snapshot_after_fill_form"))[0])

    assert elements["f7e8"].value == "Ada"
    assert elements["f7e9"].value == "Medium"
    assert elements["f7e11"].extra == {"checked": True}
    assert elements["f7e11"].focused is True


def test_quoted_yaml_lines_are_unquoted() -> None:
    elements = _by_ref(parse_snapshot(_text("snapshot_two_tabs"))[0])

    assert (elements["f9e5"].role, elements["f9e5"].name) == ("button", "Note: read me")
    assert elements["f9e6"].extra == {"text": "42"}


def test_open_tabs_section_lists_every_tab() -> None:
    _, header = parse_snapshot(_text("snapshot_two_tabs"))

    assert header is not None
    assert header.tabs == (
        ContextRef(id="0", kind="tab", title="Fixture popup page"),
        ContextRef(id="1", kind="tab", title="Fixture second page"),
    )
    assert header.focused == header.tabs[0]
    assert header.url == "http://127.0.0.1:<PORT>/popup.html"


def test_tab_list_result_gives_tabs_and_the_focused_page() -> None:
    header = parse_tabs(split_sections(_text("tabs_new"))["Result"])

    assert header is not None
    assert header.tabs == (
        ContextRef(id="0", kind="tab", title="Fixture second page"),
        ContextRef(id="1", kind="tab", title=""),
    )
    assert (header.focused, header.url, header.title) == (header.tabs[1], "about:blank", "")


def test_tab_list_without_tabs_is_none() -> None:
    assert parse_tabs("No open tabs. Navigate to a URL to create one.") is None


def test_disabled_flag_and_nodes_without_ref() -> None:
    elements = parse_elements('- button "Disabled button" [disabled] [ref=f5e4]\n- text: no ref\n- option "Small"')

    assert len(elements) == 1
    assert (elements[0].ref.ref, elements[0].enabled, elements[0].extra) == ("f5e4", False, {"disabled": True})


def test_sections_keep_their_order() -> None:
    assert list(split_sections(_text("click_opens_tab"))) == ["Ran Playwright code", "Open tabs", "Page", "Snapshot"]
