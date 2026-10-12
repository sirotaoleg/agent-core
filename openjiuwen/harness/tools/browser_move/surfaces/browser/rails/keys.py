# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""State keys, statuses and field aliases the browser rail's modules share."""

from __future__ import annotations

from typing import Dict, Iterable

from openjiuwen.harness.tools.browser_move.surfaces.browser.working_context.store import BROWSER_TASK_STATE_KEY

_BROWSER_PHASE_STATE_KEY = BROWSER_TASK_STATE_KEY
_BROWSER_TOOL_RUNTIME_STATE_KEY = "__browser_tool_runtime_state__"
_BROWSER_TASK_DEADLINE_KEY = "__browser_task_deadline__"
_BROWSER_TERMINAL_SYNTHESIS_KEY = "terminal_synthesis_started"
_BATCH_MUTATING_TARGET_OPS = frozenset(
    {"click", "fill", "type", "autocomplete", "select_option", "set_checked", "select_visible_text"}
)
_BROWSER_TERMINAL_STATUSES = frozenset({"blocked", "partial", "completed"})
_BROWSER_FIELD_ALIASES: Dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "标题", "名称", "电影", "商品"),
    "url": ("url", "link", "href", "primary link", "primary_link", "链接", "网址"),
    "price": ("price", "cost", "价格", "价钱", "费用"),
    "rating": ("rating", "score", "评分", "星级"),
    "product_rating": ("product rating", "item rating", "商品评分", "宝贝评分"),
    "shop_rating": ("shop rating", "seller rating", "store rating", "店铺评分", "卖家评分"),
    "author": ("author", "creator", "writer", "作者", "博主", "发布者", "回答者"),
    "likes": ("likes", "like count", "upvotes", "点赞", "点赞数", "赞同", "赞同数", "获赞", "获赞数"),
    "favorites": ("favorites", "favourites", "bookmarks", "收藏", "收藏数"),
    "comments": ("comments", "comment count", "replies", "评论", "评论数", "回复数"),
    "shop": ("shop", "store", "merchant", "seller", "店铺", "商家", "卖家"),
    "duration": ("duration", "video length", "时长", "视频长度"),
    "views": ("views", "view count", "play count", "播放量", "观看数"),
    "exchange_rate": ("exchange rate", "conversion rate", "汇率", "兑换率"),
    "high_temperature": (
        "high temperature",
        "maximum temperature",
        "highest temperature",
        "最高温",
        "最高气温",
        "高温",
    ),
    "low_temperature": (
        "low temperature",
        "minimum temperature",
        "lowest temperature",
        "最低温",
        "最低气温",
        "低温",
    ),
    "sort_state": ("sort state", "sort order", "ordering", "排序状态", "排序方式", "排序"),
    "date": ("date", "日期", "哪天"),
    "time": ("time", "时间", "几点"),
    "address": ("address", "location", "地址", "地点"),
}
_BROWSER_SELECTED_SORT_LABELS = {
    "sale": "销量",
    "sales": "销量",
    "volume": "销量",
    "销量": "销量",
    "price": "价格",
    "价格": "价格",
    "latest": "最新",
    "newest": "最新",
    "最新": "最新",
    "comprehensive": "综合",
    "relevance": "综合",
    "default": "综合",
    "综合": "综合",
    "rating": "评分",
    "score": "评分",
    "评分": "评分",
}


def _contains_any_token(value: str, tokens: Iterable[str]) -> bool:
    for token in tokens:
        if token in value:
            return True
    return False
