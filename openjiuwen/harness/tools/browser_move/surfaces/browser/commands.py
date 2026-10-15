# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser-only commands. Drivers handle them with ``@handles`` exactly like the shared ones in ``contract``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from openjiuwen.harness.tools.browser_move.contract import Action, Effect, Ref


@dataclass(frozen=True)
class Navigate(Action):
    effect: ClassVar[Effect] = Effect.NAVIGATE

    url: str


@dataclass(frozen=True)
class Back(Action):
    effect: ClassVar[Effect] = Effect.NAVIGATE


@dataclass(frozen=True)
class OpenContext(Action):
    """Open a new tab, at ``url`` when given."""

    effect: ClassVar[Effect] = Effect.NAVIGATE

    url: str | None = None


@dataclass(frozen=True)
class SelectOption(Action):
    """Select options of a ``<select>``; each value matches an option's value or label."""

    effect: ClassVar[Effect] = Effect.ACT

    target: Ref
    values: tuple[str, ...]


@dataclass(frozen=True)
class UploadFile(Action):
    """Set files on ``target``, or answer an open file chooser (``target`` ``None``); empty ``paths`` cancels."""

    effect: ClassVar[Effect] = Effect.ACT

    target: Ref | None
    paths: tuple[str, ...] = ()


def upload_paths(paths: tuple[str, ...], upload_root: Path | None) -> tuple[Path, ...]:
    """The resolved absolute path of every entry; a relative name is a file under ``upload_root`` and stays in it."""
    root = Path(upload_root).expanduser().resolve() if upload_root is not None else None
    resolved = []
    for raw in paths:
        text = raw.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
            text = text[1:-1].strip()
        path = Path(text).expanduser()
        if path.is_absolute():
            resolved.append(path.resolve())
            continue
        if root is None:
            raise ValueError(
                f"{raw!r} is a relative path and no upload folder is configured: give an absolute path, or set "
                "BROWSER_UPLOAD_ROOT and name a file under it (list_upload_files)"
            )
        candidate = (root / path).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(
                f"{raw!r} resolves to {candidate}, outside the upload folder {root}: name a file under it "
                "(list_upload_files)"
            )
        resolved.append(candidate)
    return tuple(resolved)


class FieldKind(str, Enum):
    TEXTBOX = "textbox"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    COMBOBOX = "combobox"
    SLIDER = "slider"


@dataclass(frozen=True)
class FormField:
    """One field of ``FillForm``. ``value`` is ``"true"``/``"false"`` for a checkbox, the option text for a combobox."""

    __sensitive_fields__: ClassVar[tuple[str, ...]] = ("value",)

    target: Ref
    kind: FieldKind
    value: str
    sensitive: bool = False


@dataclass(frozen=True)
class FillForm(Action):
    effect: ClassVar[Effect] = Effect.ACT

    fields: tuple[FormField, ...]


@dataclass(frozen=True)
class Drop(Action):
    """Drop files (``paths``) and/or data (``(mime type, value)`` pairs) onto ``target``. At least one is set."""

    effect: ClassVar[Effect] = Effect.ACT

    target: Ref
    paths: tuple[str, ...] = ()
    data: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.paths and not self.data:
            raise ValueError("Drop needs paths or data")


@dataclass(frozen=True)
class GetPdf(Action):
    """Save the page as PDF; ``ActionResult.value`` is the saved path."""

    effect: ClassVar[Effect] = Effect.READ

    filename: str | None = None


@dataclass(frozen=True)
class GetConsole(Action):
    """Console messages at ``level`` and more severe; ``all`` includes those before the last navigation."""

    effect: ClassVar[Effect] = Effect.READ

    level: str = "info"
    all: bool = False


@dataclass(frozen=True)
class GetNetwork(Action):
    """Network requests; ``static`` includes successful static resources, ``filter`` is a URL regexp."""

    effect: ClassVar[Effect] = Effect.READ

    static: bool = False
    filter: str | None = None
