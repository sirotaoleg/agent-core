#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No whole-word browser, page, tab or url in the contract or the core, comments and strings included;
import statements are the import-boundary rule's business and are not counted."""

from __future__ import annotations

import re
from collections import Counter

from . import _scan

BROWSER_WORD_RE = re.compile(r"\b(?:browser|page|tab|url)\b", re.IGNORECASE)

# file relative to the package -> number of browser-word matches.
KNOWN_BROWSER_WORDS: dict[str, int] = {
    # browser code that moves to surfaces/browser/
    "runtime/controllers/action.py": 13,
    "runtime/controllers/base.py": 1,
    "runtime/agents.py": 2,
    "runtime/browser_logging.py": 6,
    "runtime/config.py": 9,
    "runtime/main.py": 3,
    "runtime/page_state.py": 29,
    "runtime/rails/final_answer_recovery.py": 2,
    "runtime/rails/repeated_call_guard.py": 8,
    "runtime/session.py": 39,
    "runtime/runtime_tools.py": 5,
    "runtime/service.py": 10,
    "runtime/worker.py": 72,
    "runtime/service_registry.py": 4,
}


def _text_without_imports(relpath: str) -> str:
    lines = _scan.read_text(relpath).splitlines()
    for node, _lazy in _scan.iter_imports(_scan.parse(relpath)):
        for lineno in range(node.lineno, node.end_lineno + 1):
            lines[lineno - 1] = ""
    return "\n".join(lines)


def _collect_browser_words() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(lambda f: _scan.is_contract(f) or _scan.is_core(f)):
        hits = len(BROWSER_WORD_RE.findall(_text_without_imports(relpath)))
        if hits:
            found[relpath] = hits
    return found


class TestBrowserWords:
    def test_no_browser_words_in_contract_or_core(self):
        problems = _scan.compare_with_allowlist(_collect_browser_words(), KNOWN_BROWSER_WORDS, "browser word")
        assert not problems, "\n".join(problems)
