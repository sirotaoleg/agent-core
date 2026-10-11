#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No whole-word browser, page, tab or url in the contract or the core, comments and strings included."""

from __future__ import annotations

import re
from collections import Counter

from . import _scan

BROWSER_WORD_RE = re.compile(r"\b(?:browser|page|tab|url)\b", re.IGNORECASE)

# file relative to the package -> number of browser-word matches.
KNOWN_BROWSER_WORDS: dict[str, int] = {
    # browser code that moves to surfaces/browser/
    "runtime/controllers/action.py": 129,
    "runtime/controllers/base.py": 1,
    "runtime/agents.py": 23,
    "runtime/browser_capabilities.py": 12,
    "runtime/browser_logging.py": 8,
    "runtime/browser_state_context_processor.py": 36,
    "runtime/browser_working_context.py": 17,
    "runtime/browser_working_context_processor.py": 3,
    "runtime/browser_working_context_rail.py": 1,
    "runtime/config.py": 9,
    "runtime/final_answer_recovery.py": 2,
    "runtime/main.py": 3,
    "runtime/page_state.py": 29,
    "runtime/probe_semantics.py": 2,
    "runtime/probes.py": 47,
    "runtime/profiles.py": 2,
    "runtime/repeated_call_guard.py": 8,
    "runtime/runtime.py": 96,
    "runtime/runtime_tools.py": 33,
    "runtime/semantic_state.py": 8,
    "runtime/service.py": 82,
    "runtime/service_registry.py": 4,
    "runtime/site_profiles.py": 22,
    "runtime/status_logging.py": 19,
}


def _collect_browser_words() -> Counter:
    found: Counter = Counter()
    for relpath in _scan.files_where(lambda f: _scan.is_contract(f) or _scan.is_core(f)):
        hits = len(BROWSER_WORD_RE.findall(_scan.read_text(relpath)))
        if hits:
            found[relpath] = hits
    return found


class TestBrowserWords:
    def test_no_browser_words_in_contract_or_core(self):
        problems = _scan.compare_with_allowlist(_collect_browser_words(), KNOWN_BROWSER_WORDS, "browser word")
        assert not problems, "\n".join(problems)
