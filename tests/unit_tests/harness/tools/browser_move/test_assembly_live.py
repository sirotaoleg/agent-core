# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``assemble_driver`` live: managed Chrome from the provisioner, each driver over it, and ``close()``."""

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

from openjiuwen.harness.tools.browser_move.contract import ConnectionLost, Observe
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import _candidate_chrome_binaries
from openjiuwen.harness.tools.browser_move.runtime.assembly import assemble_driver
from openjiuwen.harness.tools.browser_move.runtime.config import CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState

_SIDECAR_PYTHON_ENV = "BROWSER_USE_SIDECAR_PYTHON"


LIVE_MCP_ENV = "BROWSER_MOVE_LIVE_MCP"


def _mcp_skip_reason() -> str | None:
    if os.getenv(LIVE_MCP_ENV) != "1":
        return f"set {LIVE_MCP_ENV}=1 to run against a real @playwright/mcp server and Chrome"
    settings = BrowserSettings.from_process_env()
    if shutil.which(settings.mcp_command) is None:
        return f"{settings.mcp_command!r} is not on PATH (it starts @playwright/mcp)"
    if not _candidate_chrome_binaries(settings.browser_binary_candidates):
        return "no Chrome binary found"
    return None


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
        with urlopen(f"{endpoint}/json/version", timeout=1.0) as response:  # nosec
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
        completed = subprocess.run(  # noqa: S603, S607
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        raw = json.loads(completed.stdout or "[]")
        lines = [raw] if isinstance(raw, str) else [line for line in raw or [] if line]
    else:
        completed = subprocess.run(  # noqa: S603, S607
            ["pgrep", "-af", str(user_data_dir)], capture_output=True, text=True, timeout=30, check=False
        )
        lines = completed.stdout.splitlines()
    return [line for line in lines if needle in line.lower().replace("\\", "/")]


def _process_table() -> set[tuple[int, int, str]]:
    """``(pid, parent pid, command line)`` of every running process."""
    if os.name == "nt":
        script = (
            "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, CommandLine | "
            "ConvertTo-Json -Compress"
        )
        completed = subprocess.run(  # noqa: S603, S607
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        rows = json.loads(completed.stdout or "[]")
        return {(row["ProcessId"], row["ParentProcessId"], row["CommandLine"] or "") for row in rows}
    completed = subprocess.run(  # noqa: S603, S607
        ["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, timeout=30, check=False
    )
    table = set()
    for line in completed.stdout.splitlines():
        pid, ppid, args = (line.split(None, 2) + [""])[:3]
        table.add((int(pid), int(ppid), args))
    return table


def _descendants(table: set[tuple[int, int, str]], root: int) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    parents = {root}
    while parents:
        children = {(pid, command) for pid, ppid, command in table if ppid in parents and pid not in parents}
        children -= found
        found |= children
        parents = {pid for pid, _ in children}
    return found


def _alive(processes: set[tuple[int, str]]) -> set[tuple[int, str]]:
    return processes & {(pid, command) for pid, _, command in _process_table()}


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


@pytest.mark.live
@pytest.mark.skipif(_mcp_skip_reason() is not None, reason=_mcp_skip_reason() or "")
def test_managed_chrome_and_playwright_mcp_start_through_assemble_driver_and_close_stops_both(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "chrome-profile"
    environ = {
        **os.environ,
        "BROWSER_DRIVER": "playwright_mcp",
        "BROWSER_PROVISIONING": "managed",
        "BROWSER_MANAGED_PORT": str(_free_port()),
        "BROWSER_MANAGED_USER_DATA_DIR": str(user_data_dir),
        "BROWSER_MANAGED_KILL_EXISTING": "false",
        "BROWSER_MANAGED_ARGS": "--headless=new --window-size=1280,900",
        "BROWSER_PROFILE_NAME": "assembly-live",
        "BROWSER_PROFILE_STORE_PATH": str(tmp_path / "profiles.json"),
        "PLAYWRIGHT_MCP_ARGS": "-y @playwright/mcp@0.0.78",
        "PLAYWRIGHT_RUNTIME_MCP_CWD": str(tmp_path / "mcp"),
    }
    (tmp_path / "mcp").mkdir()

    async def run():
        assembly = await assemble_driver(CoreSettings.from_env(environ), environ)
        try:
            started = _descendants(_process_table(), os.getpid())
            assert _cdp_ready(assembly.device.endpoint)
            assert assembly.stack.info.name == "playwright_mcp"
            assert assembly.stack.info.versions["@playwright/mcp"] == "0.0.78"
            return assembly.device.endpoint, started, await assembly.stack.observe(Observe())
        finally:
            await assembly.close()

    try:
        endpoint, started, observation = asyncio.run(run())

        assert any("@playwright" in command for _, command in started), started
        assert isinstance(observation.surface, BrowserContext)
        assert observation.surface.url == "about:blank"
        assert _eventually(lambda: not _cdp_ready(endpoint)), f"{endpoint} still answers after close()"
        assert _eventually(lambda: not _alive(started)), _alive(started)
        assert _eventually(lambda: not _chrome_processes(user_data_dir)), _chrome_processes(user_data_dir)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)


@pytest.mark.live
@pytest.mark.skipif(_mcp_skip_reason() is not None, reason=_mcp_skip_reason() or "")
def test_a_crashed_managed_chrome_under_playwright_mcp_is_provisioned_again_with_a_new_driver(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "chrome-profile"
    environ = {
        **os.environ,
        "BROWSER_DRIVER": "playwright_mcp",
        "BROWSER_PROVISIONING": "managed",
        "BROWSER_MANAGED_PORT": str(_free_port()),
        "BROWSER_MANAGED_USER_DATA_DIR": str(user_data_dir),
        "BROWSER_MANAGED_KILL_EXISTING": "false",
        "BROWSER_MANAGED_ARGS": "--headless=new --window-size=1280,900",
        "BROWSER_PROFILE_NAME": "assembly-restart-live",
        "BROWSER_PROFILE_STORE_PATH": str(tmp_path / "profiles.json"),
        "PLAYWRIGHT_MCP_ARGS": "-y @playwright/mcp@0.0.78",
        "PLAYWRIGHT_RUNTIME_MCP_CWD": str(tmp_path / "mcp"),
    }
    (tmp_path / "mcp").mkdir()

    async def run():
        assembly = await assemble_driver(CoreSettings.from_env(environ), environ)
        session = DriverSession(assembly, BrowserPageState(), assembly.surface.profile.messages)
        try:
            await session.observe(Observe())
            assembly.device.cleanup()
            assert _eventually(lambda: not assembly.provisioner.is_alive(assembly.device))
            with pytest.raises(ConnectionLost, match="restarted"):
                await session.observe(Observe())
            assert session.assembly.stack is not assembly.stack
            assert _cdp_ready(session.assembly.device.endpoint)
            return session.assembly.device.endpoint, await session.observe(Observe())
        finally:
            await session.close()

    try:
        endpoint, observation = asyncio.run(run())

        assert observation.surface.url == "about:blank"
        assert _eventually(lambda: not _cdp_ready(endpoint)), f"{endpoint} still answers after close()"
        assert _eventually(lambda: not _chrome_processes(user_data_dir)), _chrome_processes(user_data_dir)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)


@pytest.mark.live
@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
def test_a_crashed_managed_chrome_under_browser_use_is_provisioned_again_with_a_new_driver(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "chrome-profile"
    environ = {
        **os.environ,
        "BROWSER_DRIVER": "browser_use",
        "BROWSER_PROVISIONING": "managed",
        "BROWSER_MANAGED_PORT": str(_free_port()),
        "BROWSER_MANAGED_USER_DATA_DIR": str(user_data_dir),
        "BROWSER_MANAGED_KILL_EXISTING": "false",
        "BROWSER_MANAGED_ARGS": "--headless=new --window-size=1280,900",
        "BROWSER_PROFILE_NAME": "assembly-restart-live",
        "BROWSER_PROFILE_STORE_PATH": str(tmp_path / "profiles.json"),
    }

    async def run():
        assembly = await assemble_driver(CoreSettings.from_env(environ), environ)
        session = DriverSession(assembly, BrowserPageState(), assembly.surface.profile.messages)
        try:
            await session.observe(Observe())
            assembly.device.cleanup()
            assert _eventually(lambda: not assembly.provisioner.is_alive(assembly.device))
            with pytest.raises(ConnectionLost, match="restarted"):
                await session.observe(Observe())
            assert session.assembly.stack is not assembly.stack
            assert _cdp_ready(session.assembly.device.endpoint)
            return session.assembly.device.endpoint, await session.observe(Observe())
        finally:
            await session.close()

    try:
        endpoint, observation = asyncio.run(run())

        assert observation.surface.url == "about:blank"
        assert _eventually(lambda: not _cdp_ready(endpoint)), f"{endpoint} still answers after close()"
        assert _eventually(lambda: not _chrome_processes(user_data_dir)), _chrome_processes(user_data_dir)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)
