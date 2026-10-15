# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``upload_paths``: the one resolution rule the policy and both drivers apply to upload and drop paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import upload_paths


def test_an_absolute_path_is_resolved_as_given_with_or_without_an_upload_root(tmp_path: Path) -> None:
    given = str(tmp_path / "docs" / ".." / "cv.pdf")

    assert upload_paths((given,), None) == ((tmp_path / "cv.pdf").resolve(),)
    assert upload_paths((given,), tmp_path / "uploads") == ((tmp_path / "cv.pdf").resolve(),)


def test_a_relative_name_is_the_file_under_the_upload_root_not_under_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    for folder in (allowed, outside):
        folder.mkdir()
        (folder / "report.txt").write_text(folder.name, encoding="utf-8")
    monkeypatch.chdir(outside)

    assert upload_paths(("report.txt", "notes/../report.txt"), allowed) == ((allowed / "report.txt").resolve(),) * 2


def test_a_relative_name_without_an_upload_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no upload folder is configured"):
        upload_paths(("report.txt",), None)


@pytest.mark.parametrize("name", ["../outside/report.txt", "notes/../../report.txt"])
def test_a_relative_name_that_leaves_the_upload_root_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="outside the upload folder"):
        upload_paths((name,), tmp_path / "allowed")


def test_wrapping_quotes_are_stripped_and_the_home_marker_expanded(tmp_path: Path) -> None:
    quoted = f'"{tmp_path / "cv.pdf"}"'

    assert upload_paths((quoted, " 'cv.pdf' "), tmp_path) == ((tmp_path / "cv.pdf").resolve(),) * 2
    assert upload_paths(("~/cv.pdf",), None) == ((Path.home() / "cv.pdf").resolve(),)


def test_a_symlink_out_of_the_upload_root_is_refused_under_its_relative_name(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (tmp_path / "secret.txt").write_text("k", encoding="utf-8")
    try:
        os.symlink(tmp_path / "secret.txt", uploads / "innocent.txt")
    except OSError:
        pytest.skip("this account cannot create symlinks")

    with pytest.raises(ValueError, match="outside the upload folder"):
        upload_paths(("innocent.txt",), uploads)
