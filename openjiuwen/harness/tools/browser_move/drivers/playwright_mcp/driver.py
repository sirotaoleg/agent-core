# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``PlaywrightMcpDriver``: the driver contract on top of a ``@playwright/mcp`` server the driver owns."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.base import mcp_model_tool_prefix
from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    ActionTimeout,
    Click,
    CloseContext,
    ConnectionLost,
    Drag,
    DriverError,
    DriverInfo,
    DriverRef,
    HandleDialog,
    HandlerDriver,
    Health,
    Hover,
    Locator,
    NativeTool,
    Observation,
    Observe,
    PointRef,
    PressKey,
    ProvisionedDevice,
    RunScript,
    Screenshot,
    Scroll,
    SwitchContext,
    TypeText,
    Unsupported,
    Wait,
    handles,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.clients import (
    BrowserMoveStdioClient,
    BrowserMoveStreamableHttpClient,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.config import playwright_mcp_config
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import decode, result_text
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import (
    parse_snapshot,
    parse_tabs,
    split_sections,
    without_boxes,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
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

_PACKAGE_VERSION = re.compile(r"^@playwright/mcp@(?P<version>.+)$")
_PING_TIMEOUT_S = 5.0

# The server tool each command maps to; its description becomes the command's tool hint.
_TOOL_OF: dict[type[Action], str] = {
    Click: "browser_click",
    TypeText: "browser_type",
    PressKey: "browser_press_key",
    Hover: "browser_hover",
    Drag: "browser_drag",
    Scroll: "browser_mouse_wheel",
    SelectOption: "browser_select_option",
    FillForm: "browser_fill_form",
    Drop: "browser_drop",
    UploadFile: "browser_file_upload",
    HandleDialog: "browser_handle_dialog",
    Wait: "browser_wait_for",
    RunScript: "browser_evaluate",
    Navigate: "browser_navigate",
    Back: "browser_navigate_back",
    OpenContext: "browser_tabs",
    SwitchContext: "browser_tabs",
    CloseContext: "browser_tabs",
    GetPdf: "browser_pdf_save",
    GetConsole: "browser_console_messages",
    GetNetwork: "browser_network_requests",
}
# Server tools no command backs; the surface offers its own tool under the server's description and schema.
_UNBACKED_TOOLS = frozenset({"browser_find"})


@dataclass(frozen=True)
class PlaywrightMcpConfig:
    """What ``start()`` needs: the server's command line, environment and working directory."""

    server: McpServerConfig

    @property
    def output_dir(self) -> Path:
        return Path(self.server.params["cwd"])

    @property
    def version(self) -> str:
        for arg in self.server.params.get("args") or ():
            match = _PACKAGE_VERSION.match(str(arg))
            if match is not None:
                return match.group("version")
        return ""


def _target(ref: DriverRef | Locator) -> str:
    if isinstance(ref, Locator):
        if ref.text is not None or not ref.css:
            raise Unsupported("PlaywrightMcpDriver addresses a Locator by css only", dispatched=False)
        return ref.css
    return ref.ref


def _aim(ref: DriverRef | Locator, what: str) -> dict[str, Any]:
    target = _target(ref)
    return {"element": f"{what} {target}", "target": target}


def _snapshot_args(command: Observe) -> dict[str, Any]:
    # Boxes give elements their bounds; a scoped snapshot goes as the model asked, so its text stays the server's.
    if not command.scoped:
        return {"boxes": True}
    args: dict[str, Any] = {}
    if command.depth is not None:
        args["depth"] = command.depth
    if isinstance(command.root, PointRef):
        raise Unsupported("browser_snapshot scopes to an element ref or a css target, not a point", dispatched=False)
    if command.root is not None:
        args["target"] = _target(command.root)
    return args


def _point(ref: PointRef) -> dict[str, int]:
    # Screenshots are taken at CSS scale, so a screenshot pixel is a viewport pixel (Screenshot.scale is 1).
    return {"x": ref.x, "y": ref.y}


def _staged(paths: Iterable[str], root: Path) -> tuple[list[str], list[Path]]:
    # The server reads files only under its working directory: a file elsewhere is copied in first. Anything else
    # (a missing file, a directory) goes as given and the server refuses it before touching the page.
    root = root.resolve()
    staged = []
    created = []
    for path in paths:
        source = (root / path).resolve()
        if source.is_relative_to(root) or not source.is_file():
            staged.append(path)
            continue
        target = root / "uploads" / uuid.uuid4().hex[:12] / source.name
        target.parent.mkdir(parents=True)
        created.append(target.parent)
        shutil.copy2(source, target)
        staged.append(str(target))
    return staged, created


def _unstaged(directories: list[Path]) -> None:
    for directory in directories:
        shutil.rmtree(directory, ignore_errors=True)
    # The working directory may be the user's: an uploads folder goes only when nothing else is left in it.
    with contextlib.suppress(OSError):
        directories[0].parent.rmdir()


class PlaywrightMcpDriver(HandlerDriver):
    """Runs contract commands on a Chrome through a ``@playwright/mcp`` server started by ``start()``."""

    def __init__(self, config: PlaywrightMcpConfig | None = None) -> None:
        self._config = config
        self._client: BrowserMoveStdioClient | BrowserMoveStreamableHttpClient | None = None
        self._context: BrowserContext | None = None
        self._tool_hints: dict[type[Action], str] = {}
        self._native_tools: dict[str, NativeTool] = {}
        self._staged_dirs: list[Path] = []

    @property
    def config(self) -> PlaywrightMcpConfig | None:
        return self._config

    def describe(self) -> DriverInfo:
        version = self._config.version if self._config is not None else ""
        return DriverInfo(
            name="playwright_mcp",
            surfaces=frozenset({"browser"}),
            versions=MappingProxyType({"@playwright/mcp": version} if version else {}),
            observes=frozenset({"elements", "screenshot", "native_text", "surface"}),
            tool_hints=MappingProxyType(dict(self._tool_hints)),
            native_tools=MappingProxyType(dict(self._native_tools)),
            tool_name_prefix=mcp_model_tool_prefix(self._config.server.server_name) if self._config is not None else "",
            display_name="Playwright MCP",
        )

    async def start(self, config: PlaywrightMcpConfig | None = None) -> None:
        config = config or self._config
        if config is None:
            raise DriverError("PlaywrightMcpDriver.start() needs a PlaywrightMcpConfig", dispatched=False)
        if self._client is not None:
            await self.stop()
        if config.server.client_type == "streamable-http":
            client: BrowserMoveStdioClient | BrowserMoveStreamableHttpClient = BrowserMoveStreamableHttpClient(
                config.server
            )
        else:
            client = BrowserMoveStdioClient(config.server)
        if not await client.connect():
            raise ConnectionLost(f"could not start {config.server.server_name}", dispatched=False)
        try:
            cards = await client.list_tools()
        except Exception as exc:
            await client.disconnect()
            raise ConnectionLost(f"{config.server.server_name}: {exc}", dispatched=False) from exc
        descriptions = {card.name: card.description for card in cards}
        self._tool_hints = {command: descriptions[tool] for command, tool in _TOOL_OF.items() if descriptions.get(tool)}
        self._native_tools = {
            card.name: NativeTool(card.description, card.input_params, DECODERS.get(card.name))
            for card in cards
            if card.name in DECODERS or card.name in _UNBACKED_TOOLS
        }
        self._config = config
        self._client = client
        self._context = None

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()
        staged, self._staged_dirs = self._staged_dirs, []
        if staged:
            await asyncio.to_thread(_unstaged, staged)

    async def health(self) -> Health:
        if self._client is None:
            return Health(ok=False, detail="not started")
        if await self._client.ping(timeout=_PING_TIMEOUT_S):
            return Health(ok=True)
        return Health(ok=False, detail="the server does not answer")

    async def observe(self, command: Observe) -> Observation:
        raw = await self._call("browser_snapshot", _snapshot_args(command))
        decoded = await self._decode("browser_snapshot", raw)
        if not decoded.ok:
            return await self._observe_modal_state(raw)
        elements, header = parse_snapshot(result_text(raw))
        if header is None:
            raise DriverError("browser_snapshot reported no page", dispatched=True)
        screenshot = None
        if command.include_screenshot:
            shot = await self._run("browser_take_screenshot", {"type": "png", "fullPage": command.full_page})
            screenshot = shot.value if isinstance(shot.value, Screenshot) else None
        return Observation(
            context=header.focused,
            elements=elements,
            screenshot=screenshot,
            native_text=without_boxes(result_text(raw)),
            surface=decoded.surface,
        )

    async def _observe_modal_state(self, refused: dict[str, Any]) -> Observation:
        # A dialog or file chooser refuses the snapshot and every other page tool; the tab list still answers.
        raw = await self._call("browser_tabs", {"action": "list"})
        listed = await self._decode("browser_tabs", raw)
        header = parse_tabs(split_sections(result_text(raw)).get("Result", ""))
        if header is None:
            raise DriverError("browser_tabs reported no page", dispatched=True)
        return Observation(context=header.focused, native_text=result_text(refused), surface=listed.surface)

    @handles(Click, refs={DriverRef, Locator, PointRef})
    async def _click(self, action: Click) -> ActionResult:
        if isinstance(action.target, PointRef):
            if action.modifiers:
                raise Unsupported("browser_mouse_click_xy takes no modifiers", dispatched=False)
            args: dict[str, Any] = _point(action.target)
            if action.button != "left":
                args["button"] = action.button
            if action.count != 1:
                args["clickCount"] = action.count
            return await self._run("browser_mouse_click_xy", args)
        if action.count > 2:
            raise Unsupported("browser_click clicks once or twice", dispatched=False)
        args = _aim(action.target, "click")
        if action.count == 2:
            args["doubleClick"] = True
        if action.button != "left":
            args["button"] = action.button
        if action.modifiers:
            args["modifiers"] = list(action.modifiers)
        return await self._run("browser_click", args)

    @handles(TypeText, refs={DriverRef, Locator})
    async def _type_text(self, action: TypeText) -> ActionResult:
        if action.target is None:
            raise Unsupported("browser_type types only into a target element", dispatched=False)
        if not action.clear and not action.slowly:
            raise Unsupported("browser_type replaces the content unless it types slowly", dispatched=False)
        args = {**_aim(action.target, "type into"), "text": action.text}
        if action.submit:
            args["submit"] = True
        if action.slowly:
            args["slowly"] = True
        return await self._run("browser_type", args, sensitive=action.sensitive)

    @handles(PressKey)
    async def _press_key(self, action: PressKey) -> ActionResult:
        return await self._run("browser_press_key", {"key": action.key})

    @handles(Hover, refs={DriverRef, Locator, PointRef})
    async def _hover(self, action: Hover) -> ActionResult:
        if isinstance(action.target, PointRef):
            return await self._run("browser_mouse_move_xy", _point(action.target))
        return await self._run("browser_hover", _aim(action.target, "hover"))

    @handles(Drag, refs={DriverRef, Locator, PointRef})
    async def _drag(self, action: Drag) -> ActionResult:
        points = isinstance(action.source, PointRef), isinstance(action.destination, PointRef)
        if all(points):
            start, end = _point(action.source), _point(action.destination)
            args = {"startX": start["x"], "startY": start["y"], "endX": end["x"], "endY": end["y"]}
            return await self._run("browser_mouse_drag_xy", args)
        if any(points):
            raise Unsupported("PlaywrightMcpDriver drags between two refs or two points", dispatched=False)
        start_target, end_target = _target(action.source), _target(action.destination)
        args = {
            "startElement": f"drag {start_target}",
            "startTarget": start_target,
            "endElement": f"drop on {end_target}",
            "endTarget": end_target,
        }
        return await self._run("browser_drag", args)

    @handles(Scroll)
    async def _scroll(self, action: Scroll) -> ActionResult:
        return await self._run("browser_mouse_wheel", {"deltaX": action.dx, "deltaY": action.dy})

    @handles(SelectOption, refs={DriverRef, Locator})
    async def _select_option(self, action: SelectOption) -> ActionResult:
        args = {**_aim(action.target, "select in"), "values": list(action.values)}
        return await self._run("browser_select_option", args)

    @handles(FillForm, refs={DriverRef, Locator})
    async def _fill_form(self, action: FillForm) -> ActionResult:
        fields = []
        for field in action.fields:
            target = _target(field.target)
            fields.append(
                {
                    "name": f"{field.kind.value} {target}",
                    "type": field.kind.value,
                    "target": target,
                    "value": field.value,
                }
            )
        sensitive = any(field.sensitive for field in action.fields)
        return await self._run("browser_fill_form", {"fields": fields}, sensitive=sensitive)

    @handles(Drop, refs={DriverRef, Locator})
    async def _drop(self, action: Drop) -> ActionResult:
        args = _aim(action.target, "drop on")
        if action.paths:
            args["paths"] = await self._stage(action.paths)
        if action.data:
            args["data"] = dict(action.data)
        return await self._run("browser_drop", args)

    @handles(UploadFile)
    async def _upload_file(self, action: UploadFile) -> ActionResult:
        args = {"paths": await self._stage(action.paths)} if action.paths else {}
        return await self._run("browser_file_upload", args)

    @handles(HandleDialog)
    async def _handle_dialog(self, action: HandleDialog) -> ActionResult:
        args: dict[str, Any] = {"accept": action.accept}
        if action.prompt_text is not None:
            args["promptText"] = action.prompt_text
        return await self._run("browser_handle_dialog", args)

    @handles(Wait)
    async def _wait(self, action: Wait) -> ActionResult:
        if action.until_text is not None:
            return await self._run("browser_wait_for", {"text": action.until_text})
        return await self._run("browser_wait_for", {"time": action.seconds})

    @handles(RunScript, refs={DriverRef, Locator})
    async def _run_script(self, action: RunScript) -> ActionResult:
        args: dict[str, Any] = {}
        if action.target is not None:
            args = _aim(action.target, "evaluate on")
        function = action.code
        if action.args is not None:
            params = f"element, {json.dumps(action.args)}" if action.target is not None else json.dumps(action.args)
            head = "(element)" if action.target is not None else "()"
            function = f"{head} => ({action.code})({params})"
        return await self._run("browser_evaluate", {**args, "function": function}, sensitive=action.sensitive)

    @handles(Navigate)
    async def _navigate(self, action: Navigate) -> ActionResult:
        return await self._run("browser_navigate", {"url": action.url})

    @handles(Back)
    async def _back(self, action: Back) -> ActionResult:
        del action
        return await self._run("browser_navigate_back", {})

    @handles(OpenContext)
    async def _open_context(self, action: OpenContext) -> ActionResult:
        args: dict[str, Any] = {"action": "new"}
        if action.url is not None:
            args["url"] = action.url
        result = await self._run("browser_tabs", args)
        # The server appends the new tab and focuses it.
        opened = result.surface.tabs[-1] if isinstance(result.surface, BrowserContext) else None
        return dataclasses.replace(result, value=opened)

    @handles(SwitchContext)
    async def _switch_context(self, action: SwitchContext) -> ActionResult:
        return await self._run("browser_tabs", {"action": "select", "index": int(action.context.id)})

    @handles(CloseContext)
    async def _close_context(self, action: CloseContext) -> ActionResult:
        result = await self._run("browser_tabs", {"action": "close", "index": int(action.context.id)})
        return dataclasses.replace(result, source_context=action.context)

    @handles(GetPdf)
    async def _get_pdf(self, action: GetPdf) -> ActionResult:
        args = {"filename": action.filename} if action.filename is not None else {}
        return await self._run("browser_pdf_save", args)

    @handles(GetConsole)
    async def _get_console(self, action: GetConsole) -> ActionResult:
        args: dict[str, Any] = {"level": action.level}
        if action.all:
            args["all"] = True
        return await self._run("browser_console_messages", args)

    @handles(GetNetwork)
    async def _get_network(self, action: GetNetwork) -> ActionResult:
        args: dict[str, Any] = {"static": action.static}
        if action.filter is not None:
            args["filter"] = action.filter
        return await self._run("browser_network_requests", args)

    async def _run(self, tool: str, arguments: dict[str, Any], *, sensitive: bool = False) -> ActionResult:
        return await self._decode(tool, await self._call(tool, arguments), sensitive=sensitive)

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise ConnectionLost("PlaywrightMcpDriver is not started", dispatched=False)
        if not self._client.connected:
            raise ConnectionLost(f"{tool}: not connected to the server", dispatched=False)
        try:
            result = await self._client.call_tool_once(tool, arguments)
        except TimeoutError as exc:
            raise ActionTimeout(f"{tool}: the server did not answer in time", dispatched=True) from exc
        except Exception as exc:
            raise ConnectionLost(f"{tool}: {exc}", dispatched=True) from exc
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def _decode(self, tool: str, raw: dict[str, Any], *, sensitive: bool = False) -> ActionResult:
        result = await asyncio.to_thread(
            decode, tool, raw, output_dir=self._output_dir(), before=self._context, sensitive=sensitive
        )
        if isinstance(result.surface, BrowserContext):
            self._context = result.surface
        return result

    async def _stage(self, paths: Iterable[str]) -> list[str]:
        staged, created = await asyncio.to_thread(_staged, paths, self._output_dir())
        self._staged_dirs.extend(created)
        return staged

    def _output_dir(self) -> Path:
        return self._config.output_dir if self._config is not None else Path.cwd()


def create_playwright_mcp_driver(device: ProvisionedDevice, surface_settings: Any) -> PlaywrightMcpDriver:
    """Driver factory (registry signature): the server attaches to ``device.endpoint`` over CDP on ``start()``.

    A server config in ``surface_settings.mcp_server`` keeps its command line and environment; the factory's name
    and id win, so the tool prefix never changes with the caller, and the device endpoint replaces the config's.
    """
    server = playwright_mcp_config(surface_settings, cdp_url=device.endpoint)
    given = getattr(surface_settings, "mcp_server", None)
    if given is not None:
        # A device emulation cannot drive a CDP-attached browser; CDP mode is Chromium-only.
        env = {key: value for key, value in (given.params.get("env") or {}).items() if key != "PLAYWRIGHT_MCP_DEVICE"}
        if device.endpoint:
            env = {"PLAYWRIGHT_MCP_BROWSER": "chrome", **env, "PLAYWRIGHT_MCP_CDP_ENDPOINT": device.endpoint}
        server = McpServerConfig(
            server_id=server.server_id,
            server_name=server.server_name,
            server_path=given.server_path,
            client_type=given.client_type,
            params={**given.params, "env": env},
        )
    return PlaywrightMcpDriver(PlaywrightMcpConfig(server))


__all__ = ["PlaywrightMcpConfig", "PlaywrightMcpDriver", "create_playwright_mcp_driver"]
