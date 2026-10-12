# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The worker answer's screenshot reaches the caller only as something a vision model can read as an image."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tests.unit_tests.harness.tools.browser_move.test_service import _make_service

FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "screenshot_element.json"
PNG = base64.b64decode(json.loads(FIXTURE.read_text(encoding="utf-8"))["raw_result"]["content"][1]["data"])
JPEG = b"\xff\xd8\xff\xe0" + bytes(32)


def _normalized(tmp_path: Path, value: object) -> object:
    return _make_service(runtime_cwd=str(tmp_path))._normalize_screenshot_value(value)


def _decoded(data_url: str) -> bytes:
    return base64.b64decode(data_url.partition(",")[2], validate=True)


def test_a_screenshot_file_the_server_saved_becomes_a_png_data_url(tmp_path: Path) -> None:
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "wa.png").write_bytes(PNG)

    value = _normalized(tmp_path, "screenshots/wa.png")

    assert value.startswith("data:image/png;base64,")
    assert _decoded(value) == PNG


def test_the_media_type_follows_the_bytes_not_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(JPEG)

    assert _normalized(tmp_path, "shot.png").startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("value", ["screenshots/wa.png", "file://screenshots/none.png", "", "   ", None, 7, {"a": 1}])
def test_a_path_that_names_no_file_or_a_non_string_is_dropped(tmp_path: Path, value: object) -> None:
    assert _normalized(tmp_path, value) is None


def test_a_file_that_is_not_an_image_is_dropped(tmp_path: Path) -> None:
    (tmp_path / "notes.png").write_text("not an image", encoding="utf-8")

    assert _normalized(tmp_path, "notes.png") is None


def test_an_image_data_url_is_kept_and_a_broken_one_is_dropped(tmp_path: Path) -> None:
    good = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")

    assert _normalized(tmp_path, good) == good
    assert _normalized(tmp_path, "data:image/png;base64,not-base64!") is None
    assert _normalized(tmp_path, "data:image/png;base64," + base64.b64encode(b"text").decode("ascii")) is None


def test_a_remote_link_is_kept(tmp_path: Path) -> None:
    assert _normalized(tmp_path, "https://example.com/shot.png") == "https://example.com/shot.png"
