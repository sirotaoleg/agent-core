# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``browser_recall_offload`` over the workspace and language the agent is built with."""

from __future__ import annotations

from openjiuwen.harness.tools.browser_move.offload_recall import BrowserOffloadRecallTool
from openjiuwen.harness.tools.browser_move.playwright_runtime.tool_factory import ToolContext, ToolFlags


class RecallOffloadTool(BrowserOffloadRecallTool):
    """Reads back this session's offloaded browser results; never counts against the run's budgets."""

    flags = ToolFlags(exempt_from_budget=True)

    def __init__(self, context: ToolContext) -> None:
        super().__init__(context.workspace, language=context.language)
