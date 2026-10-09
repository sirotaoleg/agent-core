# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``HandlerDriver`` dispatches by command type, derives ``info.actions`` from ``@handles`` and refuses the rest."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    Click,
    ContextRef,
    Driver,
    DriverInfo,
    DriverRef,
    Effect,
    HandlerDriver,
    Health,
    Hover,
    IndexRef,
    Locator,
    Observation,
    Observe,
    PointRef,
    PressKey,
    TypeText,
    Unsupported,
    handles,
    iter_refs,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import FieldKind, FillForm, FormField, Navigate

E1 = DriverRef("e1", "g1")
P1 = PointRef(10, 20, "g1")


@dataclass(frozen=True)
class Poke(Action):
    """A command defined outside ``contract/`` (like a surface-owned one)."""

    effect: ClassVar[Effect] = Effect.ACT

    target: PointRef


class _Base(HandlerDriver):
    def __init__(self) -> None:
        self.handled: list[tuple[str, Action]] = []

    def describe(self) -> DriverInfo:
        return DriverInfo(
            name="toy", surfaces=frozenset({"toy"}), versions={"toy": "1"}, observes=frozenset({"elements"})
        )

    async def start(self, config: Any = None) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health(self) -> Health:
        return Health(ok=True)

    async def observe(self, command: Observe) -> Observation:
        return Observation(context=ContextRef("c1", "window"))

    def _done(self, name: str, action: Action) -> ActionResult:
        self.handled.append((name, action))
        return ActionResult(ok=True, value=name)


class ToyDriver(_Base):
    @handles(Click, refs={DriverRef, PointRef})
    async def click(self, action: Click) -> ActionResult:
        return self._done("click", action)

    @handles(Poke, refs={PointRef})
    async def poke(self, action: Poke) -> ActionResult:
        return self._done("poke", action)

    @handles(Navigate)
    async def navigate(self, action: Navigate) -> ActionResult:
        return self._done("navigate", action)

    @handles(PressKey)
    @handles(Hover, refs={DriverRef})
    async def key_or_hover(self, action: Action) -> ActionResult:
        return self._done("key_or_hover", action)

    @handles(FillForm, refs={DriverRef})
    async def fill(self, action: FillForm) -> ActionResult:
        return self._done("fill", action)


def _run(coro):
    return asyncio.run(coro)


def test_handler_driver_is_a_driver() -> None:
    assert isinstance(ToyDriver(), Driver)


def test_info_actions_are_derived_from_the_marks() -> None:
    info = ToyDriver().info

    assert dict(info.actions) == {
        Click: frozenset({DriverRef, PointRef}),
        Poke: frozenset({PointRef}),
        Navigate: frozenset(),
        PressKey: frozenset(),
        Hover: frozenset({DriverRef}),
        FillForm: frozenset({DriverRef}),
    }
    assert (info.name, info.surfaces, dict(info.versions)) == ("toy", frozenset({"toy"}), {"toy": "1"})
    assert info.observes == frozenset({"elements"})


def test_info_actions_set_in_describe_are_replaced_by_the_marks() -> None:
    class Liar(ToyDriver):
        def describe(self) -> DriverInfo:
            return DriverInfo(name="liar", surfaces=frozenset({"toy"}), actions={TypeText: frozenset({DriverRef})})

    assert TypeText not in Liar().info.actions
    assert Click in Liar().info.actions


@pytest.mark.parametrize(
    ("action", "handler"),
    [
        (Click(E1), "click"),
        (Click(P1), "click"),
        (Poke(P1), "poke"),
        (Navigate("https://example.test/"), "navigate"),
        (PressKey("Enter"), "key_or_hover"),
        (Hover(E1), "key_or_hover"),
        (FillForm((FormField(E1, FieldKind.TEXTBOX, "x"),)), "fill"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_execute_dispatches_by_command_type(action: Action, handler: str) -> None:
    driver = ToyDriver()

    result = _run(driver.execute(action))

    assert result == ActionResult(ok=True, value=handler)
    assert driver.handled == [(handler, action)]


def test_unhandled_command_is_unsupported_and_not_dispatched() -> None:
    driver = ToyDriver()

    with pytest.raises(Unsupported, match="ToyDriver does not handle TypeText") as raised:
        _run(driver.execute(TypeText(E1, "secret")))

    assert raised.value.dispatched is False
    assert driver.handled == []
    assert TypeText not in driver.info.actions


@pytest.mark.parametrize(
    "action",
    [
        Click(IndexRef(1, "g1")),
        Click(Locator("#go", None, "g1")),
        Poke(E1),
        Hover(P1),
        FillForm((FormField(E1, FieldKind.TEXTBOX, "x"), FormField(P1, FieldKind.CHECKBOX, "true"))),
    ],
    ids=["click-index", "click-locator", "poke-driver-ref", "hover-point", "nested-form-field"],
)
def test_ref_kind_the_handler_does_not_accept_is_unsupported(action: Action) -> None:
    driver = ToyDriver()

    with pytest.raises(Unsupported, match="does not accept") as raised:
        _run(driver.execute(action))

    assert raised.value.dispatched is False
    assert driver.handled == []


def test_subclass_keeps_inherited_handlers_and_may_override_them() -> None:
    class Child(ToyDriver):
        @handles(Click, refs={DriverRef})
        async def click_by_ref(self, action: Click) -> ActionResult:
            return self._done("child-click", action)

    driver = Child()

    assert driver.info.actions[Click] == frozenset({DriverRef})
    assert Poke in driver.info.actions
    assert _run(driver.execute(Click(E1))).value == "child-click"
    assert ToyDriver().info.actions[Click] == frozenset({DriverRef, PointRef}), "the parent is unchanged"


def test_handles_rejects_kinds_that_are_not_element_refs() -> None:
    with pytest.raises(TypeError, match="ContextRef"):
        handles(Click, refs={ContextRef})


def test_iter_refs_finds_nested_element_refs_only() -> None:
    form = FillForm((FormField(E1, FieldKind.TEXTBOX, "x"), FormField(P1, FieldKind.RADIO, "y")))

    assert list(iter_refs(form)) == [E1, P1]
    assert list(iter_refs(TypeText(None, "x"))) == []
    assert list(iter_refs(Navigate("https://example.test/"))) == []


def test_tool_hints_default_is_not_shared_between_instances() -> None:
    first = DriverInfo(name="a", surfaces=frozenset({"x"}))
    second = DriverInfo(name="b", surfaces=frozenset({"x"}))

    assert first.tool_hints == {} and first.tool_hints is not second.tool_hints
    assert first.versions is not second.versions
    assert first.actions is not second.actions
