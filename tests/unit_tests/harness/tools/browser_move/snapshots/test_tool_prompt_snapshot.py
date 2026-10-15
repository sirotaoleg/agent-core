# coding: utf-8
"""Golden snapshots of the tools and system prompt the model receives."""

from __future__ import annotations

import difflib
import json
import re

import pytest

from tests.unit_tests.harness.tools.browser_move.snapshots import builder
from tests.unit_tests.harness.tools.browser_move.snapshots.test_tool_factory_golden import (
    BATCH,
    BROWSER_USE_DROPPED,
    BROWSER_USE_OUTSIDE_CORE,
    PROBES,
    SCREENSHOT,
)

VIEWS = builder.list_views()
CAPABILITY_VIEWS = [view for view in VIEWS if view not in builder.RUNNER_VIEWS]
BROWSER_USE_RUNNER_VIEWS = [v for v in builder.list_views(builder.BROWSER_USE) if v in builder.RUNNER_VIEWS]


@pytest.fixture(scope="module")
def built_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return builder.build_all(tmp_path_factory.mktemp("browser-snapshot"))


@pytest.fixture(scope="module")
def built_browser_use_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    root = tmp_path_factory.mktemp("browser-use-snapshot")
    return {view: builder.build_view(view, root / view, driver=builder.BROWSER_USE)[0] for view in CAPABILITY_VIEWS}


def _golden_agent(directory, view: str) -> dict:
    return json.loads((directory / f"{view}.json").read_text(encoding="utf-8"))["agents"][0]


def _mismatch_message(view: str, expected: str, actual: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"golden/playwright_mcp/{view}.json",
            tofile=f"built/{view}.json",
        )
    )
    return (
        f"Tool/prompt snapshot for view {view!r} changed.\n{diff}\n"
        f"If the change is intended, regenerate and justify each difference in the PR:\n"
        f"    {builder.REGENERATE_COMMAND}"
    )


def test_golden_files_cover_exactly_the_views() -> None:
    on_disk = sorted(p.stem for p in builder.GOLDEN_DIR.glob("*.json"))
    assert on_disk == sorted(VIEWS), (
        f"golden views {on_disk} != built views {sorted(VIEWS)}; run {builder.REGENERATE_COMMAND}"
    )
    browser_use_views = builder.list_views(builder.BROWSER_USE)
    on_disk = sorted(p.stem for p in builder.BROWSER_USE_GOLDEN_DIR.glob("*.json"))
    assert on_disk == sorted(browser_use_views)


@pytest.mark.parametrize("view", VIEWS)
def test_playwright_mcp_snapshot_matches_golden(view: str, built_views: dict[str, str]) -> None:
    golden = builder.GOLDEN_DIR / f"{view}.json"
    assert golden.exists(), f"missing {golden.name}; run {builder.REGENERATE_COMMAND}"
    expected = golden.read_text(encoding="utf-8")
    actual = built_views[view]
    if actual != expected:
        pytest.fail(_mismatch_message(view, expected, actual), pytrace=False)


@pytest.mark.parametrize("view", ["core", builder.RUNNER_VIEW])
def test_playwright_mcp_snapshot_is_deterministic(
    view: str, built_views: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    again, _ = builder.build_view(view, tmp_path_factory.mktemp(f"again-{view}"))
    assert builder.render(again) == built_views[view]


@pytest.mark.parametrize("view", CAPABILITY_VIEWS)
def test_browser_use_golden_is_well_formed(view: str) -> None:
    path = builder.BROWSER_USE_GOLDEN_DIR / f"{view}.json"
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert builder.render(doc) == text, "golden file is not in canonical form (sorted keys, indent 2, final newline)"
    assert set(doc) == {"agents", "driver", "source_commit", "view"}
    assert doc["driver"] == "browser_use"
    assert re.fullmatch(r"[0-9a-f]{8}", doc["source_commit"]), "the golden records the commit it was taken from"
    assert doc["view"] == view
    for agent in doc["agents"]:
        assert set(agent) == {"role", "system_prompt", "tools"}
        assert isinstance(agent["system_prompt"], str) and agent["system_prompt"]
        names = [tool["name"] for tool in agent["tools"]]
        assert names == sorted(names)
        for tool in agent["tools"]:
            assert set(tool) == {"name", "description", "parameters"}


@pytest.mark.parametrize("view", CAPABILITY_VIEWS)
def test_browser_use_tools_equal_the_golden_but_the_one_catalog_differences(
    view: str, built_browser_use_views: dict[str, dict]
) -> None:
    golden = {tool["name"]: tool for tool in _golden_agent(builder.BROWSER_USE_GOLDEN_DIR, view)["tools"]}
    built = {tool["name"]: tool for tool in built_browser_use_views[view]["agents"][0]["tools"]}

    expected_missing = BROWSER_USE_DROPPED | {SCREENSHOT}
    if view != "extended_interaction":
        expected_missing |= BROWSER_USE_OUTSIDE_CORE
    assert set(golden) - set(built) == expected_missing & set(golden)
    assert set(built) - set(golden) == PROBES | {BATCH}
    golden["browser_tabs"]["parameters"]["properties"]["action"]["enum"].append("new")
    for name, tool in built.items():
        if name in PROBES or name == BATCH:
            continue
        assert tool == golden[name], name


@pytest.mark.parametrize("view", CAPABILITY_VIEWS)
def test_browser_use_prompt_equals_the_playwright_mcp_prompt(
    view: str, built_browser_use_views: dict[str, dict]
) -> None:
    built = built_browser_use_views[view]["agents"][0]
    assert built["role"] == "main"
    assert built["system_prompt"] == _golden_agent(builder.GOLDEN_DIR, view)["system_prompt"]


@pytest.mark.parametrize("view", BROWSER_USE_RUNNER_VIEWS)
def test_browser_use_runner_view_matches_golden(view: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    golden = builder.BROWSER_USE_GOLDEN_DIR / f"{view}.json"
    assert golden.exists(), f"missing {golden.name}; run {builder.REGENERATE_COMMAND}"
    expected = golden.read_text(encoding="utf-8")
    actual = builder.render(builder.build_view(view, tmp_path_factory.mktemp(view), driver=builder.BROWSER_USE)[0])
    if actual != expected:
        pytest.fail(_mismatch_message(f"browser_use/{view}", expected, actual), pytrace=False)
