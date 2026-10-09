# coding: utf-8
"""Rewrite the Playwright MCP golden files from the current code.

    uv run python -m tests.unit_tests.harness.tools.browser_move.snapshots.regenerate

Never run automatically. A PR that changes a golden file must list and justify each
difference. The browser-use golden files are recorded on 857239db and are not touched.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tests.unit_tests.harness.tools.browser_move.snapshots.builder import GOLDEN_DIR, build_all


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


if __name__ == "__main__":
    sys.exit(main())
