# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, TypeVar

MASK = "***"

_T = TypeVar("_T")


def is_sensitive(command: Any) -> bool:
    return bool(getattr(command, "sensitive", False) and getattr(command, "__sensitive_fields__", ()))


def masked(value: _T) -> _T:
    """``value`` with the ``__sensitive_fields__`` of every sensitive command, or part of one, set to ``MASK``."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        hidden = value.__sensitive_fields__ if is_sensitive(value) else ()
        changes = {
            item.name: MASK if item.name in hidden else masked(getattr(value, item.name))
            for item in dataclasses.fields(value)
            if item.init
        }
        return dataclasses.replace(value, **changes)
    if isinstance(value, (tuple, list)):
        return type(value)(masked(item) for item in value)
    if isinstance(value, Mapping):
        return {key: masked(item) for key, item in value.items()}
    return value
