# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The browser's typed surface context, carried in ``Observation.surface`` and ``ActionResult.surface``."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.harness.tools.browser_move.contract import ContextRef, SurfaceContext


@dataclass(frozen=True, kw_only=True)
class BrowserContext(SurfaceContext):
    """Page url and title, tabs, PDF flag, scroll position, HTTP status and open dialog."""

    url: str
    title: str = ""
    tabs: tuple[ContextRef, ...] = ()
    tab_urls: tuple[str, ...] = ()
    current_tab: int | None = None
    is_pdf_viewer: bool = False
    pixels_above: int = 0
    pixels_below: int = 0
    last_status: int | None = None
    dialog: str | None = None

    @property
    def identity(self) -> str:
        return self.url
