# coding: utf-8
"""What the model reads for every recorded Playwright MCP answer: the answer decoded, then rendered."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    ContentBlock,
    ContextRef,
    DriverError,
    Observation,
    Ref,
    Wait,
)
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.result import decode, result_text
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.snapshot import parse_snapshot, without_boxes
from openjiuwen.harness.tools.browser_move.drivers.playwright_mcp.tools import DECODERS
from openjiuwen.harness.tools.browser_move.surfaces.browser.renderer import BrowserRenderer

HERE = Path(__file__).resolve().parent
FIXTURES_DIR = HERE.parent / "fixtures" / "mcp"
RESULTS_DIR = HERE / "golden" / "results" / "playwright_mcp"
REGENERATE_COMMAND = "uv run python -m tests.unit_tests.harness.tools.browser_move.snapshots.regenerate --results"

_RENDERER = BrowserRenderer()
_TABS = tuple(ContextRef(str(index), "tab") for index in range(4))


class _Resolver:
    contexts = _TABS
    current_context = _TABS[0]

    def resolve(self, model_id: str, generation: str) -> Ref:
        raise ValueError(model_id)

    def generation_of(self, ref: str) -> str:
        return "g1"


def list_cases() -> list[str]:
    return sorted(path.stem for path in FIXTURES_DIR.glob("*.json"))


def fixture(case: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{case}.json").read_text(encoding="utf-8"))


def _action(tool: str, arguments: dict[str, Any]) -> Action:
    if tool == "browser_wait_for":
        return Wait(seconds=arguments.get("time"), until_text=arguments.get("text"))
    return DECODERS[tool](arguments, _Resolver())


def _text(blocks: tuple[ContentBlock, ...]) -> str:
    lines = [block if isinstance(block, str) else f"[image: {block.width}x{block.height} png]" for block in blocks]
    return "\n".join(lines) + "\n"


def render_case(case: str, output_dir: Path) -> str:
    """The golden text of one fixture; ``browser_snapshot`` answers render as the observation they become."""
    request, raw = fixture(case)["request"], fixture(case)["raw_result"]
    action = _action(request["tool"], request["arguments"])
    try:
        result = decode(request["tool"], raw, output_dir=output_dir)
    except DriverError as error:
        return _text(_RENDERER.error(action, error, None))
    if request["tool"] != "browser_snapshot":
        return _text(_RENDERER.result(action, result))
    text = result_text(raw)
    if not result.ok:
        return _text(_RENDERER.observation(Observation(context=_TABS[0], native_text=text)))
    elements, header = parse_snapshot(text)
    observed = Observation(context=header.focused, elements=elements, native_text=without_boxes(text))
    return _text(_RENDERER.observation(observed))


def model_text_diff(case: str, text: str) -> str:
    """The rendered text against what the model read before, as a unified diff."""
    before = fixture(case)["model_text"]
    return "".join(
        difflib.unified_diff(
            (before + "\n").splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"fixtures/mcp/{case}.json model_text",
            tofile=f"golden/results/playwright_mcp/{case}.txt",
        )
    )
