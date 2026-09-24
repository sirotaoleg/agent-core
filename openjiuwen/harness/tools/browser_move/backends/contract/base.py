# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""BrowserDriver protocol and value types.

The driver is eyes and hands only: it observes the page and performs
low-level actions. Orchestration (PageState, generation accounting, probe
scoring, semantic state, batch sequencing, wait_for_* polling) stays in
``BrowserAgentRuntime``. The driver never sees a ``target_id`` or a
PageState ``generation_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class IndexRef:
    """Reference an element by its last-observed selector-map index.

    ``driver_generation`` pins the observation this index was minted in; a
    driver must raise ``StaleIndexError`` if the current driver generation
    has moved on and the index no longer safely identifies the same node.
    """

    index: int
    driver_generation: int


@dataclass(frozen=True)
class SelectorRef:
    """Reference an element by a CSS selector, optionally the Nth match."""

    css: str
    nth: int = 0


@dataclass(frozen=True)
class TextRef:
    """Reference an element by visible text, optionally scoped by role."""

    text: str
    role: str | None = None


@dataclass(frozen=True)
class NodeRef:
    """Reference an element by CDP backend node id.

    This remains a legitimate ref for CDP-based backends (browser-use today).
    A backend with no CDP node id (a screenshot+coordinates CUA backend, a
    WebDriver backend, an accessibility-ref backend) cannot construct one and
    must not fabricate one; it addresses the same durable element through
    ``DriverRef`` instead. Both are valid ``ElementRef`` members.
    """

    backend_node_id: int
    frame_id: str | None = None


@dataclass(frozen=True)
class DriverRef:
    """Opaque, driver-minted durable element handle.

    The runtime MUST NOT parse ``handle``, compare it structurally, or derive
    any meaning from it. It is a receipt: store it, hand it back. This is the
    backend-neutral counterpart to ``NodeRef`` -- any backend, CDP-based or
    not, mints one for every observed element, so the runtime always has a
    durable fallback to retry with when the ephemeral ``IndexRef`` goes stale
    (see ``StaleIndexError``), regardless of what identity system the backend
    uses internally.
    """

    handle: str
    driver_generation: int


ElementRef = IndexRef | SelectorRef | TextRef | NodeRef | DriverRef


@dataclass(frozen=True)
class Box:
    """Element bounding box in CSS pixels, viewport-relative."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class TabRef:
    """One browser tab as reported by the driver.

    ``target_id`` is an opaque per-backend tab handle and is UNRELATED to
    PageState ``target_id``. For browser-use it happens to be a literal CDP
    target id (that is what the sidecar returns); other backends may use any
    stable string identity for a tab.
    """

    target_id: str
    url: str
    title: str
    active: bool


@dataclass(frozen=True)
class ObservedElement:
    """One interactive/observable DOM node from an ``observe()`` call.

    ``driver_ref`` is REQUIRED and minted by the driver for every element --
    it is the only way the runtime obtains a durable handle at all.
    ``backend_node_id`` is optional: CDP-based backends (browser-use) populate
    it because they genuinely have one, but a backend with no DOM (a
    screenshot+coordinates CUA backend, a WebDriver backend, an
    accessibility-ref backend) leaves it ``None`` rather than fabricate one.
    """

    index: int
    driver_ref: DriverRef
    frame_id: str | None
    tag: str
    role: str | None
    name: str | None
    value: str | None
    backend_node_id: int | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    box: Box | None = None
    visible: bool = False
    scrollable: bool = False


@dataclass(frozen=True)
class Observation:
    """A full page observation.

    ``ax_text`` fills the slot today held by Playwright MCP's
    ``browser_snapshot`` text. It is sourced from the backend's own
    LLM-oriented page representation and is not expected to be
    byte-identical to that AX snapshot format.
    """

    url: str
    title: str
    tabs: tuple[TabRef, ...]
    elements: tuple[ObservedElement, ...]
    ax_text: str | None
    screenshot_b64: str | None
    pixels_above: int
    pixels_below: int
    errors: tuple[str, ...]
    is_pdf_viewer: bool
    captured_at: float
    driver_generation: int


@dataclass(frozen=True)
class ResolvedElement:
    """The concrete DOM node an ``ElementRef`` resolved to.

    See ``ObservedElement`` for why ``driver_ref`` is required and
    ``backend_node_id`` is optional.
    """

    driver_ref: DriverRef
    frame_id: str | None
    box: Box | None
    visible: bool
    tag: str
    backend_node_id: int | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActResult:
    """Result of one hands action.

    ``document_changed`` is the observed replacement for the tool-name
    sniffing the runtime used to do to detect page-identity changes.
    """

    ok: bool
    detail: str
    document_changed: bool
    driver_generation: int


@dataclass(frozen=True)
class NavResult:
    """Result of a navigation action (navigate/back/forward/reload)."""

    url: str
    title: str
    changed_document: bool
    driver_generation: int


@dataclass(frozen=True)
class DriverInfo:
    """Static facts about a connected driver, returned by ``connect()``."""

    backend: str
    browser_version: str
    protocol_version: str
    backend_version: str
    initial_url: str


@dataclass(frozen=True)
class DriverHealth:
    """Liveness snapshot returned by ``health()``. Never raises."""

    connected: bool
    url: str
    tab_count: int
    latency_ms: float
    error: str | None = None


@runtime_checkable
class BrowserDriver(Protocol):
    """Eyes-and-hands browser automation surface.

    This contract is backend-neutral: every method describes browser
    automation semantics, not the shape of any one backend's wire protocol.
    ``backends/browser_use/sidecar/wire.py`` is today's ONE registered
    backend, and its wire method names happen to mirror this Protocol
    one-to-one -- that mirroring runs from the Protocol to the sidecar, not
    the other way around. A future backend (a screenshot+coordinates CUA
    loop, a WebDriver backend, a Playwright-MCP accessibility-ref driver)
    implements the same async methods without needing to speak that wire
    format, or any wire format, at all. Implementations own no
    PageState/generation concepts; they only know their own
    ``driver_generation`` and element identity (``ElementRef`` --
    ``IndexRef`` is ephemeral, ``DriverRef``/``NodeRef`` are durable).
    """

    async def connect(self, *, cdp_url: str, timeout_s: float = 30.0) -> DriverInfo: ...

    async def health(self) -> DriverHealth: ...

    async def close(self) -> None: ...

    async def observe(
        self,
        *,
        include_dom: bool = True,
        include_screenshot: bool = False,
        cached: bool = False,
    ) -> Observation: ...

    async def screenshot(self, *, full_page: bool = False, clip: Box | None = None) -> str: ...

    async def list_tabs(self) -> tuple[TabRef, ...]: ...

    async def resolve(self, ref: ElementRef) -> ResolvedElement: ...

    async def stamp(self, ref: ElementRef, *, attribute: str, value: str) -> str: ...

    async def navigate(
        self,
        url: str,
        *,
        wait_until: str = "load",
        timeout_ms: int | None = None,
        new_tab: bool = False,
    ) -> NavResult: ...

    async def go_back(self) -> NavResult: ...

    async def go_forward(self) -> NavResult: ...

    async def reload(self) -> NavResult: ...

    async def click(
        self,
        ref: ElementRef,
        *,
        button: str = "left",
        click_count: int = 1,
        modifiers: Sequence[str] = (),
    ) -> ActResult: ...

    async def type_text(
        self,
        ref: ElementRef,
        text: str,
        *,
        clear: bool = True,
        press_enter: bool = False,
        sensitive: bool = False,
    ) -> ActResult: ...

    async def press_key(self, keys: str) -> ActResult: ...

    async def select_option(
        self,
        ref: ElementRef,
        *,
        value: str | None = None,
        label: str | None = None,
    ) -> ActResult: ...

    async def set_checked(self, ref: ElementRef, checked: bool) -> ActResult: ...

    async def scroll(
        self,
        *,
        direction: str,
        amount: float,
        ref: ElementRef | None = None,
    ) -> ActResult: ...

    async def upload_files(self, ref: ElementRef, paths: Sequence[str]) -> ActResult: ...

    async def drag(
        self,
        source: ElementRef,
        target: ElementRef,
        *,
        steps: int = 10,
        delay_ms: int = 0,
    ) -> ActResult: ...

    async def hover(self, ref: ElementRef) -> ActResult: ...

    async def handle_dialog(
        self,
        *,
        accept: bool,
        prompt_text: str | None = None,
    ) -> ActResult: ...

    async def drop(
        self,
        ref: ElementRef,
        *,
        paths: Sequence[str] = (),
        data: Sequence[dict[str, str]] = (),
    ) -> ActResult: ...

    async def switch_tab(self, tab: TabRef) -> ActResult: ...

    async def close_tab(self, tab: TabRef) -> ActResult: ...

    async def evaluate(
        self,
        source: str,
        *,
        args: Any = None,
        await_promise: bool = True,
        return_by_value: bool = True,
    ) -> Any: ...

    async def wait_load_state(self, state: str = "load", *, timeout_ms: int) -> ActResult: ...


__all__ = [
    "ActResult",
    "Box",
    "BrowserDriver",
    "DriverHealth",
    "DriverInfo",
    "DriverRef",
    "ElementRef",
    "IndexRef",
    "NavResult",
    "NodeRef",
    "Observation",
    "ObservedElement",
    "ResolvedElement",
    "SelectorRef",
    "TabRef",
    "TextRef",
]
