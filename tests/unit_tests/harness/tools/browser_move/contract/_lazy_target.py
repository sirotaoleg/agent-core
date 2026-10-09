# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Imported only by the lazy-import registry test, through an import string."""

CREATED: list[tuple] = []


def make_thing(*args, **kwargs):
    CREATED.append((args, kwargs))
    return ("thing", args, kwargs)


class Nested:
    @staticmethod
    def factory():
        return "nested"
