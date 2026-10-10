# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Driver conformance suite (pytest plugin). PYTEST_DONT_REWRITE: test modules import it before pytest does."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, TypeVar

import pytest

from ..actions import Action, Effect, Observe, TypeText, Wait
from ..driver import Driver, ensure_supported, iter_refs
from ..errors import ActionIntercepted, ActionTimeout, DriverError, StaleRef, Unsupported
from ..observation import Observation
from ..surface import HandledActions, Scene, SceneCase

LIVE_MARKER = "live: runs the conformance suite against a real driver and a live scene"
SECRET_TEXT = "conformance-secret-5d1c"
TYPE_TEXT_CASE = "type-text"

_E = TypeVar("_E", bound=DriverError)


class TraceSink:
    """Collects what a driver, or the tracing middleware around it, writes to the trace."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def __call__(self, record: Any) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(repr(record) for record in self.records)


DriverFactory = Callable[[TraceSink], Driver]


@dataclass(frozen=True)
class UnhandledCommand(Action):
    """A command no driver handles; the suite sends it to check the ``Unsupported`` path."""

    effect: ClassVar[Effect] = Effect.READ


@dataclass(frozen=True)
class _Prepared:
    action: Action
    before: Observation
    seen: tuple[Observation, ...]


def _name(kind: type | None) -> str:
    return "no-ref" if kind is None else kind.__name__


def _pairs(actions: HandledActions) -> list[tuple[type[Action], type | None]]:
    pairs: list[tuple[type[Action], type | None]] = []
    for command in sorted(actions, key=lambda c: (c.__module__, c.__qualname__)):
        kinds = sorted(actions[command], key=_name)
        pairs.extend((command, kind) for kind in (kinds or [None]))
    return pairs


def _brief(observation: Observation) -> str:
    return f"context={observation.context!r} surface={observation.surface!r}"


def _skip_if_reason(scene: Scene, case: SceneCase | str) -> SceneCase:
    if isinstance(case, str):
        pytest.skip(f"scene {scene.surface!r}: {case}")
    return case


def _skip_unless_handled(driver: Driver, action: Action) -> None:
    try:
        ensure_supported(driver.info.name, driver.info.actions, action)
    except Unsupported as error:
        pytest.skip(f"the case needs a command the driver does not handle: {error}")


@contextlib.asynccontextmanager
async def _session(factory: DriverFactory, scene: Scene, sink: TraceSink | None = None) -> AsyncIterator[Driver]:
    driver = factory(sink if sink is not None else TraceSink())
    await driver.start()
    try:
        for action in scene.enter():
            await driver.execute(action)
        yield driver
    finally:
        await driver.stop()


async def _prepare(driver: Driver, case: SceneCase, *, skip_unhandled_last: bool = True) -> _Prepared:
    if not case.steps:
        raise AssertionError("a scene case needs at least one step")
    seen = [await driver.observe(Observe())]
    for step in case.steps[:-1]:
        action = step(tuple(seen))
        _skip_unless_handled(driver, action)
        await driver.execute(action)
        if case.observe_between:
            seen.append(await driver.observe(Observe()))
    action = case.steps[-1](tuple(seen))
    if skip_unhandled_last:
        _skip_unless_handled(driver, action)
    return _Prepared(action, seen[-1], tuple(seen))


async def _cleanup(driver: Driver, case: SceneCase, seen: tuple[Observation, ...]) -> None:
    if not case.cleanup:
        return
    # A cleanup failure must not hide the outcome of the case.
    with contextlib.suppress(DriverError, LookupError):
        if case.observe_between:
            seen = seen + (await driver.observe(Observe()),)
        for step in case.cleanup:
            await driver.execute(step(seen))


async def _raised(driver: Driver, action: Action, expected: type[_E]) -> _E:
    try:
        result = await driver.execute(action)
    except expected as error:
        return error
    except DriverError as error:
        raise AssertionError(
            f"expected {expected.__name__} for {action!r}, got {type(error).__name__}"
            f"(dispatched={error.dispatched}): {error}"
        ) from error
    raise AssertionError(f"expected {expected.__name__} for {action!r}, got {result!r}")


async def _expect_error(factory: DriverFactory, scene: Scene, case_of: Callable, expected: type[_E]) -> _E:
    async with _session(factory, scene) as driver:
        case = _skip_if_reason(scene, case_of(driver.info.actions))
        prepared = await _prepare(driver, case)
        try:
            return await _raised(driver, prepared.action, expected)
        finally:
            await _cleanup(driver, case, prepared.seen)


async def check_surface(factory: DriverFactory, scene: Scene) -> None:
    """The driver lists the scene's surface in ``info.surfaces``."""
    async with _session(factory, scene) as driver:
        info = driver.info
    assert scene.surface in info.surfaces, (
        f"{info.name} lists surfaces {sorted(info.surfaces)}, not the scene's {scene.surface!r}"
    )


async def check_declared_actions(factory: DriverFactory, scene: Scene, declared: HandledActions) -> None:
    async with _session(factory, scene) as driver:
        actual = dict(driver.info.actions)
    assert actual == dict(declared), f"info.actions {actual!r} differs from driver_actions {dict(declared)!r}"


async def check_happy_path(factory: DriverFactory, scene: Scene, command: type[Action], ref_kind: type | None) -> None:
    """The scene's happy path of ``command`` with ``ref_kind`` succeeds and takes effect."""
    async with _session(factory, scene) as driver:
        case = _skip_if_reason(scene, scene.happy(command, ref_kind, driver.info.actions))
        prepared = await _prepare(driver, case, skip_unhandled_last=False)
        action = prepared.action
        assert type(action) is command, f"the scene built {action!r} for {command.__name__}"
        kinds = {type(ref) for ref in iter_refs(action)}
        assert kinds == ({ref_kind} if ref_kind else set()), (
            f"the scene built {action!r} for ref kind {_name(ref_kind)}"
        )
        try:
            result = await driver.execute(action)
            assert result.ok, f"{action!r} returned ok=False: {result.detail}"
            if case.check is not None:
                after = await driver.observe(Observe())
                assert case.check(prepared.before, after, result), (
                    f"{action!r} did not take effect: before {_brief(prepared.before)}, after {_brief(after)}"
                )
        finally:
            await _cleanup(driver, case, prepared.seen)


async def check_stale_ref(factory: DriverFactory, scene: Scene) -> None:
    """A stale ref raises ``StaleRef`` before anything is sent."""
    error = await _expect_error(factory, scene, scene.stale, StaleRef)
    assert error.dispatched is False, f"StaleRef must be raised before dispatch, got dispatched={error.dispatched}"


async def check_context_change(factory: DriverFactory, scene: Scene, name: str) -> None:
    """The scene's context change ``name`` is reported as ``context_changed``; an observation then passes its check."""
    async with _session(factory, scene) as driver:
        case = _skip_if_reason(scene, scene.context_changes[name](driver.info.actions))
        prepared = await _prepare(driver, case)
        try:
            result = await driver.execute(prepared.action)
            after = None if case.check is None else await driver.observe(Observe())
        finally:
            await _cleanup(driver, case, prepared.seen)
    assert result.context_changed, f"{prepared.action!r} ({name}) did not report context_changed: {result!r}"
    if after is not None:
        assert case.check(prepared.before, after, result), (
            f"{prepared.action!r} ({name}) fails the scene's check: result {result!r}, after {_brief(after)}"
        )


async def check_intercepted(factory: DriverFactory, scene: Scene) -> None:
    """A click another element receives raises ``ActionIntercepted`` naming that element, before dispatch."""
    error = await _expect_error(factory, scene, scene.intercepted, ActionIntercepted)
    assert isinstance(error.by, str) and error.by.strip(), (
        f"ActionIntercepted.by must describe the element: {error.by!r}"
    )
    assert error.dispatched is False, (
        f"ActionIntercepted means the input never reached the target, got dispatched={error.dispatched}"
    )


async def check_timeout(factory: DriverFactory, scene: Scene) -> None:
    """A ``Wait`` for text that never appears raises ``ActionTimeout(dispatched=True)`` and is not retried."""
    async with _session(factory, scene) as driver:
        case = _skip_if_reason(scene, scene.timeout(driver.info.actions))
        prepared = await _prepare(driver, case)
        action = prepared.action
        assert isinstance(action, Wait) and action.until_text is not None and action.seconds is not None, (
            f"the timeout case must end in Wait(until_text=..., seconds=...), the scene built {action!r}"
        )
        try:
            error = await _raised(driver, action, ActionTimeout)
        finally:
            await _cleanup(driver, case, prepared.seen)
    assert error.dispatched is True, f"ActionTimeout must count as dispatched, got dispatched={error.dispatched}"


async def check_unhandled(factory: DriverFactory, scene: Scene) -> None:
    """A command the driver does not handle is absent from ``info.actions`` and raises ``Unsupported`` unsent."""
    async with _session(factory, scene) as driver:
        assert UnhandledCommand not in driver.info.actions, "info.actions lists a command no driver handles"
        error = await _raised(driver, UnhandledCommand(), Unsupported)
    assert error.dispatched is False, f"Unsupported must be raised before dispatch, got dispatched={error.dispatched}"


async def check_fingerprint(factory: DriverFactory, scene: Scene) -> None:
    """Two observations of an unchanged scene have the same fingerprint."""
    async with _session(factory, scene) as driver:
        first = await driver.observe(Observe())
        second = await driver.observe(Observe())
    assert first.fingerprint == second.fingerprint, (
        f"fingerprint changed on an unchanged scene: {first.elements!r} vs {second.elements!r}"
    )


def _type_text_case(scene: Scene, actions: HandledActions) -> SceneCase:
    accepted = actions.get(TypeText)
    if accepted is None:
        pytest.skip("the driver does not handle TypeText")
    reasons = []
    for kind in sorted(accepted, key=_name) or [None]:
        case = scene.happy(TypeText, kind, actions)
        if isinstance(case, SceneCase):
            break
        reasons.append(case)
    else:
        pytest.skip(f"scene {scene.surface!r}: {'; '.join(reasons)}")
    last = case.steps[-1]

    def secret(seen: Sequence[Observation]) -> Action:
        return dataclasses.replace(last(seen), text=SECRET_TEXT, sensitive=True)

    return dataclasses.replace(case, steps=case.steps[:-1] + (secret,))


async def _send_secret(factory: DriverFactory, scene: Scene, sink: TraceSink, name: str) -> None:
    async with _session(factory, scene, sink) as driver:
        if name == TYPE_TEXT_CASE:
            case = _type_text_case(scene, driver.info.actions)
        else:
            case = _skip_if_reason(scene, scene.sensitive[name](driver.info.actions, SECRET_TEXT))
        prepared = await _prepare(driver, case)
        assert SECRET_TEXT in repr(prepared.action), f"the case {name!r} does not send the secret: {prepared.action!r}"
        try:
            await driver.execute(prepared.action)
        finally:
            await _cleanup(driver, case, prepared.seen)


async def check_sensitive_not_traced(factory: DriverFactory, scene: Scene, name: str = TYPE_TEXT_CASE) -> None:
    """A sensitive value (``TypeText(sensitive=True)``, or the scene's case ``name``) leaves no text in the trace."""
    sink = TraceSink()
    await _send_secret(factory, scene, sink, name)
    if not sink.records:
        pytest.skip("the driver factory wrote nothing to the trace sink (no tracing middleware around the driver)")
    assert SECRET_TEXT not in sink.text, "sensitive text reached the trace"


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except (TypeError, ValueError):
            self.messages.append(f"{record.msg!r} {record.args!r}")


@contextlib.contextmanager
def _captured_logs() -> Iterator[_ListHandler]:
    # Loggers that do not propagate to the root get the handler too.
    root = logging.getLogger()
    loggers = [root] + [lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    handler = _ListHandler()
    targets = [root] + [lg for lg in loggers[1:] if not lg.propagate]
    levels = [(logger, logger.level) for logger in loggers]
    for logger in loggers:
        if logger is root or logger.level > logging.DEBUG:
            logger.setLevel(logging.DEBUG)
    for logger in targets:
        logger.addHandler(handler)
    try:
        yield handler
    finally:
        for logger in targets:
            logger.removeHandler(handler)
        for logger, level in levels:
            logger.setLevel(level)


async def check_sensitive_not_logged(factory: DriverFactory, scene: Scene, name: str = TYPE_TEXT_CASE) -> None:
    """A sensitive value (as in ``check_sensitive_not_traced``) leaves no text in the logs of this process."""
    with _captured_logs() as handler:
        await _send_secret(factory, scene, TraceSink(), name)
    leaked = [message for message in handler.messages if SECRET_TEXT in message]
    assert not leaked, f"sensitive text reached the logs: {leaked!r}"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", LIVE_MARKER)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "conformance_pair" in metafunc.fixturenames:
        actions = getattr(metafunc.cls, "driver_actions", None)
        if actions is None:
            raise pytest.UsageError(f"{metafunc.cls!r} must set driver_actions to parametrize the conformance suite")
        pairs = _pairs(actions)
        metafunc.parametrize("conformance_pair", pairs, ids=[f"{c.__name__}-{_name(k)}" for c, k in pairs])
    for fixture, names_of in (
        ("conformance_context_change", lambda scene: list(scene.context_changes)),
        ("conformance_sensitive_case", lambda scene: [TYPE_TEXT_CASE, *scene.sensitive]),
    ):
        if fixture not in metafunc.fixturenames:
            continue
        scene = getattr(metafunc.cls, "scene", None)
        if scene is None:
            raise pytest.UsageError(f"{metafunc.cls!r} must set scene to parametrize the conformance suite")
        names = names_of(scene)
        metafunc.parametrize(fixture, names, ids=names)


@pytest.fixture(scope="class")
def conformance_scene(request: pytest.FixtureRequest) -> Iterator[Scene]:
    """The class's ``scene``, started once for the class and stopped after it."""
    scene = getattr(request.cls, "scene", None)
    if scene is None:
        raise pytest.UsageError(f"{request.cls!r} must set scene")
    scene.start()
    try:
        yield scene
    finally:
        scene.stop()


class DriverConformance:
    """Subclass as ``Test...`` and set ``scene``, ``driver_actions`` and a ``conformance_driver_factory`` fixture."""

    scene: ClassVar[Scene]
    driver_actions: ClassVar[Mapping[type[Action], frozenset[type]]]

    def test_lists_the_scene_surface(self, conformance_scene: Scene, conformance_driver_factory: DriverFactory):
        asyncio.run(check_surface(conformance_driver_factory, conformance_scene))

    def test_declared_actions_match_the_driver(self, conformance_scene, conformance_driver_factory):
        asyncio.run(check_declared_actions(conformance_driver_factory, conformance_scene, self.driver_actions))

    def test_happy_path(self, conformance_scene, conformance_driver_factory, conformance_pair):
        command, ref_kind = conformance_pair
        asyncio.run(check_happy_path(conformance_driver_factory, conformance_scene, command, ref_kind))

    def test_stale_ref_is_raised_before_dispatch(self, conformance_scene, conformance_driver_factory):
        asyncio.run(check_stale_ref(conformance_driver_factory, conformance_scene))

    def test_context_change_is_reported(
        self, conformance_scene, conformance_driver_factory, conformance_context_change
    ):
        asyncio.run(check_context_change(conformance_driver_factory, conformance_scene, conformance_context_change))

    def test_intercepted_click_names_the_receiver(self, conformance_scene, conformance_driver_factory):
        asyncio.run(check_intercepted(conformance_driver_factory, conformance_scene))

    def test_timeout_counts_as_dispatched(self, conformance_scene, conformance_driver_factory):
        asyncio.run(check_timeout(conformance_driver_factory, conformance_scene))

    def test_unhandled_command_is_unsupported(self, conformance_scene, conformance_driver_factory):
        asyncio.run(check_unhandled(conformance_driver_factory, conformance_scene))

    def test_observing_twice_gives_the_same_fingerprint(self, conformance_scene, conformance_driver_factory):
        asyncio.run(check_fingerprint(conformance_driver_factory, conformance_scene))

    def test_sensitive_text_stays_out_of_the_trace(
        self, conformance_scene, conformance_driver_factory, conformance_sensitive_case
    ):
        asyncio.run(
            check_sensitive_not_traced(conformance_driver_factory, conformance_scene, conformance_sensitive_case)
        )

    def test_sensitive_text_stays_out_of_the_logs(
        self, conformance_scene, conformance_driver_factory, conformance_sensitive_case
    ):
        asyncio.run(
            check_sensitive_not_logged(conformance_driver_factory, conformance_scene, conformance_sensitive_case)
        )


__all__ = [
    "LIVE_MARKER",
    "SECRET_TEXT",
    "TYPE_TEXT_CASE",
    "DriverConformance",
    "DriverFactory",
    "TraceSink",
    "UnhandledCommand",
    "check_context_change",
    "check_declared_actions",
    "check_fingerprint",
    "check_happy_path",
    "check_intercepted",
    "check_sensitive_not_logged",
    "check_sensitive_not_traced",
    "check_stale_ref",
    "check_surface",
    "check_timeout",
    "check_unhandled",
]
