# coding: utf-8
"""Rewrite the Playwright MCP goldens and the browser-use runner views by hand; ``--results``: result goldens."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tests.unit_tests.harness.tools.browser_move.snapshots.builder import (
    BROWSER_USE,
    BROWSER_USE_GOLDEN_DIR,
    GOLDEN_DIR,
    RUNNER_VIEWS,
    build_all,
    build_view,
    list_views,
    render,
)
from tests.unit_tests.harness.tools.browser_move.snapshots.results import (
    RESULTS_DIR,
    list_cases,
    model_text_diff,
    render_case,
)


def main() -> int:
    browser_use_views = list_views(BROWSER_USE)
    with tempfile.TemporaryDirectory(prefix="browser-snapshot-", ignore_cleanup_errors=True) as tmp:
        texts = build_all(Path(tmp))
        runner_texts = {
            view: render(build_view(view, Path(tmp) / BROWSER_USE / view, driver=BROWSER_USE)[0])
            for view in browser_use_views
            if view in RUNNER_VIEWS
        }
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in sorted(set(p.stem for p in GOLDEN_DIR.glob("*.json")) - set(texts)):
        (GOLDEN_DIR / f"{stale}.json").unlink()
        print(f"removed {stale}.json")
    for view, text in texts.items():
        path = GOLDEN_DIR / f"{view}.json"
        changed = not path.exists() or path.read_text(encoding="utf-8") != text
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{'updated' if changed else 'unchanged'} {view}.json")
    for stale in sorted(set(p.stem for p in BROWSER_USE_GOLDEN_DIR.glob("*.json")) - set(browser_use_views)):
        (BROWSER_USE_GOLDEN_DIR / f"{stale}.json").unlink()
        print(f"removed {BROWSER_USE}/{stale}.json")
    for view, text in runner_texts.items():
        path = BROWSER_USE_GOLDEN_DIR / f"{view}.json"
        changed = not path.exists() or path.read_text(encoding="utf-8") != text
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{'updated' if changed else 'unchanged'} {BROWSER_USE}/{view}.json")
    return 0


def main_results() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cases = list_cases()
    for stale in sorted(set(p.stem for p in RESULTS_DIR.glob("*.txt")) - set(cases)):
        (RESULTS_DIR / f"{stale}.txt").unlink()
        print(f"removed {stale}.txt")
    with tempfile.TemporaryDirectory(prefix="browser-results-", ignore_cleanup_errors=True) as tmp:
        for case in cases:
            text = render_case(case, Path(tmp))
            path = RESULTS_DIR / f"{case}.txt"
            changed = not path.exists() or path.read_text(encoding="utf-8") != text
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"{'updated' if changed else 'unchanged'} {case}.txt")
            print(model_text_diff(case, text) or "(same as the recorded model text)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_results() if "--results" in sys.argv[1:] else main())
