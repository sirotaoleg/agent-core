# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``assemble_driver`` live: managed Chrome from the provisioner, the browser-use driver over it, and ``close()``."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from openjiuwen.harness.tools.browser_move.contract import Observe
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import assemble_driver
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import _candidate_chrome_binaries
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext

_SIDECAR_PYTHON_ENV = "BROWSER_USE_SIDECAR_PYTHON"


def _skip_reason() -> str | None:
    sidecar_python = os.getenv(_SIDECAR_PYTHON_ENV, "")
    if not sidecar_python or not Path(sidecar_python).is_file():
        return f"{_SIDECAR_PYTHON_ENV} does not name the browser-use sidecar interpreter"
    if not _candidate_chrome_binaries(BrowserSettings.from_process_env().browser_binary_candidates):
        return "no Chrome binary found"
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _cdp_ready(endpoint: str) -> bool:
    try:
        with urlopen(f"{endpoint}/json/version", timeout=1.0) as response:  # nosec B310 - loopback only
            json.load(response)
            return True
    except (URLError, OSError, ValueError):
        return False


def _chrome_processes(user_data_dir: Path) -> list[str]:
    """Command lines of running Chrome processes that use ``user_data_dir`` (renderers and crashpad included)."""
    needle = str(user_data_dir).lower().replace("\\", "/")
    if os.name == "nt":
        script = (
            "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
            "Select-Object -ExpandProperty CommandLine | ConvertTo-Json"
        )
        completed = subprocess.run(  # noqa: S603, S607 - fixed read-only query
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        raw = json.loads(completed.stdout or "[]")
        lines = [raw] if isinstance(raw, str) else [line for line in raw or [] if line]
    else:
        completed = subprocess.run(  # noqa: S603, S607 - fixed read-only query
            ["pgrep", "-af", str(user_data_dir)], capture_output=True, text=True, timeout=30, check=False
        )
        lines = completed.stdout.splitlines()
    return [line for line in lines if needle in line.lower().replace("\\", "/")]


def _eventually(condition: Callable[[], bool], timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.25)
    return condition()


@pytest.mark.live
@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
def test_managed_chrome_and_browser_use_start_through_assemble_driver_and_close_stops_both(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "chrome-profile"
    environ = {
        **os.environ,
        "BROWSER_DRIVER": "browser_use",
        "BROWSER_PROVISIONING": "managed",
        "BROWSER_MANAGED_PORT": str(_free_port()),
        "BROWSER_MANAGED_USER_DATA_DIR": str(user_data_dir),
        "BROWSER_MANAGED_KILL_EXISTING": "false",
        "BROWSER_MANAGED_ARGS": "--headless=new --window-size=1280,900",
        "BROWSER_PROFILE_NAME": "assembly-live",
        "BROWSER_PROFILE_STORE_PATH": str(tmp_path / "profiles.json"),
    }

    async def run():
        assembly = await assemble_driver(CoreSettings.from_env(environ), environ)
        try:
            assert _cdp_ready(assembly.device.endpoint)
            assert assembly.stack.info.name == "browser_use"
            assert assembly.stack.info.versions["browser"]
            return assembly.device.endpoint, await assembly.stack.observe(Observe())
        finally:
            await assembly.close()

    try:
        endpoint, observation = asyncio.run(run())

        assert isinstance(observation.surface, BrowserContext)
        assert observation.surface.url == "about:blank"
        assert _eventually(lambda: not _cdp_ready(endpoint)), f"{endpoint} still answers after close()"
        assert _eventually(lambda: not _chrome_processes(user_data_dir)), _chrome_processes(user_data_dir)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)
