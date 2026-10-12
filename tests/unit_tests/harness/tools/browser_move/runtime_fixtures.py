# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A ``BrowserAgentRuntime`` over ``FakeDriver`` with the Playwright MCP tool dialect, for the rail and facade tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import AsyncMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    ContextRef,
    DriverInfo,
    DriverRef,
    Effect,
    Element,
    NativeTool,
    Observation,
    Screenshot,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.runtime import assembly as assembly_module
from openjiuwen.harness.tools.browser_move.runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.runtime.config import BrowserRunGuardrails, CoreSettings
from openjiuwen.harness.tools.browser_move.runtime.session import BrowserAgentRuntime, ToolKind
from openjiuwen.harness.tools.browser_move.runtime.utils.parsing import sanitize_json_schema
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FillForm,
    GetConsole,
    GetNetwork,
    GetPdf,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import create_browser_surface
from tests.unit_tests.harness.tools.browser_move.snapshots import builder

MCP_PREFIX = "mcp_playwright-official_"
DEVICE = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9222", extras={})
HOME = ContextRef("0", "tab", "Home")
HOME_URL = "https://example.com/"
_REFS = next(iter(DEFAULT_ACTIONS.values()))
_SURFACE_COMMANDS = (
    Navigate, Back, OpenContext, SelectOption, UploadFile, FillForm, Drop, GetPdf, GetConsole, GetNetwork
)


def mcp_driver_info() -> DriverInfo:
    """The Playwright MCP driver's view of itself over the 0.0.78 tool list, served by a fake."""
    native = {
        tool["name"]: NativeTool(
            tool["description"], sanitize_json_schema(tool["inputSchema"]), DECODERS.get(tool["name"])
        )
        for tool in builder.load_mcp_tools()
        if tool["name"] in DECODERS or tool["name"] == "browser_find"
    }
    actions = {**DEFAULT_ACTIONS, **{command: _REFS for command in _SURFACE_COMMANDS}}
    return DriverInfo(
        name="fake",
        surfaces=frozenset({"browser"}),
        actions=actions,
        native_tools=native,
        tool_name_prefix=MCP_PREFIX,
    )


def mcp_fake_driver(observations: Iterable[Observation] = (), results: Iterable[ActionResult] = ()) -> FakeDriver:
    return FakeDriver(observations, results, info=mcp_driver_info())


def surface(url: str = HOME_URL, title: str = "Home") -> BrowserContext:
    return BrowserContext(url=url, title=title, tabs=(HOME,), tab_urls=(url,), current_tab=0)


def element(ref: str, role: str = "button", name: str = "Save") -> Element:
    return Element(ref=DriverRef(ref, ""), role=role, name=name)


def observation(
    elements: Iterable[Element] = (),
    *,
    text: str | None = None,
    url: str = HOME_URL,
    title: str = "Home",
    screenshot: Screenshot | None = None,
) -> Observation:
    elements = tuple(elements)
    native_text = text if text is not None else "\n".join(f'- {e.role} "{e.name}" [ref={e.ref.ref}]' for e in elements)
    return Observation(
        context=HOME, elements=elements, native_text=native_text, surface=surface(url, title), screenshot=screenshot
    )


def result(
    *, ok: bool = True, url: str = HOME_URL, title: str = "Home", context_changed: bool = False, value: Any = None
) -> ActionResult:
    return ActionResult(ok=ok, value=value, context_changed=context_changed, surface=surface(url, title))


def run(coro: Any) -> Any:
    """Run ``coro`` to completion, on a worker thread when the caller already runs a loop (async tests)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class FakeProvisioner:
    """Hands out ``DEVICE`` and records releases; the device is always alive."""

    def __init__(self) -> None:
        self.released: list[ProvisionedDevice] = []

    def provision(self, surface_settings: Any) -> ProvisionedDevice:
        return DEVICE

    def release(self, device: ProvisionedDevice) -> None:
        self.released.append(device)

    def is_alive(self, device: ProvisionedDevice) -> bool:
        return True


def fake_assembly(driver: FakeDriver) -> DriverAssembly:
    bundle = create_browser_surface({})
    return DriverAssembly(driver, DEVICE, FakeProvisioner(), surface=bundle)


def make_runtime(
    driver: FakeDriver | None = None,
    *,
    allowed_tool_names: Iterable[str] | None = None,
    image_support: bool | None = False,
    language: str = "en",
    workspace: Any = None,
    core_settings: CoreSettings | None = None,
) -> BrowserAgentRuntime:
    """A started runtime whose driver session is ``driver`` (a FakeDriver with the MCP dialect by default)."""
    runtime = BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=McpServerConfig(
            server_id="playwright_official_stdio",
            server_name="playwright-official",
            server_path="stdio://playwright",
            client_type="stdio",
            params={"cwd": str(Path.cwd())},
        ),
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
        allowed_tool_names=allowed_tool_names,
        language=language,
        workspace=workspace,
        core_settings=core_settings,
    )
    driver = driver if driver is not None else mcp_fake_driver()
    with patch.object(assembly_module, "assemble_driver", AsyncMock(return_value=fake_assembly(driver))):
        run(runtime.ensure_runtime_ready())
    if image_support is not None:
        runtime.agent_parts(image_support=image_support, language=language)
    return runtime


def command_kind(action: Action) -> ToolKind:
    return ToolKind(action=action, effect=action.effect, progress=True, known=True)


PROBE = ToolKind(effect=Effect.READ, progress=True, known=True, helper=True)
BATCH = ToolKind(effect=Effect.ACT, progress=True, known=True, helper=True)
RECALL = ToolKind(effect=Effect.READ, exempt_from_budget=True, progress=True, known=True, helper=True)
UNKNOWN = ToolKind()
