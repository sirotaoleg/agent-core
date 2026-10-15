# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The browser surface's policy rules: no paste chord and no clipboard read reaches a page, on either dialect."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.browser_move.contract import PolicyDenied, PressKey, RunScript
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.runtime.rails.action_policy import ActionPolicy
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import Drop, UploadFile
from openjiuwen.harness.tools.browser_move.surfaces.browser.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.surfaces.browser.policy import CLIPBOARD_DENIED, policy_denial, upload_rules
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import create_browser_surface
from openjiuwen.harness.tools.browser_move.surfaces.browser.tools import BROWSER_TOOL_SPECS
from tests.unit_tests.harness.tools.browser_move.surfaces.test_browser_batch import DONE, _batch, _context, _sent

MCP_BUILDERS = {f"mcp_playwright-official_{name}": build for name, build in DECODERS.items()}
GUARD = resources.files("openjiuwen.harness.tools.browser_move.surfaces.browser.probes").joinpath("clipboard_guard.js")
BROWSER_BUILDERS = {spec.name: spec.build for spec in BROWSER_TOOL_SPECS if spec.build is not None}
DIALECTS = [
    pytest.param(MCP_BUILDERS, "mcp_playwright-official_", id="playwright_mcp"),
    pytest.param(BROWSER_BUILDERS, "", id="browser_use"),
]
PASTE_KEYS = [
    "Control+v",
    "Control+V",
    "ctrl+v",
    "Meta+v",
    "Cmd+v",
    "ControlOrMeta+v",
    "Control+Shift+V",
    "Shift+Insert",
]
CLIPBOARD_SCRIPTS = [
    "() => navigator.clipboard.readText()",
    "async () => (await navigator.clipboard.read()).length",
    "() => navigator['clipboard']['readText']()",
    "() => { document.execCommand('paste'); return 1 }",
]


@pytest.mark.parametrize("key", PASTE_KEYS)
def test_paste_chords_are_denied(key: str) -> None:
    assert policy_denial(PressKey(key), None) == CLIPBOARD_DENIED


@pytest.mark.parametrize("key", ["Enter", "v", "Shift+v", "Alt+v", "Control+a", "Control+c", "Insert", "Control+Enter"])
def test_other_keys_pass(key: str) -> None:
    assert policy_denial(PressKey(key), None) is None


@pytest.mark.parametrize("code", CLIPBOARD_SCRIPTS)
def test_page_scripts_that_read_the_clipboard_are_denied(code: str) -> None:
    assert policy_denial(RunScript(code), None) == CLIPBOARD_DENIED


@pytest.mark.parametrize("code", ["() => document.title", "() => document.querySelector('input').value"])
def test_other_page_scripts_pass(code: str) -> None:
    assert policy_denial(RunScript(code), None) is None


def test_the_denial_tells_the_model_to_read_the_value_from_the_page() -> None:
    assert "clipboard" in CLIPBOARD_DENIED and "Read the value from the page instead" in CLIPBOARD_DENIED


def test_the_clipboard_guard_script_is_shipped_with_the_policy_message() -> None:
    assert GUARD.is_file()
    text = GUARD.read_text(encoding="utf-8")
    assert "the clipboard belongs to the host machine" in text and "Read the value from the page instead" in text


def test_the_guard_rejects_an_aliased_read_and_refuses_the_paste_command_in_the_page() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = (
        "globalThis.navigator = { clipboard: { readText: () => Promise.resolve('secret') } };\n"
        "globalThis.document = { execCommand: (command) => command };\n"
        + GUARD.read_text(encoding="utf-8")
        + "\nconst c = navigator.clipboard;\n"
        "const report = (value) => console.log(JSON.stringify(value));\n"
        "c.readText().then(() => report({ read: 'answered' }), (error) => report("
        "{ read: error.message, paste: document.execCommand('paste'), copy: document.execCommand('copy') }));"
    )
    completed = subprocess.run([node, "-e", script], check=True, text=True, capture_output=True)
    assert json.loads(completed.stdout) == {"read": CLIPBOARD_DENIED, "paste": False, "copy": "copy"}


def _rail_verdict(
    builders: dict[str, Any], tool: str, args: dict[str, Any], environ: dict[str, str] | None = None
) -> AgentCallbackContext:
    profile = create_browser_surface(environ or {}).profile
    policy = ActionPolicy(builders, profile.policy_rules, BrowserPageState())
    tool_call = ToolCall(id="call-1", type="function", name=tool, arguments=json.dumps(args))
    ctx = AgentCallbackContext(
        agent=MagicMock(), inputs=ToolCallInputs(tool_call=tool_call, tool_name=tool, tool_args=args), extra={}
    )
    asyncio.run(policy.before_tool_call(ctx))
    return ctx


@pytest.mark.parametrize(("builders", "prefix"), DIALECTS)
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("browser_press_key", {"key": "Control+v"}),
        ("browser_press_key", {"key": "Shift+Insert"}),
        ("browser_evaluate", {"function": "() => navigator.clipboard.readText()"}),
    ],
)
def test_the_policy_rail_denies_clipboard_access_on_both_dialects(
    builders: dict[str, Any], prefix: str, tool: str, args: dict[str, Any]
) -> None:
    ctx = _rail_verdict(builders, prefix + tool, args)

    assert ctx.inputs.tool_msg.content == f"### Error\n{CLIPBOARD_DENIED}"
    assert isinstance(ctx.inputs.tool_result, PolicyDenied)
    assert ctx.extra["_skip_tool_calls"] == {"call-1": True}


@pytest.mark.parametrize(("builders", "prefix"), DIALECTS)
def test_the_policy_rail_lets_ordinary_keys_through_on_both_dialects(builders: dict[str, Any], prefix: str) -> None:
    ctx = _rail_verdict(builders, prefix + "browser_press_key", {"key": "Enter"})

    assert ctx.inputs.tool_msg is None
    assert "_skip_tool_calls" not in ctx.extra


@pytest.mark.parametrize("key", ["Control+v", "Shift+Insert"])
def test_a_batch_press_step_with_a_paste_chord_never_reaches_the_driver(key: str) -> None:
    context = _context(DONE)

    payload = _batch(context, [{"op": "press", "key": key}]).data["content"]

    assert _sent(context) == []
    assert payload["ok"] is False and payload["error"] == CLIPBOARD_DENIED


def test_a_batch_stops_at_a_paste_chord_after_the_steps_before_it() -> None:
    context = _context(DONE, DONE)

    payload = _batch(
        context, [{"op": "press", "key": "Tab"}, {"op": "press", "key": "Meta+v"}, {"op": "press", "key": "Enter"}]
    ).data["content"]

    assert _sent(context) == [PressKey("Tab")]
    assert [step["ok"] for step in payload["steps"]] == [True, False]
    assert payload["steps"][1]["error"] == CLIPBOARD_DENIED


def _upload_denial(path: str, upload_root: Path | None = None, workspace_root: Path | None = None) -> str | None:
    return policy_denial(UploadFile(None, (path,)), None, upload_rules(upload_root, workspace_root))


def test_an_upload_inside_the_upload_root_or_the_workspace_passes(tmp_path: Path) -> None:
    uploads, workspace = tmp_path / "uploads", tmp_path / "workspace"

    assert _upload_denial(str(uploads / "cv.pdf"), uploads, workspace) is None
    assert _upload_denial(str(workspace / "notes" / "a.txt"), uploads, workspace) is None
    assert _upload_denial("cv.pdf", uploads, workspace) is None
    assert _upload_denial(f'"{uploads / "cv.pdf"}"', uploads) is None


def test_an_upload_outside_the_configured_folders_is_denied(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    secret = tmp_path / ".ssh" / "id_rsa"

    denial = _upload_denial(str(secret), uploads)

    assert denial is not None and repr(str(secret)) in denial and str(uploads.resolve()) in denial
    assert _upload_denial("../.ssh/id_rsa", uploads) is not None
    assert _upload_denial("~/.ssh/id_rsa", uploads) is not None


def test_a_relative_upload_may_not_climb_out_of_the_root_it_is_joined_to(tmp_path: Path) -> None:
    uploads = tmp_path / "home"
    workspace = uploads / "ws"

    assert _upload_denial("../x.txt", uploads, workspace) is not None
    assert _upload_denial("notes/../x.txt", uploads, workspace) is None


def test_without_configured_folders_every_upload_is_denied(tmp_path: Path) -> None:
    assert "none configured" in _upload_denial(str(Path.cwd() / "cv.pdf"))
    assert "none configured" in _upload_denial(str(tmp_path / "cv.pdf"))
    assert "no upload folder is configured" in _upload_denial("data/cv.pdf")


def test_a_relative_name_is_judged_under_the_upload_root_whatever_the_process_cwd_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    for folder in (allowed, outside):
        folder.mkdir()
        (folder / "report.txt").write_text(folder.name, encoding="utf-8")
    monkeypatch.chdir(outside)

    assert _upload_denial("report.txt", allowed) is None
    assert _upload_denial("../outside/report.txt", allowed) is not None
    assert _upload_denial(str(outside / "report.txt"), allowed) is not None


def test_a_symlink_out_of_the_upload_root_is_denied(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (tmp_path / "secret.txt").write_text("k", encoding="utf-8")
    link = uploads / "innocent.txt"
    try:
        os.symlink(tmp_path / "secret.txt", link)
    except OSError:
        pytest.skip("this account cannot create symlinks")

    assert _upload_denial(str(link), uploads) is not None
    assert _upload_denial("innocent.txt", uploads) is not None


def test_a_drop_is_confined_like_an_upload_and_one_without_files_passes(tmp_path: Path) -> None:
    rules = upload_rules(tmp_path / "uploads", None)

    assert policy_denial(Drop(None, paths=(str(tmp_path / "x.txt"),)), None, rules) is not None
    assert policy_denial(Drop(None, data=(("text/plain", "hi"),)), None, rules) is None


@pytest.mark.parametrize(("builders", "prefix"), DIALECTS)
def test_the_policy_rail_denies_an_upload_outside_the_upload_root_on_both_dialects(
    builders: dict[str, Any], prefix: str, tmp_path: Path
) -> None:
    uploads = tmp_path / "uploads"
    environ = {"BROWSER_UPLOAD_ROOT": str(uploads)}

    tool = prefix + "browser_file_upload"
    target = {"ref": "e1", "generation_id": "g0"}
    denied = _rail_verdict(builders, tool, {"paths": [str(tmp_path / "key")], **target}, environ)
    allowed = _rail_verdict(builders, tool, {"paths": [str(uploads / "cv.pdf")], **target}, environ)

    assert isinstance(denied.inputs.tool_result, PolicyDenied)
    assert "outside the folders files may be uploaded from" in denied.inputs.tool_msg.content
    assert allowed.inputs.tool_msg is None


@pytest.mark.parametrize(("builders", "prefix"), DIALECTS)
def test_the_policy_rail_judges_a_relative_name_under_the_upload_root_on_both_dialects(
    builders: dict[str, Any], prefix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    for folder in (allowed, outside):
        folder.mkdir()
        (folder / "report.txt").write_text(folder.name, encoding="utf-8")
    monkeypatch.chdir(outside)
    environ = {"BROWSER_UPLOAD_ROOT": str(allowed)}
    tool = prefix + "browser_file_upload"
    target = {"ref": "e1", "generation_id": "g0"}

    passed = _rail_verdict(builders, tool, {"paths": ["report.txt"], **target}, environ)
    climbed = _rail_verdict(builders, tool, {"paths": ["../outside/report.txt"], **target}, environ)

    assert passed.inputs.tool_msg is None
    assert isinstance(climbed.inputs.tool_result, PolicyDenied)
    assert "outside the upload folder" in climbed.inputs.tool_msg.content


@pytest.mark.parametrize(
    ("builders", "tool", "args"),
    [
        (MCP_BUILDERS, "mcp_playwright-official_browser_drop", {"target": "e1"}),
        (BROWSER_BUILDERS, "browser_drop", {"ref": "e1", "generation_id": "g0"}),
    ],
    ids=["playwright_mcp", "browser_use"],
)
def test_the_policy_rail_denies_a_drop_outside_the_upload_root_on_both_dialects(
    builders: dict[str, Any], tool: str, args: dict[str, Any], tmp_path: Path
) -> None:
    environ = {"BROWSER_UPLOAD_ROOT": str(tmp_path / "uploads")}

    ctx = _rail_verdict(builders, tool, {**args, "paths": [str(tmp_path / "key")]}, environ)

    assert isinstance(ctx.inputs.tool_result, PolicyDenied)
    assert "outside the folders files may be uploaded from" in ctx.inputs.tool_msg.content


def test_the_profile_confines_uploads_to_its_settings_folders(tmp_path: Path) -> None:
    rules = create_browser_surface({"BROWSER_UPLOAD_ROOT": str(tmp_path)}).profile.policy_rules

    assert [rule.command_type for rule in rules[-2:]] == [UploadFile, Drop]
    assert policy_denial(UploadFile(None, (str(tmp_path / "a.txt"),)), None, rules) is None
