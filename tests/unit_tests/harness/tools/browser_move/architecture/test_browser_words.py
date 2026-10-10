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
    "clients/logging_utils.py": 5,
    "controllers/action.py": 129,
    "controllers/base.py": 1,
    "playwright_runtime/agents.py": 23,
    "playwright_runtime/browser_capabilities.py": 12,
    "playwright_runtime/browser_logging.py": 8,
    "playwright_runtime/browser_state_context_processor.py": 37,
    "playwright_runtime/browser_tools.py": 10,
    "playwright_runtime/browser_working_context.py": 17,
    "playwright_runtime/browser_working_context_processor.py": 3,
    "playwright_runtime/browser_working_context_rail.py": 1,
    "playwright_runtime/config.py": 9,
    "playwright_runtime/final_answer_recovery.py": 2,
    "playwright_runtime/intercepted_action.py": 1,
    "playwright_runtime/main.py": 3,
    "playwright_runtime/page_state.py": 26,
    "playwright_runtime/probe_semantics.py": 2,
    "playwright_runtime/probes.py": 47,
    "playwright_runtime/profiles.py": 2,
    "playwright_runtime/repeated_call_guard.py": 10,
    "playwright_runtime/runtime.py": 148,
    "playwright_runtime/runtime_tools.py": 33,
    "playwright_runtime/semantic_state.py": 8,
    "playwright_runtime/service.py": 92,
    "playwright_runtime/service_registry.py": 4,
    "playwright_runtime/site_profiles.py": 22,
    "playwright_runtime/status_logging.py": 19,
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
