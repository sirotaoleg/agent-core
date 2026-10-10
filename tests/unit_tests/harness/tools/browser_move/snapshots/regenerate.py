# coding: utf-8
"""Rewrite the Playwright MCP golden files from the current code; run by hand, never automatically.

``--results`` rewrites the tool-result goldens instead and prints each one's diff against the recorded model text.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tests.unit_tests.harness.tools.browser_move.snapshots.builder import GOLDEN_DIR, build_all
from tests.unit_tests.harness.tools.browser_move.snapshots.results import (
    RESULTS_DIR,
    list_cases,
    model_text_diff,
    render_case,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="browser-snapshot-", ignore_cleanup_errors=True) as tmp:
        texts = build_all(Path(tmp))
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in sorted(set(p.stem for p in GOLDEN_DIR.glob("*.json")) - set(texts)):
        (GOLDEN_DIR / f"{stale}.json").unlink()
        print(f"removed {stale}.json")
    for view, text in texts.items():
        path = GOLDEN_DIR / f"{view}.json"
        changed = not path.exists() or path.read_text(encoding="utf-8") != text
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{'updated' if changed else 'unchanged'} {view}.json")
    return 0


def main_results() -> int:
    # Playwright call logs contain characters a Windows console code page cannot print.
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
