#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser conformance scene without a browser: its server, its pages and the commands its cases build."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import openjiuwen.harness.tools.browser_move.surfaces.browser.scene as scene_module
from openjiuwen.harness.tools.browser_move.contract import (
    REF_KINDS,
    ContextRef,
    DriverRef,
    Element,
    IndexRef,
    Locator,
    Observation,
    PointRef,
    SceneCase,
    iter_refs,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Navigate
from openjiuwen.harness.tools.browser_move.surfaces.browser.context import BrowserContext
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene import browser_scene

TARGETS = [value for value in vars(scene_module).values() if isinstance(value, scene_module._Target)]
HAPPY_COMMANDS = list(scene_module._BrowserScene()._happy)
ALL_ACTIONS = {command: frozenset(REF_KINDS) for command in HAPPY_COMMANDS}


@pytest.fixture(scope="module")
def scene():
    scene = browser_scene()
    scene.start()
    try:
        yield scene
    finally:
        scene.stop()


def _observation(scene) -> Observation:
    elements = tuple(
        Element(ref=DriverRef(f"e{i}", ""), role="button", name=target.name, bounds=(10.0, 20.0 * i, 80.0, 16.0))
        for i, target in enumerate(TARGETS, start=1)
    )
    tabs = (ContextRef("t1", "tab", "Scene start"), ContextRef("t2", "tab", "Scene second"))
    url = scene.enter()[0].url
    return Observation(context=tabs[0], elements=elements, surface=BrowserContext(url=url, tabs=tabs))


def _built(case: SceneCase, observation: Observation) -> list:
    return [step((observation, observation)) for step in case.steps + case.cleanup]


def test_server_serves_the_pages_on_loopback_only_while_started():
    scene = browser_scene()
    scene.start()
    url = scene.enter()[0].url
    try:
        assert url.startswith("http://127.0.0.1:") and url.endswith("/index.html")
        for page in ("index.html", "second.html", "data.json"):
            with urllib.request.urlopen(url.replace("index.html", page), timeout=5) as response:
                assert response.status == 200
    finally:
        scene.stop()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(url, timeout=2)


def test_every_target_is_on_the_start_page():
    html = (scene_module.PAGES / "index.html").read_text(encoding="utf-8")
    for target in TARGETS:
        assert f'id="{target.css_id}"' in html, target
        assert re.search(rf'aria-label="{re.escape(target.name)}"|>{re.escape(target.name)}<', html), target


def test_upload_file_exists_while_started(scene):
    case = scene.happy(scene_module.UploadFile, DriverRef, ALL_ACTIONS)
    path = Path(case.steps[0]((_observation(scene),)).paths[0])
    assert path.name == scene_module.UPLOAD_NAME and path.is_file()


@pytest.mark.parametrize("kind", [DriverRef, Locator, PointRef])
@pytest.mark.parametrize("command", HAPPY_COMMANDS, ids=lambda c: c.__name__)
def test_happy_path_builds_the_command_with_the_ref_kind(scene, command, kind):
    observation = _observation(scene)
    case = scene.happy(command, kind, ALL_ACTIONS)
    assert isinstance(case, SceneCase), case
    action = _built(case, observation)[len(case.steps) - 1]
    assert type(action) is command
    assert {type(ref) for ref in iter_refs(action)} <= {kind}


@pytest.mark.parametrize("command", HAPPY_COMMANDS, ids=lambda c: c.__name__)
def test_happy_path_without_refs_builds_or_explains(scene, command):
    case = scene.happy(command, None, ALL_ACTIONS)
    if isinstance(case, str):
        assert case
        return
    action = _built(case, _observation(scene))[len(case.steps) - 1]
    assert type(action) is command
    assert not list(iter_refs(action))


def test_index_ref_is_skipped_with_a_reason(scene):
    assert "IndexRef" in scene.happy(scene_module.Click, IndexRef, ALL_ACTIONS)


def test_error_and_context_cases_build(scene):
    observation = _observation(scene)
    for name, build in [("stale", scene.stale), ("intercepted", scene.intercepted), ("timeout", scene.timeout)] + list(
        scene.context_changes.items()
    ):
        case = build(ALL_ACTIONS)
        assert isinstance(case, SceneCase), (name, case)
        assert _built(case, observation), name


def test_same_url_reload_navigates_to_the_entry_address(scene):
    case = scene.context_changes["same-url-reload"](ALL_ACTIONS)
    assert _built(case, _observation(scene)) == [Navigate(url=scene.enter()[0].url)]


def test_cases_explain_what_the_driver_lacks(scene):
    no_click = {command: kinds for command, kinds in ALL_ACTIONS.items() if command is not scene_module.Click}
    assert "Click" in scene.timeout(no_click)
    assert "DriverRef" in scene.stale({scene_module.Click: frozenset({PointRef})})
    assert isinstance(scene.intercepted({scene_module.Click: frozenset({PointRef})}), str)
