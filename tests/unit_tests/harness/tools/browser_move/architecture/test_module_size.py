#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No module in the core or in ``surfaces/`` longer than 1,500 lines; allowlisted ones may only shrink."""

from __future__ import annotations

from collections import Counter

from . import _scan

MAX_LINES = 1500

# file relative to the package -> line count.
KNOWN_OVERSIZED_MODULES: dict[str, int] = {
    "surfaces/browser/probes/cards.py": 3010,
}


def _collect_oversized_modules() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(lambda f: _scan.is_core(f) or _scan.is_surface(f)):
        lines = len(_scan.read_text(relpath).splitlines())
        if lines > MAX_LINES:
            found[relpath] = lines
    return found


class TestModuleSize:
    def test_no_oversized_modules_in_core_or_surfaces(self):
        problems = _scan.compare_with_allowlist(
            _collect_oversized_modules(), KNOWN_OVERSIZED_MODULES, f"module over {MAX_LINES} lines"
        )
        assert not problems, "\n".join(problems)
