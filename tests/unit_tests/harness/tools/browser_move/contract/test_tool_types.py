# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Native tools, tool specs with decoders, the ref resolver, the renderer protocol and contextual policy rules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    Click,
    ContentBlock,
    ContextRef,
    DriverError,
    DriverInfo,
    DriverRef,
    HandlerDriver,
    Health,
    NativeTool,
    Observation,
    Observe,
    PolicyRule,
    Ref,
    RefResolver,
    Renderer,
    Screenshot,
    SurfaceContext,
    SurfaceProfile,
    ToolSpec,
    handles,
)

CTX = ContextRef("c1", "window", "Home")


class _Resolver:
    contexts = (CTX,)
    current_context = CTX

    def resolve(self, model_id: str, generation: str) -> Ref:
        if model_id != "t7":
            raise ValueError(f"unknown id {model_id}")
        return DriverRef("e7", generation)

    def generation_of(self, ref: str) -> str:
        return "g2" if ref == "e1" else "g3"


def _click_by_id(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Click(resolver.resolve(args["id"], args["generation"]))


def _click_by_ref(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Click(DriverRef(args["ref"], resolver.generation_of(args["ref"])))


class _Renderer:
    def observation(self, observation: Observation) -> tuple[ContentBlock, ...]:
        return (observation.native_text or "",)

    def result(self, action: Action, result: ActionResult) -> tuple[ContentBlock, ...]:
        return (result.detail or "",)

    def error(self, action: Action, error: DriverError, observation: Observation | None) -> tuple[ContentBlock, ...]:
        return (f"### Error\n{error}", Screenshot(b"png", 1, 1))


class _NativeDriver(HandlerDriver):
    def describe(self) -> DriverInfo:
        return DriverInfo(
            name="native",
            surfaces=frozenset({"toy"}),
            native_tools={"toy_click": NativeTool("Click it.", {"type": "object"}, _click_by_ref)},
            tool_name_prefix="native_",
        )

    async def start(self, config: Any = None) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> Health:
        return Health(ok=True)

    async def observe(self, command: Observe) -> Observation:
        return Observation(context=CTX)

    @handles(Click, refs={DriverRef})
    async def _click(self, action: Click) -> ActionResult:
        return ActionResult(ok=True)


def test_driver_info_has_no_native_tools_and_no_prefix_by_default() -> None:
    first = DriverInfo(name="a", surfaces=frozenset({"x"}))
    second = DriverInfo(name="b", surfaces=frozenset({"x"}))
    assert first.native_tools == {} and first.tool_name_prefix == ""
    assert first.native_tools is not second.native_tools


def test_handler_driver_info_keeps_native_tools_and_prefix() -> None:
    info = _NativeDriver().info
    assert info.tool_name_prefix == "native_"
    assert info.native_tools["toy_click"].description == "Click it."
    assert info.actions == {Click: frozenset({DriverRef})}
    hash(info)


def test_native_tool_decodes_arguments_with_the_resolver() -> None:
    tool = _NativeDriver().info.native_tools["toy_click"]
    assert tool.build({"ref": "e1"}, _Resolver()) == Click(DriverRef("e1", "g2"))
    assert tool.build({"ref": "e9"}, _Resolver()) == Click(DriverRef("e9", "g3"))


def test_native_tool_has_no_decoder_by_default() -> None:
    assert NativeTool("Find it.", {"type": "object"}).build is None


def test_tool_spec_has_no_schema_and_no_decoder_by_default() -> None:
    spec = ToolSpec("toy_click", (Click,))
    assert spec.schema is None and spec.build is None


def test_tool_spec_with_schema_stays_hashable_and_decodes() -> None:
    spec = ToolSpec("toy_click", (Click,), schema={"type": "object"}, build=_click_by_id)
    hash(spec)
    assert spec.build({"id": "t7", "generation": "g4"}, _Resolver()) == Click(DriverRef("e7", "g4"))


def test_resolver_exposes_contexts_and_the_current_one() -> None:
    resolver: RefResolver = _Resolver()
    assert resolver.contexts == (CTX,) and resolver.current_context == CTX


def test_surface_profile_takes_a_renderer_with_three_methods() -> None:
    renderer: Renderer = _Renderer()
    profile = SurfaceProfile(name="toy", commands=(), renderer=renderer)
    blocks = profile.renderer.error(Click(DriverRef("e1", "g1")), DriverError("boom"), None)
    assert blocks[0] == "### Error\nboom"
    assert isinstance(blocks[1], Screenshot)
    assert profile.renderer.observation(Observation(context=CTX, native_text="tree")) == ("tree",)
    assert profile.renderer.result(Observe(), ActionResult(ok=True, detail="done")) == ("done",)


def test_policy_rule_check_gets_the_action_and_the_current_context() -> None:
    seen: list[tuple[Action, SurfaceContext | None]] = []

    def deny_without_context(action: Action, context: SurfaceContext | None) -> str | None:
        seen.append((action, context))
        return None if context is not None else "no context yet"

    rule = PolicyRule(Click, deny_without_context)
    click = Click(DriverRef("e1", "g1"))
    assert rule.check(click, None) == "no context yet"
    assert rule.check(click, SurfaceContext()) is None
    assert seen == [(click, None), (click, SurfaceContext())]


def test_surface_profile_has_no_capability_tools_by_default() -> None:
    first = SurfaceProfile(name="toy", commands=(), renderer=_Renderer())
    second = SurfaceProfile(name="toy", commands=(), renderer=_Renderer())
    assert first.capability_tools == {} and first.capability_tools is not second.capability_tools
