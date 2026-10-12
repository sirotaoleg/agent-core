# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.browser_move.runtime import topology


@pytest.fixture(autouse=True)
def _no_image_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both topologies probe the model before building an agent; unit tests have no model endpoint.
    monkeypatch.setattr(topology, "probe_image_support", AsyncMock(return_value=None))
