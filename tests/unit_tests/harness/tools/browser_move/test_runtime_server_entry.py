# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The runtime server entry runs as a module: ``python -m …playwright_runtime_mcp_server``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SERVER = "openjiuwen.harness.tools.browser_move.playwright_runtime_mcp_server"


def test_server_entry_prints_its_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", SERVER, "--help"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "--transport" in result.stdout
