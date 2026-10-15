# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The driver conformance suite, live on ``BrowserUseDriver`` with the sidecar and a local headless Chrome."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from openjiuwen.harness.tools.browser_move.contract.testing.conformance import DriverConformance
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import (
    BrowserUseDriver,
    create_browser_use_driver,
)
from openjiuwen.harness.tools.browser_move.drivers.middleware import Tracing
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import _candidate_chrome_binaries
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene import browser_scene
from tests.unit_tests.harness.tools.browser_move.drivers.browser_scene_extras import BrowserSceneExtras

pytest_plugins = ["openjiuwen.harness.tools.browser_move.contract.testing.conformance"]

_SIDECAR_PYTHON_ENV = "BROWSER_USE_SIDECAR_PYTHON"
_CHROME_READY_TIMEOUT_S = 30.0


def _chrome_binaries() -> list[str]:
    return _candidate_chrome_binaries(BrowserSettings.from_process_env().browser_binary_candidates)


def _skip_reason() -> str | None:
    sidecar_python = os.getenv(_SIDECAR_PYTHON_ENV, "")
    if not sidecar_python:
        return f"{_SIDECAR_PYTHON_ENV} is not set (the browser-use sidecar interpreter)"
    if not Path(sidecar_python).is_file():
        return f"{_SIDECAR_PYTHON_ENV}={sidecar_python!r} is not an existing interpreter"
    if not _chrome_binaries():
        return "no Chrome binary found"
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_cdp(endpoint: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + _CHROME_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Chrome exited with code {process.returncode} before CDP was ready")
        try:
            with urlopen(f"{endpoint}/json/version", timeout=1.0) as response:  # nosec
                json.load(response)
                return
        except (URLError, OSError, ValueError):
            time.sleep(0.2)
    raise RuntimeError(f"Chrome CDP endpoint {endpoint} not ready after {_CHROME_READY_TIMEOUT_S}s")


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the Chrome we started and its child processes, nothing else."""
    if os.name == "nt":
        subprocess.run(  # noqa: S603, S607
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        process.kill()
    process.wait(timeout=10)


@pytest.fixture(scope="class")
def chrome_device() -> Iterator[ProvisionedDevice]:
    """A headless Chrome with a fresh profile on a free CDP port, as a provisioner would hand it to the driver."""
    port = _free_port()
    profile = Path(tempfile.mkdtemp(prefix="bu-conformance-chrome-"))
    process = subprocess.Popen(  # noqa: S603
        [
            _chrome_binaries()[0],
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    endpoint = f"http://127.0.0.1:{port}"
    try:
        _wait_for_cdp(endpoint, process)
        yield ProvisionedDevice(kind="chrome", endpoint=endpoint, extras={"user_data_dir": str(profile)}, cleanup=None)
    finally:
        _kill_tree(process)
        shutil.rmtree(profile, ignore_errors=True)


@pytest.mark.live
@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
class TestBrowserUseConformance(DriverConformance, BrowserSceneExtras):
    scene = browser_scene()
    driver_actions = BrowserUseDriver.handled_actions()

    @pytest.fixture
    def conformance_driver_factory(self, chrome_device: ProvisionedDevice):
        settings = BrowserSettings.from_process_env()
        return lambda sink: Tracing(create_browser_use_driver(chrome_device, settings), sink)
