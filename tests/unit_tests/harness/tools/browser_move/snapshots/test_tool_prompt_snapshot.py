# coding: utf-8
"""Golden snapshots of the tools and system prompt the model receives."""

from __future__ import annotations

import difflib
import json

import pytest

from tests.unit_tests.harness.tools.browser_move.snapshots import builder

VIEWS = builder.list_views()


@pytest.fixture(scope="module")
def built_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return builder.build_all(tmp_path_factory.mktemp("browser-snapshot"))


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


@pytest.mark.parametrize("view", VIEWS)
def test_browser_use_golden_is_well_formed(view: str) -> None:
    path = builder.BROWSER_USE_GOLDEN_DIR / f"{view}.json"
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert builder.render(doc) == text, "golden file is not in canonical form (sorted keys, indent 2, final newline)"
    assert set(doc) == {"agents", "driver", "source_commit", "view"}
    assert doc["driver"] == "browser_use"
    assert doc["source_commit"] == "857239db"
    assert doc["view"] == view
    for agent in doc["agents"]:
        assert set(agent) == {"role", "system_prompt", "tools"}
        assert isinstance(agent["system_prompt"], str) and agent["system_prompt"]
        names = [tool["name"] for tool in agent["tools"]]
        assert names == sorted(names)
        for tool in agent["tools"]:
            assert set(tool) == {"name", "description", "parameters"}
    # SKIPPED rather than XFAIL, so a malformed golden file still fails loudly.
    pytest.skip("the agent does not build browser-use views on the driver contract yet")
