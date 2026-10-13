# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The driver conformance suite, live on ``PlaywrightMcpDriver`` with ``@playwright/mcp`` and a headless Chrome."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from openjiuwen.harness.tools.browser_move.contract.testing.conformance import DriverConformance
from openjiuwen.harness.tools.browser_move.drivers.middleware import Tracing
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.driver import (
    PlaywrightMcpDriver,
    create_playwright_mcp_driver,
)
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import _candidate_chrome_binaries
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene import browser_scene
from tests.unit_tests.harness.tools.browser_move.drivers.test_conformance_browser_use_live import (  # noqa: F401
    chrome_device,
)

pytest_plugins = ["openjiuwen.harness.tools.browser_move.contract.testing.conformance"]

SERVER_ARGS = "-y @playwright/mcp@0.0.78"


LIVE_MCP_ENV = "BROWSER_MOVE_LIVE_MCP"


def _skip_reason() -> str | None:
    if os.getenv(LIVE_MCP_ENV) != "1":
        return f"set {LIVE_MCP_ENV}=1 to run against a real @playwright/mcp server and Chrome"
    settings = BrowserSettings.from_process_env()
    if shutil.which(settings.mcp_command) is None:
        return f"{settings.mcp_command!r} is not on PATH (it starts @playwright/mcp)"
    if not _candidate_chrome_binaries(settings.browser_binary_candidates):
        return "no Chrome binary found"
    return None


@pytest.fixture(scope="class")
def mcp_settings() -> Iterator[BrowserSettings]:
    """Settings that pin the server version and give it a fresh working directory for its output files."""
    work_dir = Path(tempfile.mkdtemp(prefix="mcp-conformance-"))
    try:
        environ = {"PLAYWRIGHT_MCP_ARGS": SERVER_ARGS, "PLAYWRIGHT_RUNTIME_MCP_CWD": str(work_dir)}
        yield BrowserSettings.from_env(environ)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.live
@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
class TestPlaywrightMcpConformance(DriverConformance):
    scene = browser_scene()
    driver_actions = PlaywrightMcpDriver.handled_actions()

    @pytest.fixture
    def conformance_driver_factory(self, chrome_device: ProvisionedDevice, mcp_settings: BrowserSettings):  # noqa: F811
        return lambda sink: Tracing(create_playwright_mcp_driver(chrome_device, mcp_settings), sink)
