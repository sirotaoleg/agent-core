# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ToolFactory`` rules and the ``CommandTool`` pipeline on ``FakeDriver``."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    Click,
    ConnectionLost,
    ContextRef,
    DriverInfo,
    DriverRef,
    Effect,
    Element,
    NativeTool,
    Observation,
    Observe,
    PointRef,
    PolicyRule,
    RefResolver,
    RunScript,
    Screenshot,
    Scroll,
    StaleRef,
    SurfaceProfile,
    SwitchContext,
    ToolSpec,
    TypeText,
    Unsupported,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.contract.testing.fake import DEFAULT_ACTIONS
from openjiuwen.harness.tools.browser_move.drivers.middleware import MASK, Tracing
from openjiuwen.harness.tools.browser_move.playwright_runtime import tool_factory
from openjiuwen.harness.tools.browser_move.playwright_runtime.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.driver_session import DriverSession
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import (
    AgentTools,
    CommandRecord,
    ToolContext,
    ToolFactory,
    command_record,
)
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

MESSAGES = {"driver_restarted": "restarted; observe again", "driver_lost": "the device is gone"}
DEVICE = ProvisionedDevice(kind="fake-device", endpoint="fake://1", extras={})
HOME = ContextRef("0", "tab", "Home")
SAVE = Element(ref=DriverRef("e1", ""), role="button", name="Save")
SHOT = Screenshot(b"\x89PNG-fake", 2, 2)
SURFACE = BrowserContext(
    url="https://a.test/", title="Home", tabs=(HOME,), tab_urls=("https://a.test/",), current_tab=0
)
REF_SCHEMA = {"type": "object", "properties": {"ref": {"type": "string"}}}


def _observation(text: str = '- button "Save" [ref=e1]', **fields: Any) -> Observation:
    return Observation(context=HOME, elements=(SAVE,), native_text=text, surface=SURFACE, **fields)


def _click(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    ref = str(args["ref"])
    return Click(DriverRef(ref, resolver.generation_of(ref)))


def _look(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe(include_screenshot=bool(args.get("shot")))


def _tabs(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    if args.get("action") == "list":
        return Observe()
    index = int(args["index"])
    if not 0 <= index < len(resolver.contexts):
        raise ValueError(f"no tab {index}")
    return SwitchContext(resolver.contexts[index])


def _script(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return RunScript(str(args["code"]))


def _shot(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe(include_screenshot=True)


SPECS = (
    ToolSpec("toy_click", (Click,), "Click an element.", schema=REF_SCHEMA, build=_click, prompt_section="Click well."),
    ToolSpec("toy_look", (Observe,), "Look at the screen.", schema={"type": "object"}, build=_look),
    ToolSpec("toy_tabs", (Observe, SwitchContext), "List or switch.", schema={"type": "object"}, build=_tabs),
    ToolSpec("toy_script", (RunScript,), "Run a script.", schema={"type": "object"}, build=_script),
    ToolSpec("toy_shot", (Observe,), "Screenshot.", schema={"type": "object"}, build=_shot),
    ToolSpec("toy_point", (Click,)),
    ToolSpec("toy_wheel", (Scroll,)),
)
TOOLS = {
    "core": ("toy_click", "toy_look", "toy_tabs", "toy_script"),
    "vision": ("toy_point", "toy_shot", "toy_wheel"),
    "nothing": (),
    "served_elsewhere": ("toy_find",),
}
CATALOG = {
    "core": frozenset({(Click, DriverRef), (Observe, None), (SwitchContext, None), (RunScript, None)}),
    "vision": frozenset({(Click, PointRef), (Observe, None), (Scroll, None)}),
}


def _profile(**fields: Any) -> SurfaceProfile:
    values: dict[str, Any] = {
        "name": "toy",
        "commands": (),
        "renderer": BrowserRenderer(),
        "tool_specs": SPECS,
        "capability_catalog": CATALOG,
        "capability_tools": TOOLS,
    }
    values.update(fields)
    return SurfaceProfile(**values)


def _info(**fields: Any) -> DriverInfo:
    values: dict[str, Any] = {"name": "fake", "surfaces": frozenset({"toy"}), "actions": DEFAULT_ACTIONS}
    values.update(fields)
    return DriverInfo(**values)


class _LiveDevice:
    def is_alive(self, device: ProvisionedDevice) -> bool:
        return True


def _build(
    driver: FakeDriver,
    capabilities: tuple[str, ...] = ("core",),
    *,
    profile: SurfaceProfile | None = None,
    image_support: bool = False,
    page_state: BrowserPageState | None = None,
) -> tuple[AgentTools, BrowserPageState]:
    page_state = page_state or BrowserPageState(page_id="page-test")
    session = DriverSession(DriverAssembly(driver, DEVICE, _LiveDevice(), None), page_state, MESSAGES)
    built = ToolFactory(profile or _profile(), driver.info).build(
        capabilities, image_support=image_support, session=session, page_state=page_state
    )
    return built, page_state


def _tool(built: AgentTools, name: str) -> Any:
    return next(tool for tool in built.tools if tool.card.name == name)


def _names(built: AgentTools) -> list[str]:
    return [tool.card.name for tool in built.tools]


# --- selection and the four rules ---------------------------------------------------------------------------------


def test_catalog_entries_select_their_specs_once_in_catalog_order() -> None:
    built, _ = _build(FakeDriver(), ("core", "core"))
    assert _names(built) == ["toy_click", "toy_look", "toy_tabs", "toy_script"]
    assert built.prompt_sections == ("Click well.",)


def test_a_spec_whose_command_the_driver_lacks_is_skipped() -> None:
    info = _info(actions={command: refs for command, refs in DEFAULT_ACTIONS.items() if command is not RunScript})
    built, _ = _build(FakeDriver(info=info))
    assert "toy_script" not in _names(built)


def test_a_spec_whose_ref_kinds_the_driver_does_not_accept_is_skipped() -> None:
    info = _info(actions={**DEFAULT_ACTIONS, Click: frozenset({DriverRef})})
    built, _ = _build(FakeDriver(info=info), ("vision",), image_support=True)
    assert _names(built) == ["toy_shot"]


def test_skipped_tools_and_empty_entries_are_logged_once_per_factory() -> None:
    info = _info(actions={command: refs for command, refs in DEFAULT_ACTIONS.items() if command is not RunScript})
    driver = FakeDriver(info=info)
    page_state = BrowserPageState()
    session = DriverSession(DriverAssembly(driver, DEVICE, None, None), page_state, MESSAGES)
    factory = ToolFactory(_profile(), driver.info)
    with patch.object(tool_factory, "tool_logger", MagicMock()) as log:
        for _ in range(2):
            factory.build(
                ("core", "nothing", "served_elsewhere"), image_support=False, session=session, page_state=page_state
            )
    skipped = sorted(call.args[1] for call in log.info.call_args_list)
    assert skipped == ["nothing", "toy_find", "toy_script"]


def test_a_native_tool_wins_over_the_profile_form_and_carries_the_driver_prefix() -> None:
    native = NativeTool("Server click.", {"type": "object", "properties": {"target": {"type": "string"}}}, _click)
    info = _info(native_tools={"toy_click": native}, tool_name_prefix="srv_")
    built, _ = _build(FakeDriver(info=info))
    card = _tool(built, "srv_toy_click").card
    assert (card.description, card.input_params) == ("Server click.", native.schema)
    assert _tool(built, "srv_toy_look").card.description == "Look at the screen."


def test_description_falls_back_to_the_tool_hint_then_the_command_docstring() -> None:
    specs = (ToolSpec("toy_click", (Click,), schema=REF_SCHEMA, build=_click), ToolSpec("toy_run", (RunScript,)))
    profile = _profile(
        tool_specs=specs,
        capability_tools={"core": ("toy_click", "toy_run")},
        capability_catalog={"core": frozenset({(Click, DriverRef), (RunScript, DriverRef)})},
    )
    built, _ = _build(FakeDriver(info=_info(tool_hints={Click: "Hinted click."})), profile=profile)
    assert _tool(built, "toy_click").card.description == "Hinted click."
    assert _tool(built, "toy_run").card.description == RunScript.__doc__


def test_a_spec_without_schema_gets_one_generated_from_its_ref_kinds() -> None:
    built, _ = _build(FakeDriver(), ("vision",), image_support=True)
    point = _tool(built, "toy_point")
    coordinates = {"x": {"type": "integer"}, "y": {"type": "integer"}}
    assert point.card.input_params == {"type": "object", "properties": coordinates}
    assert built.builders["toy_point"]({"x": 3, "y": 4}, BrowserPageState()) == Click(PointRef(3, 4, ""))


def test_a_spec_without_schema_and_without_ref_kinds_is_skipped() -> None:
    built, _ = _build(FakeDriver(), ("vision",), image_support=True)
    assert "toy_wheel" not in _names(built)


def test_a_screenshot_tool_is_offered_only_with_image_support() -> None:
    assert "toy_shot" not in _names(_build(FakeDriver(), ("vision",))[0])
    assert "toy_shot" in _names(_build(FakeDriver(), ("vision",), image_support=True)[0])
    assert "toy_look" in _names(_build(FakeDriver())[0])


def test_extra_tools_are_built_from_the_tool_context() -> None:
    seen: list[ToolContext] = []

    def extra(context: ToolContext) -> Any:
        seen.append(context)
        return _tool(_build(FakeDriver())[0], "toy_look")

    built, page_state = _build(FakeDriver(), profile=_profile(extra_tools=(extra,), settings="toy-settings"))
    assert len(built.tools) == 5
    assert (seen[0].page_state, seen[0].settings, seen[0].image_support) == (page_state, "toy-settings", False)
    assert isinstance(seen[0].driver, DriverSession)


def test_extra_tools_get_the_workspace_and_language_the_agent_is_built_with(tmp_path: Path) -> None:
    seen: list[ToolContext] = []

    def extra(context: ToolContext) -> Any:
        seen.append(context)
        return _tool(_build(FakeDriver())[0], "toy_look")

    driver, page_state = FakeDriver(), BrowserPageState()
    session = DriverSession(DriverAssembly(driver, DEVICE, None, None), page_state, MESSAGES)
    ToolFactory(_profile(extra_tools=(extra,)), driver.info).build(
        ("core",), image_support=False, session=session, page_state=page_state, workspace=tmp_path, language="en"
    )
    assert (seen[0].workspace, seen[0].language) == (tmp_path, "en")


def test_builders_map_model_facing_names_to_decoders_for_the_policy_rail() -> None:
    info = _info(tool_name_prefix="srv_")
    built, page_state = _build(FakeDriver(info=info))
    assert set(built.builders) == {"srv_toy_click", "srv_toy_look", "srv_toy_tabs", "srv_toy_script"}
    assert isinstance(built.builders, MappingProxyType)
    assert built.builders["srv_toy_click"] is _click


@pytest.mark.asyncio
async def test_policy_rail_denies_with_the_factory_builders() -> None:
    built, page_state = _build(FakeDriver())
    rule = PolicyRule(RunScript, lambda action, context: "scripts are off")
    policy = ActionPolicy(built.builders, (rule,), page_state)
    tool_call = ToolCall(id="call-1", type="function", name="toy_script", arguments='{"code": "1"}')
    inputs = ToolCallInputs(tool_call=tool_call, tool_name="toy_script", tool_args={"code": "1"})
    ctx = AgentCallbackContext(agent=MagicMock(), inputs=inputs, extra={})

    await policy.before_tool_call(ctx)

    assert isinstance(inputs.tool_msg, ToolMessage)
    assert inputs.tool_msg.content == "### Error\nscripts are off"
    assert ctx.extra["_skip_tool_calls"] == {"call-1": True}


# --- the CommandTool pipeline -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_call_decodes_executes_records_and_renders_in_order() -> None:
    driver = FakeDriver(results=[ActionResult(ok=True, detail="### Page\n- clicked")])
    built, page_state = _build(driver)
    page_state.record(Observe(), _observation())

    output = await _tool(built, "toy_click").invoke({"ref": "e1"})

    assert driver.kinds() == ["execute"]
    assert driver.calls[0].payload == Click(DriverRef("e1", "g0"))
    assert output.success is True
    assert output.data["content"] == "### Page\n- clicked"
    record = output.data["command"]
    assert record == CommandRecord(
        command=Click,
        effect=Effect.ACT,
        ok=True,
        dispatched=True,
        generation="g0",
        action=Click(DriverRef("e1", "g0")),
    )


@pytest.mark.asyncio
async def test_a_stale_ref_never_reaches_the_driver_and_answers_with_one_fresh_observation() -> None:
    driver = FakeDriver(observations=[_observation('- button "Saved" [ref=e2]')])
    built, page_state = _build(driver)
    page_state.record(Observe(), _observation())
    page_state.reset()

    output = await _tool(built, "toy_click").invoke({"ref": "e1"})

    assert driver.kinds() == ["observe"]
    assert output.success is False
    assert output.error == (
        '### Error\nStale AX ref e1 belongs to g0; current generation is g1.\n\n- button "Saved" [ref=e2]'
    )
    record = output.data["command"]
    assert (record.error, record.dispatched, record.generation) == ("StaleRef", False, "g1")
    assert page_state.last_observation is not None and page_state.last_observation.generation == "g1"


@pytest.mark.asyncio
async def test_a_stale_ref_the_driver_found_answers_with_the_observation_it_carries_and_records_it() -> None:
    fresh = _observation('- button "Saved" [ref=e2]')
    seen: list[Any] = []

    class Spy(BrowserRenderer):
        def error(self, action: Action, error: Any, observation: Observation | None) -> Any:
            seen.append(observation)
            return super().error(action, error, observation)

    driver = FakeDriver()
    driver.fail_next(StaleRef("e1 is gone", observation=fresh))
    built, page_state = _build(driver, profile=_profile(renderer=Spy()))

    output = await _tool(built, "toy_click").invoke({"ref": "e1"})

    recorded = page_state.last_observation
    assert output.error == '### Error\ne1 is gone\n\n- button "Saved" [ref=e2]'
    assert seen == [recorded]
    assert (recorded.native_text, recorded.generation) == (fresh.native_text, "g0")
    assert output.data["command"].error == "StaleRef"


@pytest.mark.asyncio
async def test_a_point_on_a_screenshot_of_an_older_generation_never_reaches_the_driver() -> None:
    driver = FakeDriver(observations=[_observation(screenshot=SHOT), _observation()])
    built, page_state = _build(driver, ("vision",), image_support=True)
    await _tool(built, "toy_shot").invoke({})
    page_state.reset()

    output = await _tool(built, "toy_point").invoke({"x": 3, "y": 4})

    assert driver.kinds() == ["observe", "observe"]
    assert output.error.startswith("### Error\nStale PointRef belongs to g0; current generation is g1.")


@pytest.mark.asyncio
async def test_a_dispatched_failure_outdates_the_last_observation() -> None:
    driver = FakeDriver(observations=[_observation()])
    built, page_state = _build(driver)
    page_state.record(Observe(), await driver.observe(Observe()))
    driver.fail_next(ActionTimeout("slow", dispatched=True))

    await _tool(built, "toy_click").invoke({"ref": "e1"})

    assert page_state.last_observation is not None and page_state.fresh_observation is None


@pytest.mark.asyncio
async def test_the_free_observation_of_a_result_is_recorded_but_not_rendered() -> None:
    free = _observation('- heading "Next" [ref=e9]')
    driver = FakeDriver(results=[ActionResult(ok=True, detail="done", context_changed=True, observation=free)])
    built, page_state = _build(driver)
    page_state.record(Observe(), _observation())

    output = await _tool(built, "toy_click").invoke({"ref": "e1"})

    assert output.data["content"] == "done"
    assert page_state.free_observations == 1
    assert page_state.last_observation.native_text == free.native_text
    assert output.data["command"].generation == page_state.last_observation.generation == "g1"


@pytest.mark.asyncio
async def test_a_single_observe_tool_renders_the_observation() -> None:
    driver = FakeDriver(observations=[_observation()])
    built, page_state = _build(driver)

    output = await _tool(built, "toy_look").invoke({})

    assert driver.kinds() == ["observe"]
    assert output.data["content"] == '- button "Save" [ref=e1]'
    assert page_state.generation_of("e1") == "g0"
    assert output.data["command"].effect is Effect.READ


@pytest.mark.asyncio
async def test_a_screenshot_only_tool_renders_the_image_only() -> None:
    built, _ = _build(FakeDriver(observations=[_observation(screenshot=SHOT)]), ("vision",), image_support=True)

    output = await _tool(built, "toy_shot").invoke({})

    assert output.data["content"] == "1 image(s) attached as multimodal input."
    assert len(output.data["multimodal"]) == 1


@pytest.mark.asyncio
async def test_an_observe_record_keeps_the_observed_elements_as_its_value() -> None:
    built, _ = _build(FakeDriver(observations=[_observation()]))

    output = await _tool(built, "toy_look").invoke({})

    assert output.data["command"].value == (Element(ref=DriverRef("e1", "g0"), role="button", name="Save"),)


@pytest.mark.asyncio
async def test_an_observe_inside_a_multi_command_tool_renders_the_result() -> None:
    built, _ = _build(FakeDriver(observations=[_observation()]))

    output = await _tool(built, "toy_tabs").invoke({"action": "list"})

    assert output.data["content"] == "### Result\n- 0: (current) [Home](https://a.test/)"


@pytest.mark.asyncio
async def test_image_blocks_go_to_multimodal_only_with_image_support() -> None:
    with_images, _ = _build(FakeDriver(observations=[_observation(screenshot=SHOT)]), image_support=True)
    without, _ = _build(FakeDriver(observations=[_observation(screenshot=SHOT)]))

    shown = await _tool(with_images, "toy_look").invoke({"shot": True})
    hidden = await _tool(without, "toy_look").invoke({"shot": True})

    encoded = base64.b64encode(SHOT.png).decode("ascii")
    assert shown.data["content"] == '- button "Save" [ref=e1]\n\n1 image(s) attached as multimodal input.'
    assert shown.data["multimodal"] == [
        {
            "type": "image",
            "source": "toy",
            "source_path": "toy_look",
            "mime_type": "image/png",
            "data_url": f"data:image/png;base64,{encoded}",
        }
    ]
    assert "multimodal" not in hidden.data
    assert hidden.data["content"] == '- button "Save" [ref=e1]'


@pytest.mark.asyncio
async def test_a_driver_error_is_a_failure_with_the_rendered_error() -> None:
    driver = FakeDriver()
    driver.fail_next(ActionIntercepted("covered", by="div#overlay"))
    built, page_state = _build(driver)
    page_state.record(Observe(), _observation())

    output = await _tool(built, "toy_click").invoke({"ref": "e1"})

    assert (output.success, output.error) == (False, "### Error\ncovered")
    assert output.data["content"] == output.error
    record = output.data["command"]
    assert (record.error, record.dispatched, record.surface) == ("ActionIntercepted", False, SURFACE)
    assert (record.blocked_by, record.action) == ("div#overlay", Click(DriverRef("e1", "g0")))


@pytest.mark.asyncio
async def test_a_refused_result_is_a_failure() -> None:
    built, _ = _build(FakeDriver(results=[ActionResult(ok=False, detail="a dialog is open")]))

    output = await _tool(built, "toy_script").invoke({"code": "1"})

    assert (output.success, output.error) == (False, "### Error\na dialog is open")


@pytest.mark.asyncio
async def test_arguments_that_do_not_decode_send_nothing() -> None:
    driver = FakeDriver()
    built, _ = _build(driver)

    output = await _tool(built, "toy_tabs").invoke({"action": "select", "index": 3})

    assert driver.calls == []
    assert (output.success, output.error) == (False, "### Error\nno tab 3")


@pytest.mark.asyncio
async def test_a_restarted_driver_answers_with_the_restart_message() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    built, _ = _build(driver)

    output = await _tool(built, "toy_script").invoke({"code": "1"})

    assert output.error == "### Error\nrestarted; observe again"
    assert output.data["command"].error == ConnectionLost.__name__


@pytest.mark.asyncio
async def test_a_second_lost_connection_fails_the_task() -> None:
    driver = FakeDriver()
    driver.lose_connection_on(1)
    built, _ = _build(driver)
    tool = _tool(built, "toy_script")
    await tool.invoke({"code": "1"})
    driver.lose_connection_on(2)

    with pytest.raises(BaseError):
        await tool.invoke({"code": "1"})


def _type(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return TypeText(None, str(args["text"]), sensitive=True)


@pytest.mark.asyncio
async def test_the_record_and_the_trace_mask_sensitive_fields_with_one_helper() -> None:
    traced: list[dict[str, Any]] = []
    driver = Tracing(FakeDriver(results=[ActionResult(ok=True)]), traced.append)
    spec = ToolSpec("toy_type", (TypeText,), "Type.", schema={"type": "object"}, build=_type)
    profile = _profile(tool_specs=(*SPECS, spec), capability_tools={"core": ("toy_type",)})
    page_state = BrowserPageState(page_id="page-test")
    session = DriverSession(DriverAssembly(driver, DEVICE, None, None), page_state, MESSAGES)
    built = ToolFactory(profile, driver.info).build(
        ("core",), image_support=False, session=session, page_state=page_state
    )

    output = await _tool(built, "toy_type").invoke({"text": "hunter2"})

    record = output.data["command"]
    assert record.action == TypeText(None, MASK, sensitive=True)
    assert traced[0]["args"]["text"] == MASK
    assert "hunter2" not in repr(record) + repr(traced)


@pytest.mark.asyncio
async def test_command_record_reads_the_record_and_is_none_for_a_call_that_sent_nothing() -> None:
    built, _ = _build(FakeDriver(results=[ActionResult(ok=True)]))
    tool = _tool(built, "toy_script")

    sent = await tool.invoke({"code": "1"})
    undecoded = await _tool(built, "toy_tabs").invoke({"action": "select", "index": 3})

    assert command_record(sent) is sent.data["command"]
    assert command_record(undecoded) is None


# --- the message metadata the context processors read -------------------------------------------------------------


def _record(**fields: Any) -> CommandRecord:
    values: dict[str, Any] = {"command": Click, "effect": Effect.ACT, "ok": True, "dispatched": True}
    values.update(fields)
    return CommandRecord(**values)


@pytest.mark.parametrize(
    ("record", "metadata"),
    [
        (_record(), (True, True, True)),
        (_record(command=Observe, effect=Effect.READ), (True, True, False)),
        (_record(command=Observe, effect=Effect.READ, dispatched=False), (True, True, False)),
        (_record(command=RunScript, effect=Effect.READ, ok=False, error="DriverError"), (False, False, False)),
        (_record(effect=Effect.NAVIGATE, context_changed=True), (True, True, True)),
        (_record(ok=False, error=ActionTimeout.__name__), (False, True, True)),
        (_record(ok=False, dispatched=True, error="DriverError"), (False, True, False)),
        (_record(ok=False, dispatched=False, error=ActionIntercepted.__name__), (False, False, False)),
        (_record(ok=False, dispatched=False, error="StaleRef"), (False, False, False)),
        (_record(ok=False, dispatched=False, error=Unsupported.__name__), (False, False, False)),
        (_record(ok=False, context_changed=True), (False, True, True)),
    ],
)
def test_message_metadata_keeps_the_keys_the_context_processors_read(
    record: CommandRecord, metadata: tuple[bool, bool, bool]
) -> None:
    success, executed, state_changed = metadata
    assert record.message_metadata == {
        "success": success,
        "executed": executed,
        "state_changed": state_changed,
        "denied": False,
    }
