# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The browser's conformance scene: local pages served on ``127.0.0.1`` and the cases the suite plays there.

Every element on the pages reports what happened to it in ``document.title`` (``clicked``, ``form:3:true``, ...), so a
case checks its effect through ``BrowserContext.title``, which every browser driver observes. Elements are found by
their accessible name; a ``Locator`` uses their CSS id.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverRef,
    Element,
    HandleDialog,
    Hover,
    IndexRef,
    Locator,
    Observation,
    PointRef,
    PressKey,
    Ref,
    RunScript,
    Scene,
    SceneCase,
    Scroll,
    SwitchContext,
    TypeText,
    Wait,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FieldKind,
    FillForm,
    FormField,
    GetConsole,
    GetNetwork,
    GetPdf,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene.server import SceneServer

PAGES = Path(__file__).parent / "pages"
UPLOAD_NAME = "scene-upload.txt"

Actions = Mapping[type[Action], frozenset[type]]
Check = Callable[[Observation, Observation, ActionResult], bool]


@dataclass(frozen=True)
class _Target:
    name: str
    css_id: str


SAVE = _Target("Save order", "save")
NAME = _Target("Full name", "name")
GIFT = _Target("Gift wrap", "gift")
SIZE = _Target("Size", "size")
KEYS = _Target("Key field", "keys")
HOVER = _Target("Hover target", "hover")
CONFIRM = _Target("Show confirm", "confirm")
POPUP = _Target("Open popup", "popup")
DRAG_SOURCE = _Target("Drag source", "drag-source")
DROP_TARGET = _Target("Drop target", "drop-target")
FILE_DROP = _Target("File drop zone", "file-drop")
UPLOAD = _Target("Upload file", "upload")
SCROLL_BOX = _Target("Scroll box", "scroll-box")
COVERED = _Target("Covered button", "covered")
LAST = _Target("Last button", "last")
SECOND_LINK = _Target("Go to second", "second-link")

# The intercept case covers the button only after observing it (some drivers do not list occluded elements).
_COVER_SCRIPT = "() => { cover(); return true; }"
# The timeout case waits this long for a text that is on no scene page.
_ABSENT_TEXT = "scene text that never appears"
_TIMEOUT_S = 2.0

# Ref kinds the scene can build, in order of preference, for setup steps and for element-targeted cases.
_ANY_KINDS = (DriverRef, Locator, PointRef)
_ELEMENT_KINDS = (DriverRef, Locator)


def _element(observation: Observation, target: _Target) -> Element:
    wanted = target.name.casefold()
    names = [(element, (element.name or "").strip().casefold()) for element in observation.elements]
    exact = [element for element, name in names if name == wanted]
    if exact:
        return exact[0]
    partial = [element for element, name in names if wanted in name]
    if len(partial) == 1:
        return partial[0]
    raise LookupError(
        f"no single element named {target.name!r} in the observation; names: {[e.name for e in observation.elements]}"
    )


def _ref(observation: Observation, target: _Target, kind: type) -> Ref:
    if kind is Locator:
        return Locator(css=f"#{target.css_id}", text=None, generation=observation.generation)
    element = _element(observation, target)
    if kind is DriverRef:
        return element.ref
    if kind is PointRef:
        if element.bounds is None:
            raise LookupError(f"the driver reports no bounds for {target.name!r}")
        x, y, width, height = element.bounds
        scale = observation.screenshot.scale if observation.screenshot is not None else 1.0
        return PointRef(
            x=round((x + width / 2) * scale), y=round((y + height / 2) * scale), generation=observation.generation
        )
    raise LookupError(f"the scene cannot build a {kind.__name__}")


def _kind_for(actions: Actions, command: type[Action], kinds: Sequence[type] = _ANY_KINDS) -> type | str:
    """The ref kind a case uses for ``command``, or the reason the scene cannot provide the case."""
    accepted = actions.get(command)
    if accepted is None:
        return f"the driver does not handle {command.__name__}, which this case needs"
    for kind in kinds:
        if kind in accepted:
            return kind
    return f"the scene addresses elements for {command.__name__} only as {[k.__name__ for k in kinds]}"


def _surface(observation: Observation) -> BrowserContext | None:
    return observation.surface if isinstance(observation.surface, BrowserContext) else None


def _title(observation: Observation) -> str:
    context = _surface(observation)
    return context.title if context is not None else observation.context.title or ""


def _url(observation: Observation) -> str:
    context = _surface(observation)
    return context.url if context is not None else ""


def _tabs(observation: Observation) -> tuple[ContextRef, ...]:
    context = _surface(observation)
    return context.tabs if context is not None else ()


def _other_tab(observation: Observation, not_id: str) -> ContextRef:
    for tab in _tabs(observation):
        if tab.id != not_id:
            return tab
    raise LookupError(f"no tab other than {not_id!r} in {_tabs(observation)!r}")


def _title_is(expected: str) -> Check:
    return lambda before, after, result: _title(after) == expected


def _close_extra_tab(seen: Sequence[Observation]) -> Action:
    return CloseContext(context=_other_tab(seen[-1], seen[0].context.id))


class _BrowserScene:
    def __init__(self) -> None:
        self._server = SceneServer(PAGES)
        self._base = ""
        self._files: Path | None = None
        self._happy: dict[type[Action], Callable[[type | None, Actions], SceneCase | str]] = {
            Click: self._click,
            TypeText: self._type_text,
            PressKey: self._press_key,
            Scroll: self._scroll,
            Hover: self._hover,
            Drag: self._drag,
            Wait: self._wait,
            SwitchContext: self._switch_context,
            CloseContext: self._close_context,
            HandleDialog: self._handle_dialog,
            RunScript: self._run_script,
            Navigate: self._navigate,
            Back: self._back,
            OpenContext: self._open_context,
            SelectOption: self._select_option,
            UploadFile: self._upload_file,
            FillForm: self._fill_form,
            Drop: self._drop,
            GetPdf: self._get_pdf,
            GetConsole: self._get_console,
            GetNetwork: self._get_network,
        }

    def start(self) -> None:
        self._base = self._server.start()
        self._files = Path(tempfile.mkdtemp(prefix="browser-scene-"))
        (self._files / UPLOAD_NAME).write_text("conformance upload\n", encoding="utf-8")

    def stop(self) -> None:
        self._server.stop()
        if self._files is not None:
            shutil.rmtree(self._files, ignore_errors=True)
            self._files = None

    def url(self, page: str) -> str:
        return self._base + page

    @property
    def upload_path(self) -> str:
        return str(self._files / UPLOAD_NAME) if self._files is not None else UPLOAD_NAME

    def enter(self) -> Sequence[Action]:
        return (Navigate(url=self.url("index.html")),)

    def _with_target(
        self, kind: type | None, actions: Actions, target: _Target, make: Callable, check: Check
    ) -> SceneCase | str:
        """``make(ref)`` targets ``target`` directly, or (``kind`` is ``None``) acts on it after a click focused it."""
        if kind is not None:
            return SceneCase(steps=(lambda seen: make(_ref(seen[-1], target, kind)),), check=check)
        click_kind = _kind_for(actions, Click)
        if isinstance(click_kind, str):
            return click_kind

        def focus(seen: Sequence[Observation]) -> Action:
            return Click(target=_ref(seen[-1], target, click_kind))

        return SceneCase(steps=(focus, lambda seen: make(None)), check=check)

    # Happy paths, one per command; ``kind`` is the ref kind under test (``None`` for a command without refs).

    def happy(self, command: type[Action], kind: type | None, actions: Actions) -> SceneCase | str:
        if kind is IndexRef:
            return "an IndexRef is internal to a driver; the scene cannot address an element by index"
        build = self._happy.get(command)
        if build is None:
            return f"the scene has no happy path for {command.__name__}"
        return build(kind, actions)

    def _click(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return self._with_target(kind, actions, SAVE, lambda ref: Click(target=ref), _title_is("clicked"))

    def _type_text(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return self._with_target(
            kind, actions, NAME, lambda ref: TypeText(target=ref, text="Ada"), _title_is("form:3:false")
        )

    def _press_key(self, kind: type | None, actions: Actions) -> SceneCase | str:
        def make(ref: Ref | None) -> Action:
            return PressKey(key="Enter", target=ref)

        if kind is None:
            return SceneCase(steps=(lambda seen: make(None),), check=_title_is("key:Enter"))
        return self._with_target(kind, actions, KEYS, make, _title_is("key:Enter"))

    def _scroll(self, kind: type | None, actions: Actions) -> SceneCase | str:
        def make(ref: Ref | None) -> Action:
            return Scroll(target=ref, dy=200)

        if kind is None:
            return SceneCase(steps=(lambda seen: make(None),), check=_title_is("scrolled"))
        return self._with_target(kind, actions, SCROLL_BOX, make, _title_is("scrolled"))

    def _hover(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return self._with_target(kind, actions, HOVER, lambda ref: Hover(target=ref), _title_is("hovered"))

    def _drag(self, kind: type | None, actions: Actions) -> SceneCase | str:
        if kind is None:
            return "Drag needs element refs"

        def step(seen: Sequence[Observation]) -> Action:
            return Drag(source=_ref(seen[-1], DRAG_SOURCE, kind), destination=_ref(seen[-1], DROP_TARGET, kind))

        return SceneCase(steps=(step,), check=_title_is("dragged"))

    def _wait(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(steps=(lambda seen: Wait(seconds=0.2),))

    def _switch_context(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(
                lambda seen: OpenContext(url=self.url("second.html")),
                lambda seen: SwitchContext(context=_other_tab(seen[-1], seen[-1].context.id)),
            ),
            check=lambda before, after, result: after.context.id != before.context.id,
            cleanup=(_close_extra_tab,),
        )

    def _close_context(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(
                lambda seen: OpenContext(url=self.url("second.html")),
                lambda seen: CloseContext(context=_other_tab(seen[-1], seen[0].context.id)),
            ),
            check=lambda before, after, result: len(_tabs(after)) < len(_tabs(before)),
        )

    def _handle_dialog(self, kind: type | None, actions: Actions) -> SceneCase | str:
        click_kind = _kind_for(actions, Click)
        if isinstance(click_kind, str):
            return click_kind
        return SceneCase(
            steps=(
                lambda seen: Click(target=_ref(seen[-1], CONFIRM, click_kind)),
                lambda seen: HandleDialog(accept=True),
            ),
            check=_title_is("confirm:true"),
            observe_between=False,
        )

    def _run_script(self, kind: type | None, actions: Actions) -> SceneCase | str:
        if kind is None:
            code = "() => { document.title = 'script:ran'; return 'ran'; }"
            return SceneCase(steps=(lambda seen: RunScript(code=code),), check=_title_is("script:ran"))
        code = "(element) => { document.title = 'script:' + element.id; return element.id; }"

        def step(seen: Sequence[Observation]) -> Action:
            return RunScript(code=code, target=_ref(seen[-1], SAVE, kind))

        return SceneCase(steps=(step,), check=_title_is("script:save"))

    def _navigate(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(lambda seen: Navigate(url=self.url("second.html")),),
            check=lambda before, after, result: _url(after).endswith("/second.html"),
        )

    def _back(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(lambda seen: Navigate(url=self.url("second.html")), lambda seen: Back()),
            check=lambda before, after, result: _url(after).endswith("/index.html"),
        )

    def _open_context(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(lambda seen: OpenContext(url=self.url("second.html")),),
            check=lambda before, after, result: len(_tabs(after)) > len(_tabs(before)),
            cleanup=(_close_extra_tab,),
        )

    def _select_option(self, kind: type | None, actions: Actions) -> SceneCase | str:
        if kind is None:
            return "SelectOption needs an element ref"

        def step(seen: Sequence[Observation]) -> Action:
            return SelectOption(target=_ref(seen[-1], SIZE, kind), values=("m",))

        return SceneCase(steps=(step,), check=_title_is("size:m"))

    def _upload_file(self, kind: type | None, actions: Actions) -> SceneCase | str:
        check = _title_is(f"upload:{UPLOAD_NAME}")
        if kind is not None:

            def step(seen: Sequence[Observation]) -> Action:
                return UploadFile(target=_ref(seen[-1], UPLOAD, kind), paths=(self.upload_path,))

            return SceneCase(steps=(step,), check=check)
        click_kind = _kind_for(actions, Click)
        if isinstance(click_kind, str):
            return click_kind
        return SceneCase(
            steps=(
                lambda seen: Click(target=_ref(seen[-1], UPLOAD, click_kind)),
                lambda seen: UploadFile(target=None, paths=(self.upload_path,)),
            ),
            check=check,
            observe_between=False,
        )

    def _fill_form(self, kind: type | None, actions: Actions) -> SceneCase | str:
        if kind is None:
            return "FillForm needs element refs"

        def step(seen: Sequence[Observation]) -> Action:
            return FillForm(
                fields=(
                    FormField(target=_ref(seen[-1], NAME, kind), kind=FieldKind.TEXTBOX, value="Ada"),
                    FormField(target=_ref(seen[-1], GIFT, kind), kind=FieldKind.CHECKBOX, value="true"),
                )
            )

        return SceneCase(steps=(step,), check=_title_is("form:3:true"))

    def _drop(self, kind: type | None, actions: Actions) -> SceneCase | str:
        if kind is None:
            return "Drop needs an element ref"

        def step(seen: Sequence[Observation]) -> Action:
            return Drop(target=_ref(seen[-1], FILE_DROP, kind), paths=(self.upload_path,))

        return SceneCase(steps=(step,), check=_title_is("dropped:1"))

    def _get_pdf(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(steps=(lambda seen: GetPdf(),), check=lambda before, after, result: bool(result.value))

    def _get_console(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(lambda seen: GetConsole(),),
            check=lambda before, after, result: "scene console message" in str(result.value),
        )

    def _get_network(self, kind: type | None, actions: Actions) -> SceneCase | str:
        return SceneCase(
            steps=(lambda seen: GetNetwork(),), check=lambda before, after, result: "data.json" in str(result.value)
        )

    # Error cases.

    def stale(self, actions: Actions) -> SceneCase | str:
        if DriverRef not in actions.get(Click, frozenset()):
            return "the scene makes a stale ref only as a DriverRef, and the driver does not click DriverRefs"
        return SceneCase(
            steps=(
                lambda seen: Navigate(url=self.url("second.html")),
                lambda seen: Click(target=_element(seen[0], LAST).ref),
            )
        )

    def _click_case(self, actions: Actions, target: _Target, kinds: Sequence[type], **options) -> SceneCase | str:
        kind = _kind_for(actions, Click, kinds)
        if isinstance(kind, str):
            return kind
        return SceneCase(steps=(lambda seen: Click(target=_ref(seen[-1], target, kind)),), **options)

    def intercepted(self, actions: Actions) -> SceneCase | str:
        kind = _kind_for(actions, Click, _ELEMENT_KINDS)
        if isinstance(kind, str):
            return kind
        if RunScript not in actions:
            return "the driver does not handle RunScript, which this case needs to cover the button"
        # No observation between covering and clicking: the click uses the ref taken while the button was visible.
        return SceneCase(
            steps=(lambda seen: RunScript(code=_COVER_SCRIPT), lambda seen: Click(target=_ref(seen[0], COVERED, kind))),
            observe_between=False,
        )

    def timeout(self, actions: Actions) -> SceneCase | str:
        if Wait not in actions:
            return "the driver does not handle Wait, which this case needs"
        return SceneCase(steps=(lambda seen: Wait(until_text=_ABSENT_TEXT, seconds=_TIMEOUT_S),))

    # Sensitive values: the suite also types its secret through the TypeText happy path.

    def sensitive(self) -> dict[str, Callable[[Actions, str], SceneCase | str]]:
        return {"form-field": self._sensitive_form_field}

    def _sensitive_form_field(self, actions: Actions, secret: str) -> SceneCase | str:
        kind = _kind_for(actions, FillForm)
        if isinstance(kind, str):
            return kind

        def step(seen: Sequence[Observation]) -> Action:
            field = FormField(target=_ref(seen[-1], NAME, kind), kind=FieldKind.TEXTBOX, value=secret, sensitive=True)
            return FillForm(fields=(field,))

        return SceneCase(steps=(step,))

    # Context changes (plan 8.1): new document (also a same-URL reload), URL change, dialog, tab opened/closed/switched.

    def context_changes(self) -> dict[str, Callable[[Actions], SceneCase | str]]:
        def second(seen: Sequence[Observation]) -> Action:
            return OpenContext(url=self.url("second.html"))

        return {
            "navigate": lambda actions: SceneCase(steps=(lambda seen: Navigate(url=self.url("second.html")),)),
            "same-url-reload": lambda actions: SceneCase(steps=(lambda seen: Navigate(url=self.url("index.html")),)),
            "link-click": lambda actions: self._click_case(actions, SECOND_LINK, _ANY_KINDS),
            "open-tab": lambda actions: SceneCase(steps=(second,), cleanup=(_close_extra_tab,)),
            "popup-click": lambda actions: self._click_case(actions, POPUP, _ANY_KINDS, cleanup=(_close_extra_tab,)),
            "switch-tab": lambda actions: SceneCase(
                steps=(second, lambda seen: SwitchContext(context=_other_tab(seen[-1], seen[-1].context.id))),
                cleanup=(_close_extra_tab,),
            ),
            "close-tab": lambda actions: SceneCase(
                steps=(second, lambda seen: CloseContext(context=_other_tab(seen[-1], seen[0].context.id)))
            ),
            "dialog": lambda actions: self._click_case(
                actions,
                CONFIRM,
                _ANY_KINDS,
                observe_between=False,
                cleanup=(lambda seen: HandleDialog(accept=False),),
            ),
        }


def browser_scene() -> Scene:
    """A new, not yet started browser scene for the conformance suite (pages on ``127.0.0.1``, no network)."""
    scene = _BrowserScene()
    return Scene(
        surface="browser",
        start=scene.start,
        stop=scene.stop,
        enter=scene.enter,
        happy=scene.happy,
        stale=scene.stale,
        intercepted=scene.intercepted,
        timeout=scene.timeout,
        context_changes=scene.context_changes(),
        sensitive=scene.sensitive(),
    )


__all__ = ["browser_scene"]
