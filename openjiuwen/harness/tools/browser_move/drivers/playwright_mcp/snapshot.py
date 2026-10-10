# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parse @playwright/mcp text: result sections, the page header, tab lists and the ARIA snapshot YAML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.harness.tools.browser_move.contract import ContextRef, DriverRef, Element

TAB_KIND = "tab"
ARIA_REF = re.compile(r"(?:f\d+)?e\d+")

_SECTION = re.compile(r"^### (.+)$", re.MULTILINE)
_FENCED_YAML = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_PAGE_FIELD = re.compile(r"^- Page (URL|Title): ?(.*)$", re.MULTILINE)
_TAB_LINE = re.compile(r"^- (\d+): (\(current\) )?\[(.*)\]\((.*)\)$", re.MULTILINE)
_NODE_LINE = re.compile(r"^(?P<indent> *)- (?P<body>.*)$")
_PROPERTY = re.compile(r"/(?P<key>[\w-]+): ?(?P<value>.*)")
_NODE = re.compile(
    r'(?P<role>[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)(?::(?: (?P<text>.*))?)?'
)
_ATTRIBUTE = re.compile(r"\[([^\]=]+)(?:=([^\]]*))?\]")
_VALUE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider"})


@dataclass(frozen=True)
class PageHeader:
    """The page and tabs a result reports; ``tabs`` lists every open tab, the focused one included."""

    url: str
    title: str
    tabs: tuple[ContextRef, ...]
    focused: ContextRef


def split_sections(text: str) -> dict[str, str]:
    """``### Name`` sections in order; text before the first header is kept under ``""``."""
    parts: dict[str, str] = {}
    matches = list(_SECTION.finditer(text))
    preface = text[: matches[0].start()] if matches else text
    if preface.strip():
        parts[""] = preface.strip("\n")
    for match, following in zip(matches, matches[1:] + [None]):
        end = following.start() if following is not None else len(text)
        parts[match.group(1).strip()] = text[match.end() : end].strip("\n")
    return parts


def snapshot_yaml(section: str) -> str | None:
    """The inline YAML of a ``### Snapshot`` section, or None when the section only links a file."""
    match = _FENCED_YAML.search(section)
    return None if match is None else match.group(1)


def parse_tabs(body: str) -> PageHeader | None:
    """Header from a tab list (``browser_tabs`` result or ``### Open tabs``); None when it lists no tab."""
    tabs: list[ContextRef] = []
    focused: tuple[ContextRef, str] | None = None
    for match in _TAB_LINE.finditer(body):
        index, current, title, url = match.groups()
        tab = ContextRef(id=index, kind=TAB_KIND, title=title)
        tabs.append(tab)
        if current:
            focused = (tab, url)
    if focused is None:
        return None
    return PageHeader(url=focused[1], title=focused[0].title or "", tabs=tuple(tabs), focused=focused[0])


def parse_header(parts: dict[str, str]) -> PageHeader | None:
    """Header from ``### Page`` and ``### Open tabs``; None when the result has no ``### Page``."""
    page = parts.get("Page")
    if page is None:
        return None
    fields = dict(_PAGE_FIELD.findall(page))
    url, title = fields.get("URL", ""), fields.get("Title", "")
    listed = parse_tabs(parts.get("Open tabs", ""))
    if listed is not None:
        return PageHeader(url=url, title=title, tabs=listed.tabs, focused=listed.focused)
    # The server lists tabs only when more than one is open.
    only = ContextRef(id="0", kind=TAB_KIND, title=title)
    return PageHeader(url=url, title=title, tabs=(only,), focused=only)


def parse_snapshot(text: str) -> tuple[tuple[Element, ...], PageHeader | None]:
    """Elements and header of a ``browser_snapshot`` result."""
    parts = split_sections(text)
    yaml = snapshot_yaml(parts.get("Snapshot", ""))
    return (parse_elements(yaml) if yaml else ()), parse_header(parts)


def parse_elements(yaml: str) -> tuple[Element, ...]:
    """Every node with a ref; ``/key: value`` lines and a control's text or selected option fill its fields."""
    elements: list[dict[str, Any]] = []
    parents: list[tuple[int, dict[str, Any] | None]] = []
    for line in yaml.splitlines():
        match = _NODE_LINE.match(line)
        if match is None:
            continue
        indent, body = len(match.group("indent")), _unquote_key(match.group("body"))
        while parents and parents[-1][0] >= indent:
            parents.pop()
        parent = parents[-1][1] if parents else None
        prop = _PROPERTY.fullmatch(body)
        if prop is not None:
            if parent is not None:
                parent["extra"][prop.group("key")] = prop.group("value")
            continue
        node = _NODE.fullmatch(body)
        if node is None:
            parents.append((indent, None))
            continue
        role = node.group("role")
        name = json.loads(f'"{node.group("name")}"') if node.group("name") is not None else ""
        attributes = {key: value or True for key, value in _ATTRIBUTE.findall(node.group("attrs"))}
        text = _unquote_value(node.group("text") or "")
        if parent is not None and parent["role"] in _VALUE_ROLES and parent["value"] is None:
            if role == "text":
                parent["value"] = text
            elif role == "option" and "selected" in attributes:
                parent["value"] = name
        ref = attributes.pop("ref", None)
        element = None
        if isinstance(ref, str) and ARIA_REF.fullmatch(ref):
            element = _element(ref, role, name, attributes, text)
        parents.append((indent, element))
        if element is not None:
            elements.append(element)
    return tuple(Element(**element) for element in elements)


def _unquote_key(body: str) -> str:
    # YAML single-quotes a node whose text has characters like ": " in it.
    if not body.startswith("'"):
        return body
    match = re.fullmatch(r"'((?:[^']|'')*)'(:.*)?", body)
    return body if match is None else match.group(1).replace("''", "'") + (match.group(2) or "")


def _element(ref: str, role: str, name: str, attributes: dict[str, Any], text: str) -> dict[str, Any]:
    extra: dict[str, Any] = {key: value for key, value in attributes.items() if key not in ("box", "active")}
    if text and role not in _VALUE_ROLES:
        extra["text"] = text
    box = attributes.get("box")
    return {
        "ref": DriverRef(ref=ref, generation=""),
        "role": role,
        "name": name,
        "value": text if text and role in _VALUE_ROLES else None,
        "bounds": tuple(float(part) for part in box.split(",")) if isinstance(box, str) else None,
        "enabled": "disabled" not in attributes,
        "focused": "active" in attributes,
        "extra": extra,
    }


def _unquote_value(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return str(json.loads(text))
        except ValueError:
            return text
    return text
