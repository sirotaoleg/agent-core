# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract data types are frozen, have the documented defaults, and the fingerprint is stable."""

from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ActionResult,
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverRef,
    Effect,
    Element,
    HandleDialog,
    Hover,
    IndexRef,
    Locator,
    Observation,
    Observe,
    PointRef,
    PressKey,
    RunScript,
    Screenshot,
    Scroll,
    SurfaceContext,
    SwitchContext,
    TypeText,
    Viewport,
    Wait,
    iter_refs,
)
from openjiuwen.harness.tools.browser_move.contract import actions as contract_actions
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

CTX = ContextRef("c1", "window", "Home")
E1 = DriverRef("e1", "g1")


def _observation(**overrides) -> Observation:
    fields = {
        "context": CTX,
        "elements": (Element(E1, "button", "Go"), Element(DriverRef("e2", "g1"), "link", "More")),
    }
    fields.update(overrides)
    return Observation(**fields)


FROZEN_SAMPLES = [
    E1,
    PointRef(1, 2, "g1"),
    IndexRef(3, "g1"),
    Locator("#go", None, "g1"),
    CTX,
    Viewport(800, 600),
    Screenshot(b"png", 800, 600),
    Element(E1, "button", "Go"),
    _observation(),
    ActionResult(ok=True),
    Click(E1),
    Navigate("https://example.test/"),
    BrowserContext(url="https://example.test/"),
]


@pytest.mark.parametrize("sample", FROZEN_SAMPLES, ids=lambda s: type(s).__name__)
def test_contract_values_are_frozen(sample) -> None:
    name = dataclasses.fields(sample)[0].name

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(sample, name, None)


EFFECTS = {
    Observe: Effect.READ,
    Click: Effect.ACT,
    TypeText: Effect.ACT,
    PressKey: Effect.ACT,
    Scroll: Effect.ACT,
    Hover: Effect.ACT,
    Drag: Effect.ACT,
    Wait: Effect.READ,
    SwitchContext: Effect.NAVIGATE,
    CloseContext: Effect.NAVIGATE,
    HandleDialog: Effect.ACT,
    RunScript: Effect.SCRIPT,
    Navigate: Effect.NAVIGATE,
    Back: Effect.NAVIGATE,
    OpenContext: Effect.NAVIGATE,
    SelectOption: Effect.ACT,
    UploadFile: Effect.ACT,
    FillForm: Effect.ACT,
    Drop: Effect.ACT,
    GetPdf: Effect.READ,
    GetConsole: Effect.READ,
    GetNetwork: Effect.READ,
}


@pytest.mark.parametrize("command", list(EFFECTS), ids=lambda c: c.__name__)
def test_every_command_has_its_class_level_effect(command: type[Action]) -> None:
    assert issubclass(command, Action)
    assert command.effect is EFFECTS[command]
    assert "effect" not in {f.name for f in dataclasses.fields(command)}, "effect is not an instance field"


def test_shared_commands_are_exactly_the_approved_list() -> None:
    shared = {
        obj
        for obj in vars(contract_actions).values()
        if isinstance(obj, type) and issubclass(obj, Action) and obj is not Action
    }

    assert {c.__name__ for c in shared} == {
        "Observe",
        "Click",
        "TypeText",
        "PressKey",
        "Scroll",
        "Hover",
        "Drag",
        "Wait",
        "SwitchContext",
        "CloseContext",
        "HandleDialog",
        "RunScript",
    }, "GetText, Forward and Reload were removed (decision C14)"


def test_an_observe_is_scoped_by_a_depth_or_a_root_the_stale_check_sees() -> None:
    assert not Observe().scoped and not Observe(include_screenshot=True).scoped
    assert Observe(depth=1).scoped and Observe(root=E1).scoped
    assert list(iter_refs(Observe(root=E1))) == [E1]


def test_command_defaults() -> None:
    assert Observe() == Observe(include_screenshot=False, full_page=False, depth=None, root=None)
    assert Click(E1) == Click(E1, button="left", modifiers=(), count=1)
    typed = TypeText(None, "hi")
    assert (typed.submit, typed.sensitive, typed.clear, typed.slowly) == (False, False, True, False)
    assert PressKey("Enter").target is None
    assert Scroll() == Scroll(target=None, dx=0, dy=0)
    assert HandleDialog(True).prompt_text is None
    script = RunScript("() => 1")
    assert (script.args, script.sensitive, script.target) == (None, False, None)
    assert OpenContext().url is None
    assert UploadFile(None).paths == ()
    assert GetPdf().filename is None
    assert GetConsole() == GetConsole(level="info", all=False)
    assert GetNetwork() == GetNetwork(static=False, filter=None)


def test_wait_needs_seconds_or_text() -> None:
    with pytest.raises(ValueError, match="seconds, until_text or both"):
        Wait()


def test_wait_accepts_seconds_text_or_text_with_a_deadline() -> None:
    assert Wait(seconds=0.5).until_text is None
    assert Wait(until_text="done").seconds is None
    bounded = Wait(until_text="done", seconds=2.0)
    assert (bounded.until_text, bounded.seconds) == ("done", 2.0)


def test_drop_needs_paths_or_data() -> None:
    with pytest.raises(ValueError, match="paths or data"):
        Drop(E1)
    assert Drop(E1, paths=("/tmp/a.txt",)).data == ()
    assert Drop(E1, data=(("text/plain", "hello"),)).paths == ()


def test_fill_form_fields_use_todays_field_kinds() -> None:
    form = FillForm((FormField(E1, FieldKind.CHECKBOX, "true"),))

    assert {kind.value for kind in FieldKind} == {"textbox", "checkbox", "radio", "combobox", "slider"}
    assert form.fields[0].kind == "checkbox"


def test_form_field_is_not_sensitive_unless_marked() -> None:
    assert FormField(E1, FieldKind.TEXTBOX, "Ann").sensitive is False
    assert FormField(E1, FieldKind.TEXTBOX, "pw", sensitive=True).sensitive is True


def test_observation_and_result_defaults() -> None:
    observation = Observation(context=CTX)
    assert (observation.generation, observation.elements, observation.screenshot) == ("", (), None)
    assert (observation.viewport, observation.native_text, observation.surface) == (None, None, None)

    result = ActionResult(ok=True)
    assert (result.value, result.detail, result.context_changed, result.generation) == (None, None, False, "")
    assert (result.observation, result.source_context, result.opened_contexts, result.surface) == (None, None, (), None)


def test_element_defaults_and_extra_not_shared() -> None:
    first, second = Element(E1, "button", "Go"), Element(E1, "button", "Go")

    assert (first.value, first.bounds, first.enabled, first.focused) == (None, None, True, False)
    assert first.extra == {} and first.extra is not second.extra
    assert hash(first) == hash(second)


def test_screenshot_scale_defaults_to_one() -> None:
    assert Screenshot(b"png", 10, 20).scale == 1.0


def test_surface_context_is_an_empty_base() -> None:
    assert dataclasses.fields(SurfaceContext) == ()
    context = BrowserContext(url="https://example.test/", tabs=(CTX,))
    assert isinstance(context, SurfaceContext)
    assert (context.title, context.is_pdf_viewer, context.pixels_above, context.pixels_below) == ("", False, 0, 0)
    assert context.last_status is None


def test_fingerprint_is_stable_for_the_same_scene() -> None:
    first = _observation()
    second = _observation(
        generation="g9",
        screenshot=Screenshot(b"other", 1, 1),
        native_text="tree",
        surface=BrowserContext(url="https://example.test/"),
    )

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint == _observation().fingerprint
    assert len(first.fingerprint) == 16


def test_fingerprint_ignores_ref_generation_and_values() -> None:
    elements = (
        Element(DriverRef("e1", "g7"), "button", "Go", value="x"),
        Element(DriverRef("e2", "g7"), "link", "More"),
    )

    assert _observation(elements=elements).fingerprint == _observation().fingerprint


@pytest.mark.parametrize(
    "overrides",
    [
        {"context": ContextRef("c2", "window", "Home")},
        {"elements": (Element(E1, "button", "Stop"), Element(DriverRef("e2", "g1"), "link", "More"))},
        {"elements": (Element(E1, "link", "Go"), Element(DriverRef("e2", "g1"), "link", "More"))},
        {"elements": (Element(DriverRef("e3", "g1"), "button", "Go"), Element(DriverRef("e2", "g1"), "link", "More"))},
        {"elements": (Element(E1, "button", "Go"),)},
    ],
    ids=["context-id", "name", "role", "ref", "fewer-elements"],
)
def test_fingerprint_changes_with_context_refs_roles_and_names(overrides) -> None:
    assert _observation(**overrides).fingerprint != _observation().fingerprint
