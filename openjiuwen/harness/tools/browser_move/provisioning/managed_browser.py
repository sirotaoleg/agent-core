# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Managed isolated browser launcher for CDP attach."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import warnings
from pathlib import Path
from types import MappingProxyType
from typing import Optional
from urllib.request import urlopen

from ..playwright_runtime.profiles import BrowserProfile, BrowserProfileStore
from ..surfaces.browser.config import BrowserSettings
from . import ProvisionedDevice


def _default_chrome_user_data_dir() -> str:
    """Return the platform-standard Chrome user data directory path.

    Does not guarantee the directory exists 鈥?only that this is where Chrome
    stores its default profile on the current OS.
    """
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(local_app_data) / "Google" / "Chrome" / "User Data")
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Application Support" / "Google" / "Chrome")
    return str(Path.home() / ".config" / "google-chrome")


def _kill_chrome_by_user_data_dir(user_data_dir: str) -> int:
    """Kill Chrome processes whose command line references user_data_dir.

    Returns the number of PIDs sent a kill signal. Failures are silently
    swallowed so a misconfigured environment never blocks startup.
    """
    normalized = str(Path(user_data_dir).expanduser().resolve()).lower().replace("\\", "/")
    killed = 0

    if os.name == "nt":
        ps_script = (
            "Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" "
            "| Select-Object -Property CommandLine,ProcessId "
            "| ConvertTo-Json -Depth 1"
        )
        try:
            powershell_bin = shutil.which("powershell")
            if powershell_bin:
                result = subprocess.run(
                    [powershell_bin, "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode == 0 and result.stdout.strip():
                    items = json.loads(result.stdout.strip())
                    if isinstance(items, dict):
                        items = [items]
                    taskkill_bin = shutil.which("taskkill")
                    for item in items or []:
                        cmdline = str(item.get("CommandLine") or "").lower().replace("\\", "/")
                        pid = item.get("ProcessId")
                        if not pid or normalized not in cmdline:
                            continue
                        if taskkill_bin:
                            subprocess.run(
                                [taskkill_bin, "/F", "/PID", str(pid)],
                                capture_output=True,
                                timeout=10,
                            )
                        killed += 1
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            pgrep_bin = shutil.which("pgrep")
            if pgrep_bin:
                result = subprocess.run(
                    [pgrep_bin, "-f", f"--user-data-dir={user_data_dir}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                kill_bin = shutil.which("kill")
                if result.returncode == 0 and kill_bin:
                    for line in result.stdout.strip().splitlines():
                        pid_str = line.strip()
                        if pid_str.isdigit():
                            subprocess.run([kill_bin, "-9", pid_str], capture_output=True, timeout=5)
                            killed += 1
        except Exception:  # noqa: BLE001
            pass

    return killed


def _cleanup_chrome_singleton_files(user_data_dir: str) -> None:
    """Remove stale Chrome singleton lock files left after a forced kill."""
    base = Path(user_data_dir).expanduser()
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        target = base / name
        try:
            if target.is_symlink() or target.exists():
                target.unlink(missing_ok=True)
        except OSError:
            pass


def _candidate_chrome_binaries(env_install_paths: tuple[str, ...]) -> list[str]:
    names = [
        "chrome",
        "google-chrome",
        "google-chrome-stable",
    ]
    resolved = [shutil.which(name) for name in names]
    binaries = [item for item in resolved if item]

    if os.name == "nt":
        install_paths = [
            *env_install_paths,
            str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ]
    elif sys.platform == "darwin":
        install_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        install_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
        ]

    for path in install_paths:
        if Path(path).exists():
            binaries.append(path)

    # Preserve order and remove duplicates.
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in binaries:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _allocate_free_port() -> int:
    # bind-to-0 so concurrent instances never pick the same port; Chrome starts on it right away and
    # the endpoint-ready check absorbs the small window between closing the socket and the launch.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _is_chrome_identifier(value: str) -> bool:
    lowered = (value or "").strip().replace("\\", "/").lower()
    if not lowered:
        return False
    return "chrome" in Path(lowered).name


class ManagedBrowserProvisioner:
    """Provisions Chrome for the managed, remote and extension modes; a managed instance owns the Chrome it starts."""

    def __init__(
        self,
        profile: Optional[BrowserProfile] = None,
        profile_store: Optional[BrowserProfileStore] = None,
    ) -> None:
        self.profile = profile
        self._profile_store = profile_store
        self._binary_candidates: tuple[str, ...] = ()
        self._process: Optional[subprocess.Popen] = None
        self._owns_process: bool = False

    def provision(self, surface_settings: BrowserSettings) -> ProvisionedDevice:
        settings = surface_settings
        if settings.provisioning == "remote":
            return ProvisionedDevice(
                kind="chrome",
                endpoint=settings.cdp_url,
                extras=MappingProxyType(
                    {
                        "cdp_headers": settings.cdp_headers,
                        "cdp_timeout": settings.cdp_timeout,
                        "launch_args": settings.launch_args,
                    }
                ),
            )
        if settings.provisioning == "extension":
            return ProvisionedDevice(
                kind="chrome",
                endpoint="",
                extras=MappingProxyType(
                    {
                        "extension": True,
                        "extension_token": settings.mcp_extension_token,
                        "launch_args": settings.launch_args,
                    }
                ),
            )
        store = self._profile_store or BrowserProfileStore(
            settings.profile_store_path or settings.mcp_cwd / ".browser" / "profiles.json"
        )
        profile = store.get_profile(settings.profile_name)
        if (
            profile is None
            or profile.driver_type != "managed"
            or profile.debug_port <= 0
            or not str(profile.user_data_dir).strip()
            or profile.extra_args != list(settings.managed_args)
        ):
            profile = _build_profile(settings)
        profile.browser_binary = settings.browser_binary
        store.upsert_profile(profile, select=True)
        self.profile = profile
        self._binary_candidates = settings.browser_binary_candidates

        endpoint = self.start(30.0, settings.managed_kill_existing)
        profile.cdp_url = endpoint
        store.upsert_profile(profile, select=True)
        return ProvisionedDevice(
            kind="chrome",
            endpoint=endpoint,
            extras=MappingProxyType(
                {
                    "profile": profile,
                    "port": profile.debug_port,
                    "user_data_dir": profile.user_data_dir,
                    "managed_args": tuple(profile.extra_args),
                    "launch_args": settings.launch_args,
                }
            ),
            cleanup=self.stop,
        )

    def release(self, device: ProvisionedDevice) -> None:
        if device.cleanup is not None:
            device.cleanup()

    @property
    def cdp_endpoint(self) -> str:
        host = self.profile.host or "127.0.0.1"
        return f"http://{host}:{int(self.profile.debug_port)}"

    @property
    def owns_process(self) -> bool:
        return self._owns_process

    def is_endpoint_ready(self) -> bool:
        return self._is_endpoint_ready()

    def _resolve_binary(self) -> str:
        explicit = (self.profile.browser_binary or "").strip()
        if explicit:
            if _is_chrome_identifier(explicit):
                candidate = Path(explicit).expanduser()
                if candidate.is_file():
                    return str(candidate)
                resolved = shutil.which(explicit)
                if resolved:
                    return resolved
                raise RuntimeError(f"Configured Chrome binary not found: {explicit}")
            raise RuntimeError(
                "Managed mode supports Chrome only. "
                "Set BROWSER_MANAGED_BINARY to a Chrome executable."
            )

        candidates = _candidate_chrome_binaries(self._binary_candidates)
        if not candidates:
            raise RuntimeError(
                "Chrome needs to be installed. No Chrome binary was found on this machine."
            )
        return candidates[0]

    def _build_args(self, binary: str) -> list[str]:
        user_data_dir = Path(self.profile.user_data_dir).expanduser()
        user_data_dir.mkdir(parents=True, exist_ok=True)

        host = (self.profile.host or "127.0.0.1").strip() or "127.0.0.1"
        port = int(self.profile.debug_port)
        if port <= 0:
            raise RuntimeError(f"Invalid debug port for managed browser profile: {port}")

        args = [
            binary,
            f"--remote-debugging-address={host}",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "about:blank",
        ]
        args.extend(self.profile.extra_args)
        return args

    def _is_endpoint_ready(self) -> bool:
        endpoint = f"{self.cdp_endpoint}/json/version"
        try:
            with urlopen(endpoint, timeout=1.5) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
                if isinstance(payload, dict):
                    return bool(payload.get("webSocketDebuggerUrl") or payload.get("Browser"))
        except (OSError, ValueError):
            return False
        return False

    def start(self, timeout_s: float = 30.0, kill_existing: bool = False) -> str:
        if self._process is not None and self._process.poll() is None:
            if self._is_endpoint_ready():
                return self.cdp_endpoint

        if self._is_endpoint_ready():
            self._process = None
            self._owns_process = False
            return self.cdp_endpoint

        if kill_existing:
            user_data_dir = str(Path(self.profile.user_data_dir).expanduser())
            _kill_chrome_by_user_data_dir(user_data_dir)
            time.sleep(1.5)
            _cleanup_chrome_singleton_files(user_data_dir)

        binary = self._resolve_binary()
        args = self._build_args(binary)
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        )
        self._owns_process = True

        deadline = time.time() + max(1.0, float(timeout_s))
        while time.time() < deadline:
            if self._process.poll() is not None:
                self._owns_process = False
                raise RuntimeError(
                    f"Managed browser process exited early with code {self._process.returncode}"
                )
            if self._is_endpoint_ready():
                return self.cdp_endpoint
            time.sleep(0.25)

        self._owns_process = False
        raise RuntimeError(f"Managed browser CDP endpoint not ready after {timeout_s:.1f}s: {self.cdp_endpoint}")

    def stop(self, wait_timeout_s: float = 5.0) -> None:
        process = self._process
        self._process = None
        owns_process = self._owns_process
        self._owns_process = False
        if process is None:
            return
        if not owns_process:
            return
        if process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=max(0.5, float(wait_timeout_s)))
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def clear(self):
        # Reap a Chrome child that exited (e.g. user closed the window) but
        # whose Popen handle still sits on the driver.
        if self._process is None:
            return
        process = self._process
        self._process = None
        self._owns_process = False
        if process is None:
            return
        from openjiuwen.core.common.logging import logger as _logger
        _logger.info("ManagedBrowser: clear handle process")
        process.poll()


def _build_profile(settings: BrowserSettings) -> BrowserProfile:
    # managed_port 0: pick a free port (an isolated instance must not share one fixed port).
    port = settings.managed_port or _allocate_free_port()
    if settings.user_data_dir:
        user_data_dir = settings.user_data_dir
    elif settings.managed_kill_existing:
        user_data_dir = _default_chrome_user_data_dir()
    else:
        user_data_dir = str(settings.mcp_cwd / ".browser-profiles" / settings.profile_name)
    host = settings.managed_host
    return BrowserProfile(
        name=settings.profile_name,
        driver_type="managed",
        cdp_url=f"http://{host}:{port}",
        browser_binary=settings.browser_binary,
        user_data_dir=user_data_dir,
        debug_port=port,
        host=host,
        extra_args=list(settings.managed_args),
    )


def __getattr__(name: str):
    # ManagedBrowserDriver is the old name; kept as a deprecated alias.
    if name == "ManagedBrowserDriver":
        warnings.warn(
            "ManagedBrowserDriver is deprecated; use ManagedBrowserProvisioner",
            DeprecationWarning,
            stacklevel=2,
        )
        return ManagedBrowserProvisioner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
