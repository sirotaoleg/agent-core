#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No whole-word browser, page, tab or url in the contract or the core, outside import statements."""

from __future__ import annotations

import re
from collections import Counter

from . import _scan

BROWSER_WORD_RE = re.compile(r"\b(?:browser|page|tab|url)\b", re.IGNORECASE)

KNOWN_BROWSER_WORDS: dict[str, int] = {
    "runtime/agents.py": 1,
    "runtime/config.py": 1,
    "runtime/rails/final_answer_recovery.py": 1,
    "runtime/rails/repeated_call_guard.py": 4,
    "runtime/worker.py": 48,
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

    def test_the_core_page_state_names_no_browser_field(self):
        fields = re.compile(r"\b(?:url|title|tabs|price|rating|shop|href|captcha|login)\b", re.IGNORECASE)
        hits = fields.findall(_text_without_imports("runtime/page_state.py"))
        assert not hits, f"runtime/page_state.py names browser fields: {sorted(set(hits))}"
