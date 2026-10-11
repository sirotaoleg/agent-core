#!/usr/bin/env python
# coding: utf-8
"""Tests for process-level browser service lifecycle ownership."""
# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserInstanceConfig,
    BrowserRunGuardrails,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    _ACTIVE_BROWSER_RUNTIMES,
    BrowserAgentRuntime,
    reset_active_browser_runtimes,
    reset_managed_browser_runtime,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService
from openjiuwen.harness.tools.browser_move.playwright_runtime.service_registry import (
    BROWSER_SERVICE_REGISTRY,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import create_browser_surface
from tests.unit_tests.harness.tools.browser_move.runtime_fixtures import DEVICE, mcp_fake_driver


def _mcp_cfg(key: str) -> McpServerConfig:
    return McpServerConfig(
        server_id=f"test-playwright-{key}",
        server_name=f"test-playwright-{key}",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )


def _make_service(*, key: str = "shared") -> BrowserService:
    return BrowserService(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=_mcp_cfg(key),
        guardrails=BrowserRunGuardrails(
            max_steps=3,
            max_failures=1,
            timeout_s=30,
            retry_once=False,
        ),
        instance=BrowserInstanceConfig(key=key, driver_mode="managed"),
    )


def _provisioner() -> MagicMock:
    provisioner = MagicMock()
    provisioner.owns_process = True
    return provisioner


def _assembly(provisioner: Any) -> DriverAssembly:
    bundle = create_browser_surface({})
    return DriverAssembly(mcp_fake_driver(), DEVICE, provisioner, surface=bundle)


async def _open(service: BrowserService, provisioner: Any) -> FakeDriver:
    """Open the service's session on a fresh fake driver over ``provisioner``."""
    assembly = _assembly(provisioner)
    with patch.object(assembly_module, "assemble_driver", AsyncMock(return_value=assembly)):
        await service.ensure_runtime_ready()
    return assembly.stack


@pytest.fixture(autouse=True)
def _clear_process_registry():
    BROWSER_SERVICE_REGISTRY.clear()
    _ACTIVE_BROWSER_RUNTIMES.clear()
    yield
    BROWSER_SERVICE_REGISTRY.clear()
    _ACTIVE_BROWSER_RUNTIMES.clear()


@pytest.mark.asyncio
async def test_release_last_task_binding_preserves_managed_chrome() -> None:
    service = _make_service()
    provisioner = _provisioner()
    driver = await _open(service, provisioner)
    service._browser_agent = MagicMock()

    assert await service.release_task_binding() is True

    assert driver.kinds() == ["stop"]
    provisioner.release.assert_not_called()
    provisioner.stop.assert_not_called()
    assert service.session is not None
    assert service._browser_agent is None
    assert service.started is False


@pytest.mark.asyncio
async def test_explicit_reset_stops_browser_preserved_after_task_release() -> None:
    runtime = BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=_mcp_cfg("shared"),
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
        instance=BrowserInstanceConfig(key="shared", driver_mode="managed"),
    )
    provisioner = _provisioner()
    driver = await _open(runtime.service, provisioner)
    await runtime.ensure_runtime_ready()

    await runtime.release_task_resources()
    reset_count = await reset_active_browser_runtimes()

    assert reset_count == 1
    assert driver.started is False
    provisioner.release.assert_called_once_with(DEVICE)
    provisioner.stop.assert_called_once()
    assert (runtime.service.session, runtime.session) == (None, None)


@pytest.mark.asyncio
async def test_identity_reset_stops_only_matching_idle_managed_browser(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BROWSER_PROFILE_NAME", "jiuwenclaw")
    monkeypatch.delenv("BROWSER_MANAGED_ARGS", raising=False)
    headed = _make_service(key="")
    headed_driver = _provisioner()
    await _open(headed, headed_driver)
    await headed.release_task_binding()

    monkeypatch.setenv("BROWSER_MANAGED_ARGS", "--headless=new")
    headless = _make_service(key="")
    headless_driver = _provisioner()
    await _open(headless, headless_driver)
    await headless.release_task_binding()

    reset_count = await reset_managed_browser_runtime(
        browser_key="",
        profile_name="jiuwenclaw",
        display_mode="headed",
        browser_binary="",
    )

    assert reset_count == 1
    headed_driver.stop.assert_called_once()
    headless_driver.stop.assert_not_called()


@pytest.mark.asyncio
async def test_reused_service_waits_for_last_concurrent_task_reference() -> None:
    service = _make_service()
    service.acquire_task_binding()
    service.acquire_task_binding()
    driver = await _open(service, _provisioner())
    service._browser_agent = MagicMock()

    await service.release_task_binding()
    assert driver.kinds() == []
    assert service.started is True
    assert service._browser_agent is not None

    await service.release_task_binding()
    assert driver.kinds() == ["stop"]
    assert service.started is False
    assert service._browser_agent is None


@pytest.mark.asyncio
async def test_releasing_one_service_leaves_the_other_services_stack_running() -> None:
    first = _make_service()
    second = _make_service()
    first_driver = await _open(first, _provisioner())
    second_driver = await _open(second, _provisioner())

    await first.release_task_binding()

    assert (first_driver.kinds(), first.started) == (["stop"], False)
    assert (second_driver.kinds(), second.started) == ([], True)

    await second.release_task_binding()
    assert second_driver.kinds() == ["stop"]


@pytest.mark.asyncio
async def test_reset_stops_only_matching_browser_identity() -> None:
    first = _make_service(key="first")
    second = _make_service(key="second")
    first_provisioner, second_provisioner = _provisioner(), _provisioner()
    await _open(first, first_provisioner)
    await _open(second, second_provisioner)

    await first.reset()

    first_provisioner.stop.assert_called_once()
    second_provisioner.stop.assert_not_called()
    assert first.session is None
    assert second.session is not None


def test_display_mode_is_part_of_lifecycle_identity(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_MANAGED_ARGS", "--headless=new")
    headless = _make_service()
    monkeypatch.setenv("BROWSER_MANAGED_ARGS", "")
    headed = _make_service()

    assert headless.lifecycle_identity != headed.lifecycle_identity
    assert headless.lifecycle_identity.display_mode == "headless"
    assert headed.lifecycle_identity.display_mode == "headed"
