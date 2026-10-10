# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Golden text the model reads for every recorded Playwright MCP answer."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_tests.harness.tools.browser_move.snapshots import results

CASES = results.list_cases()


def test_result_goldens_cover_exactly_the_recorded_fixtures() -> None:
    on_disk = sorted(path.stem for path in results.RESULTS_DIR.glob("*.txt"))
    assert on_disk == CASES, f"run {results.REGENERATE_COMMAND}"


@pytest.mark.parametrize("case", CASES)
def test_rendered_answer_equals_the_golden_byte_for_byte(case: str, tmp_path: Path) -> None:
    golden = results.RESULTS_DIR / f"{case}.txt"
    assert golden.exists(), f"missing {golden.name}; run {results.REGENERATE_COMMAND}"
    expected = golden.read_text(encoding="utf-8")
    actual = results.render_case(case, tmp_path)
    if actual != expected:
        pytest.fail(
            f"{case}: rendering changed; if intended, run {results.REGENERATE_COMMAND} and justify the diff "
            f"against the recorded model text:\n{results.model_text_diff(case, actual)}",
            pytrace=False,
        )
