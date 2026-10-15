# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Live cases of the browser scene beyond the shared suite, mixed into both drivers' conformance classes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from openjiuwen.harness.tools.browser_move.contract import DriverError, Observe, Scene
from openjiuwen.harness.tools.browser_move.contract.testing.conformance import (
    DriverFactory,
    _expect_error,
    _prepare,
    _session,
    _skip_if_reason,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.scene import clipboard_read_case, paste_command_case


async def _check(factory: DriverFactory, scene: Scene, case_of: Callable) -> None:
    async with _session(factory, scene) as driver:
        case = _skip_if_reason(scene, case_of(driver.info.actions))
        prepared = await _prepare(driver, case)
        result = await driver.execute(prepared.action)
        after = await driver.observe(Observe())
        assert case.check is not None and case.check(prepared.before, after, result), f"{prepared.action!r}: {result!r}"


class BrowserSceneExtras:
    def test_a_clipboard_read_through_an_alias_is_rejected_in_the_page(
        self, conformance_scene, conformance_driver_factory
    ):
        error = asyncio.run(
            _expect_error(conformance_driver_factory, conformance_scene, clipboard_read_case, DriverError)
        )
        assert "clipboard belongs to the host machine" in str(error)

    def test_the_paste_command_is_refused_in_the_page(self, conformance_scene, conformance_driver_factory):
        asyncio.run(_check(conformance_driver_factory, conformance_scene, paste_command_case))
