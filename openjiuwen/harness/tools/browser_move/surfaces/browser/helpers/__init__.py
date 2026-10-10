# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser helpers built on commands: the extra tools of the profile and the hooks a decision policy uses."""

from .find import FindTool
from .page import activate_page, page_metadata
from .probes import ProbeCardsTool, ProbeInteractivesTool, probe_for_policy

EXTRA_TOOLS = (ProbeInteractivesTool, ProbeCardsTool, FindTool)

__all__ = [
    "EXTRA_TOOLS",
    "FindTool",
    "ProbeCardsTool",
    "ProbeInteractivesTool",
    "activate_page",
    "page_metadata",
    "probe_for_policy",
]
