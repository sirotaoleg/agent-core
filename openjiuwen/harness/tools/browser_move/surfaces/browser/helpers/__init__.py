# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser helpers built on commands: the profile's extra tools, the custom actions and a decision policy's hooks."""

from .batch import BatchTool
from .custom import CUSTOM_ACTIONS, drag_and_drop, get_element_coordinates, set_input_files
from .find import FindTool
from .page import activate_page, page_metadata
from .probes import ProbeCardsTool, ProbeInteractivesTool, probe_for_policy
from .recall import RecallOffloadTool

EXTRA_TOOLS = (ProbeInteractivesTool, ProbeCardsTool, BatchTool, FindTool, RecallOffloadTool)

__all__ = [
    "BatchTool",
    "CUSTOM_ACTIONS",
    "EXTRA_TOOLS",
    "FindTool",
    "ProbeCardsTool",
    "ProbeInteractivesTool",
    "RecallOffloadTool",
    "activate_page",
    "drag_and_drop",
    "get_element_coordinates",
    "page_metadata",
    "probe_for_policy",
    "set_input_files",
]
