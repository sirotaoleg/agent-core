#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""No browser words in the contract or the core (plan section 4.3, "Browser words").

The words ``browser``, ``page``, ``tab`` and ``url`` must not appear as whole words
(regex ``\\b``, case-insensitive) in ``contract/`` or in core folders: code, strings
and comments all count. ``\\b`` treats ``_`` as part of a word, so ``page_state``,
``browser_tools`` or ``PageState`` do not match, and neither do plurals (``tabs``).

Core folders are ``runtime/`` plus today's ``playwright_runtime/``,
``controllers/``, ``utils/`` and ``clients/`` until Phase 8/9 moves them (see
``_scan.py``). Counts are per file: a file whose count grows, or a new file, fails.
"""

from __future__ import annotations

import re
from collections import Counter

from . import _scan

BROWSER_WORD_RE = re.compile(r"\b(?:browser|page|tab|url)\b", re.IGNORECASE)

# file relative to the package -> number of browser-word matches.
KNOWN_BROWSER_WORDS: dict[str, int] = {
    # all removed in Phase 9 (browser code moves to surfaces/browser/)
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
    "playwright_runtime/config.py": 6,
    "playwright_runtime/final_answer_recovery.py": 2,
    "playwright_runtime/intercepted_action.py": 1,
    "playwright_runtime/main.py": 4,
    "playwright_runtime/page_state.py": 26,
    "playwright_runtime/probe_semantics.py": 2,
    "playwright_runtime/probes.py": 47,
    "playwright_runtime/profiles.py": 2,
    "playwright_runtime/repeated_call_guard.py": 10,
    "playwright_runtime/runtime.py": 148,
    "playwright_runtime/runtime_tools.py": 33,
    "playwright_runtime/semantic_state.py": 8,
    "playwright_runtime/service.py": 93,
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
