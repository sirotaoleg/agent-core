# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "OPENJIUWEN_BROWSER_AGENT_LOG_FILE", str(Path(tempfile.mkdtemp(prefix="browser-agent-log-")) / "browser_agent.log")
)

from openjiuwen.harness.tools.browser_move.runtime import topology  # noqa: E402

_TEST_ROOT = Path(__file__).parent
_SCAFFOLDING = ("AGENT.md", "SOUL.md")


@pytest.fixture(autouse=True)
def _no_image_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(topology, "probe_image_support", AsyncMock(return_value=None))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    stray = sorted(str(path.relative_to(_TEST_ROOT)) for name in _SCAFFOLDING for path in _TEST_ROOT.rglob(name))
    if stray:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"agent workspace files written into the test tree: {stray}", red=True)
