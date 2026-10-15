# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The core runtime package: the composition root, the session, the service and the topologies."""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
SRC_ROOT = _HERE.parent
REPO_ROOT = SRC_ROOT.parent.parent.parent.parent

__all__ = ["REPO_ROOT", "SRC_ROOT"]
