#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Self-test of the conformance suite on ``FakeDriver``; each deliberately wrong driver makes its case fail."""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverError,
    DriverInfo,
    DriverRef,
    Element,
    ElementNotFound,
    HandleDialog,
    Hover,
    IndexRef,
    Observation,
    Observe,
    PointRef,
    PressKey,
    Ref,
    RunScript,
    Scene,
    SceneCase,
    Scroll,
    StaleRef,
    SurfaceContext,
    SwitchContext,
    TypeText,
    Unsupported,
    Wait,
    ensure_supported,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.contract.testing.conformance import (
    SECRET_TEXT,
    DriverConformance,
    TraceSink,
    check_context_change,
    check_declared_actions,
    check_fingerprint,
    check_happy_path,
    check_intercepted,
    check_sensitive_not_logged,
    check_sensitive_not_traced,
    check_stale_ref,
    check_surface,
    check_timeout,
    check_unhandled,
)

pytest_plugins = ["openjiuwen.harness.tools.browser_move.contract.testing.conformance"]

HOME = ("Save", "Name", "Covered", "Next", "Alert", "Popup", "Box", "Target")
NEXT = ("Home",)
ROW = 10.0


@dataclasses.dataclass(frozen=True)
class FakeScreen(SurfaceContext):
    dialog_open: bool = False


class FakeWorld:
    """What a ``WorldDriver`` acts on. Element ``i`` of a document is ``e<i>`` at ``y = 10 * i``."""

    def __init__(self) -> None:
        self.documents = {"c1": "home"}
        self.loads = {"c1": 1}
        self.current = "c1"
        self.title = ""
        self.late_waits = 0
        self.dialog_open = False
        self._opened = 1

    def generation(self) -> str:
        return f"{self.current}.{self.loads[self.current]}"

    def names(self) -> tuple[str, ...]:
        return HOME if self.documents[self.current] == "home" else NEXT

    def observe(self) -> Observation:
        generation = self.generation()
        elements = tuple(
            Element(ref=DriverRef(f"e{i}", generation), role="button", name=name, bounds=(0.0, ROW * i, 100.0, ROW))
            for i, name in enumerate(self.names(), start=1)
        )
        return Observation(
            context=ContextRef(self.current, "screen", self.title),
            elements=elements,
            surface=FakeScreen(dialog_open=self.dialog_open),
        )

    def resolve(self, ref: Ref) -> str:
        if isinstance(ref, DriverRef):
            if ref.generation != self.generation():
                raise StaleRef(f"{ref.ref} belongs to {ref.generation}", dispatched=False)
            index = int(ref.ref[1:])
        elif isinstance(ref, PointRef):
            index = int(ref.y // ROW)
        else:
            raise ElementNotFound(f"cannot resolve {ref!r}", dispatched=False)
        if not 1 <= index <= len(self.names()):
            raise ElementNotFound(f"no element {index}", dispatched=False)
        return self.names()[index - 1]

    def _load(self, document: str) -> ActionResult:
        self.documents[self.current] = document
        self.loads[self.current] += 1
        return ActionResult(ok=True, context_changed=True)

    def click(self, name: str) -> ActionResult:
        if name == "Covered":
            raise ActionIntercepted("another element would receive the click", by="overlay", dispatched=False)
        if name == "Next":
            return self._load("next")
        if name == "Home":
            return self._load("home")
        if name == "Alert":
            self.dialog_open = True
            return ActionResult(ok=True, context_changed=True)
        if name == "Popup":
            self._opened += 1
            source = ContextRef(self.current, "screen")
            self.current = f"c{self._opened}"
            self.documents[self.current] = "next"
            self.loads[self.current] = 1
            opened = (ContextRef(self.current, "screen"),)
            return ActionResult(ok=True, context_changed=True, source_context=source, opened_contexts=opened)
        self.title = "clicked" if name == "Save" else f"clicked:{name}"
        return ActionResult(ok=True)

    def wait(self, action: Wait) -> ActionResult:
        """A text that is not on the screen appears late: the first wait for it times out, a second one finds it."""
        if action.until_text is not None and action.until_text not in self.names():
            self.late_waits += 1
            if self.late_waits == 1:
                raise ActionTimeout(f"{action.until_text!r} did not appear in {action.seconds}s", dispatched=True)
        return ActionResult(ok=True)

    def apply(self, action: Action) -> ActionResult:
        if isinstance(action, Wait):
            return self.wait(action)
        if isinstance(action, Click):
            return self.click(self.resolve(action.target))
        if isinstance(action, Drag):
            self.resolve(action.source)
            self.resolve(action.destination)
            self.title = "dragged"
        elif isinstance(action, (TypeText, PressKey, Scroll, Hover)) and action.target is not None:
            self.resolve(action.target)
        if isinstance(action, TypeText):
            self.title = f"typed:{len(action.text)}"
        elif isinstance(action, PressKey):
            self.title = f"key:{action.key}"
        elif isinstance(action, Scroll):
            self.title = "scrolled"
        elif isinstance(action, Hover):
            self.title = "hovered"
        elif isinstance(action, HandleDialog):
            self.dialog_open = False
            self.title = f"dialog:{action.accept}"
        elif isinstance(action, RunScript):
            return ActionResult(ok=True, value="ran")
        elif isinstance(action, SwitchContext):
            self.current = action.context.id
            return ActionResult(ok=True, context_changed=True)
        elif isinstance(action, CloseContext):
            del self.documents[action.context.id]
            self.current = next(iter(self.documents))
            return ActionResult(ok=True, context_changed=True)
        return ActionResult(ok=True)


FAKE_ACTIONS: dict[type[Action], frozenset[type]] = {
    Click: frozenset({DriverRef, PointRef, IndexRef}),
    TypeText: frozenset({DriverRef}),
    PressKey: frozenset({DriverRef}),
    Scroll: frozenset({DriverRef}),
    Hover: frozenset({DriverRef}),
    Drag: frozenset({DriverRef}),
    Wait: frozenset(),
    SwitchContext: frozenset(),
    CloseContext: frozenset(),
    HandleDialog: frozenset(),
    RunScript: frozenset(),
}
FAKE_INFO = DriverInfo(name="fake-world", surfaces=frozenset({"fake"}), actions=FAKE_ACTIONS)


class WorldDriver(FakeDriver):
    """A correct driver: a ``FakeDriver`` whose scripted answers come from a ``FakeWorld``, with a masking trace."""

    def __init__(self, sink: TraceSink, info: DriverInfo = FAKE_INFO) -> None:
        super().__init__(info=info)
        self.world = FakeWorld()
        self.sink = sink

    async def observe(self, command: Observe) -> Observation:
        self.script(observations=(self.world.observe(),))
        return await super().observe(command)

    async def execute(self, action: Action) -> ActionResult:
        ensure_supported(self.info.name, self.info.actions, action)
        self.trace(action)
        try:
            self.script(results=(self.world.apply(action),))
        except DriverError as error:
            self.fail_next(error)
        return await super().execute(action)

    def trace(self, action: Action) -> None:
        if isinstance(action, TypeText) and action.sensitive:
            action = dataclasses.replace(action, text="***")
        elif isinstance(action, RunScript) and action.sensitive:
            action = dataclasses.replace(action, code="***")
        self.sink(action)


def _element(observation: Observation, name: str) -> Element:
    for element in observation.elements:
        if element.name == name:
            return element
    raise LookupError(f"no element {name!r}")


def _ref(observation: Observation, name: str, kind: type = DriverRef) -> Ref:
    element = _element(observation, name)
    if kind is PointRef:
        x, y, width, height = element.bounds
        return PointRef(x=int(x + width / 2), y=int(y + height / 2), generation=observation.generation)
    return element.ref


def _title_is(expected: str):
    return lambda before, after, result: after.context.title == expected


def _context_moved(before: Observation, after: Observation, result: ActionResult) -> bool:
    return after.context.id != before.context.id


def _shows_dialog(before: Observation, after: Observation, result: ActionResult) -> bool:
    return isinstance(after.surface, FakeScreen) and after.surface.dialog_open


def _click(name: str):
    return lambda seen: Click(target=_ref(seen[-1], name))


def _happy(command: type[Action], kind: type | None, actions) -> SceneCase | str:
    if kind is IndexRef:
        return "an IndexRef is internal to the driver"
    cases = {
        Click: lambda: SceneCase(
            steps=(lambda seen: Click(target=_ref(seen[-1], "Save", kind)),), check=_title_is("clicked")
        ),
        TypeText: lambda: SceneCase(
            steps=(lambda seen: TypeText(target=_ref(seen[-1], "Name"), text="Ada"),), check=_title_is("typed:3")
        ),
        PressKey: lambda: SceneCase(
            steps=(lambda seen: PressKey(key="Enter", target=_ref(seen[-1], "Name")),), check=_title_is("key:Enter")
        ),
        Scroll: lambda: SceneCase(
            steps=(lambda seen: Scroll(target=_ref(seen[-1], "Box"), dy=100),), check=_title_is("scrolled")
        ),
        Hover: lambda: SceneCase(steps=(lambda seen: Hover(target=_ref(seen[-1], "Box")),), check=_title_is("hovered")),
        Drag: lambda: SceneCase(
            steps=(lambda seen: Drag(source=_ref(seen[-1], "Box"), destination=_ref(seen[-1], "Target")),),
            check=_title_is("dragged"),
        ),
        Wait: lambda: SceneCase(steps=(lambda seen: Wait(seconds=0),)),
        SwitchContext: lambda: SceneCase(
            steps=(_click("Popup"), lambda seen: SwitchContext(context=seen[0].context)), check=_context_moved
        ),
        CloseContext: lambda: SceneCase(
            steps=(_click("Popup"), lambda seen: CloseContext(context=seen[-1].context)), check=_context_moved
        ),
        HandleDialog: lambda: SceneCase(
            steps=(_click("Alert"), lambda seen: HandleDialog(accept=True)),
            check=_title_is("dialog:True"),
            observe_between=False,
        ),
        RunScript: lambda: SceneCase(
            steps=(lambda seen: RunScript(code="noop"),), check=lambda before, after, result: result.value == "ran"
        ),
    }
    build = cases.get(command)
    return build() if build is not None else f"no happy path for {command.__name__}"


def _scene(**overrides) -> Scene:
    fields = dict(
        surface="fake",
        start=lambda: None,
        stop=lambda: None,
        happy=_happy,
        stale=lambda actions: SceneCase(
            steps=(_click("Next"), lambda seen: Click(target=_element(seen[0], "Save").ref))
        ),
        intercepted=lambda actions: SceneCase(steps=(_click("Covered"),)),
        timeout=lambda actions: SceneCase(steps=(lambda seen: Wait(until_text="Late", seconds=0.1),)),
        context_changes={
            "new-document": lambda actions: SceneCase(steps=(_click("Next"),)),
            "dialog": lambda actions: SceneCase(
                steps=(_click("Alert"),),
                check=_shows_dialog,
                observe_between=False,
                cleanup=(lambda seen: HandleDialog(accept=False),),
            ),
            "opened": lambda actions: SceneCase(steps=(_click("Popup"),)),
            "switched": lambda actions: SceneCase(
                steps=(_click("Popup"), lambda seen: SwitchContext(context=seen[0].context))
            ),
            "closed": lambda actions: SceneCase(
                steps=(_click("Popup"), lambda seen: CloseContext(context=seen[-1].context))
            ),
        },
        sensitive={
            "script": lambda actions, secret: SceneCase(steps=(lambda seen: RunScript(code=secret, sensitive=True),)),
        },
    )
    fields.update(overrides)
    return Scene(**fields)


FAKE_SCENE = _scene()


class TestConformanceOnFakeDriver(DriverConformance):
    scene = FAKE_SCENE
    driver_actions = FAKE_ACTIONS

    @pytest.fixture
    def conformance_driver_factory(self):
        return WorldDriver


class RetriesDispatchedAction(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        try:
            return await super().execute(action)
        except ActionTimeout:
            return await super().execute(action)


class StaleAfterDispatch(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        try:
            return await super().execute(action)
        except StaleRef as error:
            raise StaleRef(str(error), dispatched=True) from error


class NoContextSignal(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        return dataclasses.replace(await super().execute(action), context_changed=False)


class DialogNotObserved(WorldDriver):
    async def observe(self, command: Observe) -> Observation:
        return dataclasses.replace(await super().observe(command), surface=None)


class InterceptedAfterDispatch(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        try:
            return await super().execute(action)
        except ActionIntercepted as error:
            raise ActionIntercepted(str(error), by=error.by, dispatched=True) from error


class AnonymousIntercept(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        try:
            return await super().execute(action)
        except ActionIntercepted as error:
            raise ActionIntercepted(str(error), by="", dispatched=error.dispatched) from error


class UnsupportedAsGenericError(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        try:
            return await super().execute(action)
        except Unsupported as error:
            raise DriverError(str(error)) from error


class UnstableFingerprint(WorldDriver):
    async def observe(self, command: Observe) -> Observation:
        observation = await super().observe(command)
        count = len(self.calls)
        elements = tuple(dataclasses.replace(e, name=f"{e.name} {count}") for e in observation.elements)
        return dataclasses.replace(observation, elements=elements)


class LeakyTrace(WorldDriver):
    def trace(self, action: Action) -> None:
        self.sink(action)


_LEAKY_LOGGER = logging.getLogger("conformance.selftest.leaky")
_LEAKY_LOGGER.propagate = False


class LeakyLog(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        if isinstance(action, TypeText):
            _LEAKY_LOGGER.debug("typing %s", action.text)
        elif isinstance(action, RunScript):
            _LEAKY_LOGGER.debug("running %s", action.code)
        return await super().execute(action)


class ClickWithoutEffect(WorldDriver):
    async def execute(self, action: Action) -> ActionResult:
        if isinstance(action, Click) and isinstance(action.target, DriverRef):
            ensure_supported(self.info.name, self.info.actions, action)
            return ActionResult(ok=True)
        return await super().execute(action)


def _driver_with(info: DriverInfo):
    return lambda sink: WorldDriver(sink, info=info)


def _run(check, factory, *args) -> None:
    asyncio.run(check(factory, FAKE_SCENE, *args))


@pytest.mark.parametrize(
    "check, factory, args, message",
    [
        pytest.param(check_timeout, RetriesDispatchedAction, (), "expected ActionTimeout", id="retries-dispatched"),
        pytest.param(check_stale_ref, StaleAfterDispatch, (), "before dispatch", id="stale-ref-dispatched"),
        pytest.param(
            check_context_change, NoContextSignal, ("new-document",), "did not report context_changed", id="no-signal"
        ),
        pytest.param(
            check_context_change, DialogNotObserved, ("dialog",), "fails the scene's check", id="dialog-not-observed"
        ),
        pytest.param(check_intercepted, AnonymousIntercept, (), "must describe the element", id="intercept-no-by"),
        pytest.param(
            check_intercepted, InterceptedAfterDispatch, (), "never reached the target", id="intercept-dispatched"
        ),
        pytest.param(check_unhandled, UnsupportedAsGenericError, (), "expected Unsupported", id="unhandled-generic"),
        pytest.param(check_fingerprint, UnstableFingerprint, (), "fingerprint changed", id="unstable-fingerprint"),
        pytest.param(check_sensitive_not_traced, LeakyTrace, (), "reached the trace", id="leaky-trace"),
        pytest.param(check_sensitive_not_traced, LeakyTrace, ("script",), "reached the trace", id="leaky-trace-script"),
        pytest.param(check_sensitive_not_logged, LeakyLog, (), "reached the logs", id="leaky-log"),
        pytest.param(check_sensitive_not_logged, LeakyLog, ("script",), "reached the logs", id="leaky-log-script"),
        pytest.param(check_happy_path, ClickWithoutEffect, (Click, DriverRef), "did not take effect", id="no-effect"),
        pytest.param(
            check_surface,
            _driver_with(dataclasses.replace(FAKE_INFO, surfaces=frozenset({"other"}))),
            (),
            "not the scene's 'fake'",
            id="wrong-surface",
        ),
        pytest.param(
            check_declared_actions,
            _driver_with(dataclasses.replace(FAKE_INFO, actions={Click: frozenset({DriverRef})})),
            (FAKE_ACTIONS,),
            "differs from driver_actions",
            id="drifted-actions",
        ),
    ],
)
def test_case_fails_for_a_wrong_driver(check, factory, args, message):
    with pytest.raises(AssertionError, match=message):
        _run(check, factory, *args)


def test_retrying_driver_really_repeats_the_dispatched_action():
    drivers: list[RetriesDispatchedAction] = []

    def factory(sink: TraceSink) -> RetriesDispatchedAction:
        drivers.append(RetriesDispatchedAction(sink))
        return drivers[-1]

    with pytest.raises(AssertionError, match="expected ActionTimeout"):
        _run(check_timeout, factory)
    assert drivers[0].world.late_waits == 2


def test_a_timeout_case_that_is_not_a_bounded_wait_for_text_fails():
    scene = _scene(timeout=lambda actions: SceneCase(steps=(lambda seen: Wait(until_text="Late"),)))
    with pytest.raises(AssertionError, match=r"must end in Wait\(until_text=..., seconds=...\)"):
        asyncio.run(check_timeout(WorldDriver, scene))


def test_a_sensitive_case_that_does_not_send_the_secret_fails():
    scene = _scene(sensitive={"script": lambda actions, secret: SceneCase(steps=(lambda seen: RunScript(code="x"),))})
    with pytest.raises(AssertionError, match="does not send the secret"):
        asyncio.run(check_sensitive_not_logged(WorldDriver, scene, "script"))


def test_the_correct_driver_keeps_the_secret_out_of_a_non_empty_trace():
    sinks: list[TraceSink] = []

    def factory(sink: TraceSink) -> WorldDriver:
        sinks.append(sink)
        return WorldDriver(sink)

    _run(check_sensitive_not_traced, factory)
    assert sinks[0].records
    assert SECRET_TEXT not in sinks[0].text


def test_a_case_the_scene_cannot_provide_is_skipped_with_its_reason():
    with pytest.raises(pytest.skip.Exception, match="IndexRef is internal to the driver"):
        _run(check_happy_path, WorldDriver, Click, IndexRef)


def test_a_scene_without_a_case_skips_with_the_default_reason():
    bare = Scene(surface="fake", start=lambda: None, stop=lambda: None)
    with pytest.raises(pytest.skip.Exception, match="does not provide this case"):
        asyncio.run(check_stale_ref(WorldDriver, bare))


def test_a_case_needing_an_unhandled_setup_command_is_skipped_naming_it():
    without_click = dataclasses.replace(FAKE_INFO, actions={HandleDialog: frozenset()})
    with pytest.raises(pytest.skip.Exception, match="does not handle Click"):
        _run(check_happy_path, _driver_with(without_click), HandleDialog, None)


def test_a_scene_built_with_the_wrong_ref_kind_fails():
    scene = _scene(happy=lambda command, kind, actions: _happy(command, DriverRef, actions))
    with pytest.raises(AssertionError, match="for ref kind PointRef"):
        asyncio.run(check_happy_path(WorldDriver, scene, Click, PointRef))
