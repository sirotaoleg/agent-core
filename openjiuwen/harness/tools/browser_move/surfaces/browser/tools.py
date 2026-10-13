# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The browser-dialect tools: names, descriptions and schemas as the model knows them, and their argument decoders."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Iterable, Optional

from openjiuwen.harness.tools.browser_move.contract import (
    Action,
    Click,
    CloseContext,
    ContextRef,
    Drag,
    DriverRef,
    HandleDialog,
    Hover,
    Locator,
    Observe,
    PressKey,
    Ref,
    RefResolver,
    RunScript,
    Scroll,
    SwitchContext,
    ToolSpec,
    TypeText,
)
from openjiuwen.harness.tools.browser_move.surfaces.browser.commands import (
    Back,
    Drop,
    FieldKind,
    FillForm,
    FormField,
    GetConsole,
    GetNetwork,
    GetPdf,
    Navigate,
    OpenContext,
    SelectOption,
    UploadFile,
)

_TRUE = frozenset({"1", "true", "yes"})
_FALSE = frozenset({"0", "false", "no"})
_CHECKED = frozenset({"1", "true", "yes", "on", "checked"})
_FIELD_KINDS = {
    "textbox": FieldKind.TEXTBOX,
    "spinbutton": FieldKind.TEXTBOX,
    "searchbox": FieldKind.TEXTBOX,
    "slider": FieldKind.SLIDER,
    "combobox": FieldKind.COMBOBOX,
    "listbox": FieldKind.COMBOBOX,
    "select": FieldKind.COMBOBOX,
    "checkbox": FieldKind.CHECKBOX,
    "switch": FieldKind.CHECKBOX,
    "radio": FieldKind.RADIO,
}
_TAB_ACTIONS = ("list", "new", "select", "close")

_ELEMENT_TARGET_PROPERTIES: dict[str, Any] = {
    "generation_id": {
        "type": "string",
        "description": "Current PageState generation_id for the element target.",
    },
    "target_id": {
        "type": "string",
        "description": "Generation-scoped target_id from PageState / probes.",
    },
    "ref": {
        "type": "string",
        "description": "Element ref from the current page observation.",
    },
    "selector": {
        "type": "string",
        "description": "CSS selector when a validated selector is already known.",
    },
}

_NAVIGATE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Absolute URL to open, e.g. https://example.com",
        },
        "wait_until": {
            "type": "string",
            "description": "Navigation wait condition. Default load.",
        },
        "timeout_ms": {
            "type": "integer",
            "description": "Optional navigation timeout in milliseconds.",
        },
    },
    "required": ["url"],
}

_NO_PARAMS: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

_CLICK_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_ELEMENT_TARGET_PROPERTIES,
        "button": {
            "type": "string",
            "description": "Mouse button: left, right, or middle. Default left.",
        },
        "click_count": {
            "type": "integer",
            "description": "Number of clicks. Default 1.",
        },
    },
    "required": ["generation_id"],
}

_TYPE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_ELEMENT_TARGET_PROPERTIES,
        "text": {
            "type": "string",
            "description": "Text to type into the element.",
        },
        "clear": {
            "type": "boolean",
            "description": "Clear existing content before typing. Default true.",
        },
        "press_enter": {
            "type": "boolean",
            "description": "Press Enter after typing. Default false.",
        },
        "sensitive": {
            "type": "boolean",
            "description": "Mark input as sensitive (e.g. password). Default false.",
        },
    },
    "required": ["generation_id", "text"],
}

_PRESS_KEY_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Key or combination, e.g. Enter, Control+a, Escape.",
        },
    },
    "required": ["key"],
}

_SCREENSHOT_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_page": {
            "type": "boolean",
            "description": "Capture the full scrollable page. Default false.",
        },
    },
    "required": [],
}

_TABS_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "Tab action: list, select, or close. Default list.",
            "enum": ["list", "select", "close", "new"],
        },
        "index": {
            "type": "integer",
            "description": "Zero-based tab index for select/close.",
        },
    },
    "required": [],
}

_SELECT_OPTION_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_ELEMENT_TARGET_PROPERTIES,
        "value": {
            "type": "string",
            "description": "Option value attribute to select.",
        },
        "label": {
            "type": "string",
            "description": "Visible option label to select.",
        },
    },
    "required": ["generation_id"],
}

_EVALUATE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "function": {
            "type": "string",
            "description": "JavaScript source to evaluate (expression or function body).",
        },
        "args": {
            "description": "Optional JSON-serializable argument passed to the script.",
        },
    },
    "required": ["function"],
}

_DRAG_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "generation_id": _ELEMENT_TARGET_PROPERTIES["generation_id"],
        "source_target_id": {
            "type": "string",
            "description": "Source element target_id from PageState.",
        },
        "source_ref": {
            "type": "string",
            "description": "Source element ref.",
        },
        "source_selector": {
            "type": "string",
            "description": "Source CSS selector.",
        },
        "target_target_id": {
            "type": "string",
            "description": "Target element target_id from PageState.",
        },
        "target_ref": {
            "type": "string",
            "description": "Target element ref.",
        },
        "target_selector": {
            "type": "string",
            "description": "Target CSS selector.",
        },
        "steps": {
            "type": "integer",
            "description": "Intermediate drag steps. Default 10.",
        },
    },
    "required": ["generation_id"],
}

_FILE_UPLOAD_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_ELEMENT_TARGET_PROPERTIES,
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Absolute or workspace-relative file paths to upload. "
                "Relative/bare names also resolve under BROWSER_UPLOAD_ROOT when set "
                "(see list_upload_files)."
            ),
        },
    },
    "required": ["generation_id", "paths"],
}

_HOVER_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_ELEMENT_TARGET_PROPERTIES,
    },
    "required": ["generation_id"],
}

_HANDLE_DIALOG_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accept": {
            "type": "boolean",
            "description": "True to accept/OK, false to dismiss/Cancel.",
        },
        "promptText": {
            "type": "string",
            "description": "Optional text to submit for a prompt() dialog.",
        },
        "prompt_text": {
            "type": "string",
            "description": "Alias for promptText.",
        },
    },
    "required": ["accept"],
}

_DROP_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_ELEMENT_TARGET_PROPERTIES,
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Absolute or workspace-relative file paths to drop. "
                "Resolved like browser_file_upload (BROWSER_UPLOAD_ROOT)."
            ),
        },
        "data": {
            "type": "array",
            "description": "MIME-typed drag data items (mimeType + data).",
            "items": {
                "type": "object",
                "properties": {
                    "mimeType": {"type": "string"},
                    "mime_type": {"type": "string"},
                    "data": {"type": "string"},
                },
            },
        },
    },
    "required": ["generation_id"],
}

_FILL_FORM_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "generation_id": _ELEMENT_TARGET_PROPERTIES["generation_id"],
        "fields": {
            "type": "array",
            "description": "Ordered field fill steps.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Field type: textbox, combobox/select, checkbox/radio.",
                    },
                    "target_id": {"type": "string"},
                    "ref": {"type": "string"},
                    "selector": {"type": "string"},
                    "value": {},
                    "label": {"type": "string"},
                    "sensitive": {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    "required": ["generation_id", "fields"],
}

_SNAPSHOT_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_screenshot": {
            "type": "boolean",
            "description": "Include a screenshot in the observation. Default false.",
        },
    },
    "required": [],
}


def _text(args: Mapping[str, Any], name: str) -> str:
    return str(args.get(name) or "").strip()


def _flag(args: Mapping[str, Any], name: str, default: bool) -> bool:
    value = args.get(name, default)
    if isinstance(value, str):
        word = value.strip().lower()
        return True if word in _TRUE else False if word in _FALSE else default
    return bool(value)


def _integer(args: Mapping[str, Any], name: str, default: int | None) -> int | None:
    value = args.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be an integer") from None


def _generation(args: Mapping[str, Any]) -> str:
    generation = _text(args, "generation_id")
    if not generation:
        raise ValueError("'generation_id' is required")
    return generation


def _target(args: Mapping[str, Any], resolver: RefResolver, generation: str, prefix: str = "") -> Ref:
    target_id, ref, selector = (_text(args, prefix + name) for name in ("target_id", "ref", "selector"))
    if target_id:
        return resolver.resolve(target_id, generation)
    if ref:
        return DriverRef(ref, generation)
    if selector:
        return Locator(css=selector, text=None, generation=generation)
    raise ValueError(f"exactly one of {prefix}target_id, {prefix}ref, or {prefix}selector is required")


def _element(args: Mapping[str, Any], resolver: RefResolver) -> Ref:
    return _target(args, resolver, _generation(args))


def _paths(args: Mapping[str, Any]) -> tuple[str, ...] | None:
    value = args.get("paths")
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("'paths' must be a list of strings")
    return tuple(str(path) for path in value)


def _current_context(resolver: RefResolver) -> ContextRef:
    context = resolver.current_context
    if context is None:
        raise ValueError("no open tab")
    return context


def build_navigate(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    url = _text(args, "url")
    if not url:
        raise ValueError("'url' is required")
    return Navigate(url)


def build_navigate_back(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Back()


def build_click(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    count = _integer(args, "click_count", 1)
    return Click(_element(args, resolver), button=_text(args, "button") or "left", count=max(1, count))


def build_type(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return TypeText(
        _element(args, resolver),
        str(args.get("text") or ""),
        submit=_flag(args, "press_enter", False),
        sensitive=_flag(args, "sensitive", False),
        clear=_flag(args, "clear", True),
    )


def build_press_key(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    key = str(args.get("key") or "")
    if not key:
        raise ValueError("'key' is required")
    return PressKey(key)


def build_take_screenshot(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe(include_screenshot=True, full_page=_flag(args, "full_page", False))


def build_tabs(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    action = _text(args, "action").lower() or "list"
    if action not in _TAB_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(_TAB_ACTIONS)}")
    if action == "list":
        return Observe()
    if action == "new":
        return OpenContext()
    index = _integer(args, "index", None)
    if index is None:
        if action == "select":
            raise ValueError("action=select requires integer index")
        return CloseContext(_current_context(resolver))
    contexts = resolver.contexts
    if not 0 <= index < len(contexts):
        raise ValueError(f"tab index {index} is out of range: {len(contexts)} tab(s) open")
    return SwitchContext(contexts[index]) if action == "select" else CloseContext(contexts[index])


def build_close(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return CloseContext(_current_context(resolver))


def build_select_option(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    value = _text(args, "value") or _text(args, "label")
    if not value:
        raise ValueError("exactly one of value or label is required")
    return SelectOption(_element(args, resolver), (value,))


def build_evaluate(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    code = str(args.get("function") or args.get("source") or "")
    if not code:
        raise ValueError("'function' is required")
    return RunScript(code, args.get("args"))


def build_drag(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    generation = _generation(args)
    _integer(args, "steps", None)
    return Drag(
        _target(args, resolver, generation, prefix="source_"), _target(args, resolver, generation, prefix="target_")
    )


def build_file_upload(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    paths = _paths(args)
    if paths is None:
        raise ValueError("'paths' must be a list of strings")
    return UploadFile(_element(args, resolver), paths)


def build_hover(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Hover(_element(args, resolver))


def build_handle_dialog(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    if "accept" not in args:
        raise ValueError("'accept' is required")
    prompt = args.get("promptText", args.get("prompt_text"))
    return HandleDialog(_flag(args, "accept", False), None if prompt is None else str(prompt))


def build_drop(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    target = _element(args, resolver)
    raw_data = args.get("data")
    data: tuple[tuple[str, str], ...] = ()
    if raw_data is not None:
        if not isinstance(raw_data, list):
            raise ValueError("'data' must be a list of objects")
        if not all(isinstance(item, Mapping) for item in raw_data):
            raise ValueError("'data' items must be objects")
        data = tuple(
            (str(item.get("mimeType") or item.get("mime_type") or ""), str(item.get("data") or "")) for item in raw_data
        )
    return Drop(target, paths=_paths(args) or (), data=data)


def build_fill_form(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    generation = _generation(args)
    raw_fields = args.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("'fields' must be a non-empty list")
    fields = []
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, Mapping):
            raise ValueError(f"fields[{index}] must be an object")
        name = _text(raw, "type").lower() or "textbox"
        kind = _FIELD_KINDS.get(name)
        if kind is None:
            raise ValueError(f"fields[{index}] unsupported type={name!r}")
        value = raw.get("value")
        if kind in (FieldKind.CHECKBOX, FieldKind.RADIO):
            checked = value if isinstance(value, bool) else str(value).strip().lower() in _CHECKED
            text = "true" if checked else "false"
        elif kind is FieldKind.COMBOBOX:
            text = "" if value in (None, "") else str(value)
            text = text or _text(raw, "label")
        else:
            text = "" if value is None else str(value)
        target = _target(raw, resolver, generation)
        fields.append(FormField(target, kind, text, sensitive=_flag(raw, "sensitive", False)))
    return FillForm(tuple(fields))


def build_snapshot(args: Mapping[str, Any], resolver: RefResolver) -> Action:
    return Observe(include_screenshot=_flag(args, "include_screenshot", False))


BROWSER_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("browser_navigate", (Navigate,), "Navigate to a URL.", schema=_NAVIGATE_PARAMS, build=build_navigate),
    ToolSpec(
        "browser_navigate_back",
        (Back,),
        "Navigate back in browser history.",
        schema=_NO_PARAMS,
        build=build_navigate_back,
    ),
    ToolSpec("browser_click", (Click,), "Click an element.", schema=_CLICK_PARAMS, build=build_click),
    ToolSpec("browser_type", (TypeText,), "Type text into an element.", schema=_TYPE_PARAMS, build=build_type),
    ToolSpec(
        "browser_press_key",
        (PressKey,),
        "Press a keyboard key or key combination.",
        schema=_PRESS_KEY_PARAMS,
        build=build_press_key,
    ),
    ToolSpec(
        "browser_take_screenshot",
        (Observe,),
        "Take a screenshot of the current page.",
        schema=_SCREENSHOT_PARAMS,
        build=build_take_screenshot,
    ),
    ToolSpec(
        "browser_tabs",
        (Observe, OpenContext, SwitchContext, CloseContext),
        "List, select, or close browser tabs.",
        schema=_TABS_PARAMS,
        build=build_tabs,
    ),
    ToolSpec("browser_close", (CloseContext,), "Close the active browser tab.", schema=_NO_PARAMS, build=build_close),
    ToolSpec(
        "browser_select_option",
        (SelectOption,),
        "Select an option from a native select element.",
        schema=_SELECT_OPTION_PARAMS,
        build=build_select_option,
    ),
    ToolSpec(
        "browser_evaluate",
        (RunScript,),
        "Evaluate a small JavaScript expression in the page. Do not dump the full document.",
        schema=_EVALUATE_PARAMS,
        build=build_evaluate,
    ),
    ToolSpec(
        "browser_drag",
        (Drag,),
        "Drag from a source element to a target element.",
        schema=_DRAG_PARAMS,
        build=build_drag,
    ),
    ToolSpec(
        "browser_file_upload",
        (UploadFile,),
        "Upload one or more files to a file input element.",
        schema=_FILE_UPLOAD_PARAMS,
        build=build_file_upload,
    ),
    ToolSpec(
        "browser_hover",
        (Hover,),
        "Hover the mouse over an element (tooltips / :hover menus).",
        schema=_HOVER_PARAMS,
        build=build_hover,
    ),
    ToolSpec(
        "browser_handle_dialog",
        (HandleDialog,),
        "Accept or dismiss a JavaScript alert/confirm/prompt. "
        "If no dialog is open yet, arms handling for the next dialog — "
        "call this before the click/type that opens it.",
        schema=_HANDLE_DIALOG_PARAMS,
        build=build_handle_dialog,
    ),
    ToolSpec(
        "browser_drop",
        (Drop,),
        "Drop local files and/or MIME-typed data onto an element as if dragged from "
        "outside the page. Distinct from browser_drag (element→element). "
        "At least one of paths or data is required. Supported MIME types: "
        "text/plain, text/uri-list, text/html.",
        schema=_DROP_PARAMS,
        build=build_drop,
    ),
    ToolSpec(
        "browser_fill_form",
        (FillForm,),
        "Fill multiple form fields in one call (type / select / checkbox).",
        schema=_FILL_FORM_PARAMS,
        build=build_fill_form,
    ),
    ToolSpec(
        "browser_snapshot",
        (Observe,),
        "Capture an accessibility/page observation of the current page.",
        schema=_SNAPSHOT_PARAMS,
        build=build_snapshot,
    ),
    ToolSpec("browser_mouse_click_xy", (Click,)),
    ToolSpec("browser_mouse_move_xy", (Hover,)),
    ToolSpec("browser_mouse_drag_xy", (Drag,)),
    ToolSpec("browser_mouse_wheel", (Scroll,)),
    ToolSpec("browser_pdf_save", (GetPdf,)),
    ToolSpec("browser_console_messages", (GetConsole,)),
    ToolSpec("browser_network_requests", (GetNetwork,)),
)

_BROWSER_IMAGE_CAPABILITY_SECTION_NAME = "browser_image_input_capability"
_BROWSER_DATE_PICKER_SECTION_NAME = "browser_date_picker_guidance"
_BROWSER_DATE_PICKER_GUIDANCE = {
    "en": (
        "For calendar/date-picker widgets, do not type the date into a text field. Click the "
        'specific day cell instead (look for a data-iso="YYYY-MM-DD" attribute or an aria-label '
        "naming the full date), then confirm the selection before proceeding."
    ),
    "cn": (
        "对于日历/日期选择器组件，不要向文本框输入日期，而应点击对应的日期单元格"
        '（查找 data-iso="YYYY-MM-DD" 属性或包含完整日期的 aria-label），'
        "确认选中后再继续。"
    ),
}
_BROWSER_TASK_RULES_SECTION_NAME = "browser_task_rules"
_BROWSER_TASK_RULES = {
    "en": (
        "Task rules:\n"
        "- Today's date is {today}. A date without a year means its next occurrence on or after today.\n"
        "- Apply every filter, category and option the task names (a property type, a class year, a price range) "
        "before reading results.\n"
        "- Check on the page what is today's, the latest or the newest; never answer that from memory.\n"
        "- When the task names a source, open that source and read the answer there.\n"
        "- Perform every UI step the task names, even when the answer is reachable another way.\n"
        "- When the question has two readings, answer both.\n"
        "- When the site misreads an entity, query it again disambiguated (full name, kind or unit).\n"
        "- When the page shows the answer, state it plainly, without caveats that undermine it.\n"
        "- Do not retry a hand-built search URL more than twice; open a candidate result instead."
    ),
    "cn": (
        "任务规则：\n"
        "- 今天的日期是 {today}。未写年份的日期指今天或之后最近的那一天。\n"
        "- 读取结果前，应用任务点名的每一个筛选、类别和选项（如住宿类型、年级、价格区间）。\n"
        "- “今天”“最新”“最近”的内容要在页面上核实，不要凭记忆回答。\n"
        "- 任务点名了来源时，打开该来源并在那里读取答案。\n"
        "- 任务点名的每个界面操作都要执行，即使答案可以通过别的途径得到。\n"
        "- 问题有两种理解时，两种都回答。\n"
        "- 网站误解了某个实体时，用消歧后的写法重新查询（全名、类别或单位）。\n"
        "- 页面上已有答案时直接给出，不要附加削弱答案的保留说明。\n"
        "- 手工拼接的搜索 URL 最多重试两次，之后改为打开一个候选结果。"
    ),
}


def browser_task_rules() -> dict[str, str]:
    """The task rules per language, with today's date."""
    today = datetime.date.today().isoformat()
    return {language: text.format(today=today) for language, text in _BROWSER_TASK_RULES.items()}


_BROWSER_IMAGE_CAPABILITY_GUIDANCE = {
    True: {
        "en": (
            "The current model can inspect image input. Use browser_take_screenshot only when "
            "pixel-level visual evidence is required and DOM probes, accessibility snapshots, "
            "or targeted evaluation cannot answer the task."
        ),
        "cn": (
            "当前模型可以理解图片输入。仅当任务需要像素级视觉证据，且 DOM 探测、无障碍快照或"
            "定向脚本无法解决时，才使用 browser_take_screenshot。"
        ),
    },
    False: {
        "en": (
            "Image input is unavailable or unverified for this run. Do not request screenshots, "
            "including browser_take_screenshot or browser_batch_interact with op=screenshot. "
            "Use DOM probes, accessibility snapshots, and targeted evaluation instead."
        ),
        "cn": (
            "本次运行的图片输入能力不可用或尚未确认。不要请求截图，包括 "
            "browser_take_screenshot 或 browser_batch_interact 的 op=screenshot；"
            "请改用 DOM 探测、无障碍快照和定向脚本。"
        ),
    },
}


_BROWSER_WORKER_HELPER_TOOL_NAMES = (
    "browser_probe_interactives",
    "browser_probe_cards",
    "browser_batch_interact",
    "browser_custom_action",
    "browser_list_custom_actions",
)


def build_browser_worker_system_prompt(
    screenshot_subdir: str = "screenshots",
    artifacts_subdir: str = "artifacts",
    *,
    tool_names: Optional[Iterable[str]] = None,
    driver_name: str,
) -> str:
    """Render the worker system prompt; helper guidance only for helpers in ``tool_names`` (``None``: all)."""
    available = set(_BROWSER_WORKER_HELPER_TOOL_NAMES if tool_names is None else tool_names)
    interactives = "browser_probe_interactives" in available
    cards = "browser_probe_cards" in available
    probes = interactives or cards
    batch = "browser_batch_interact" in available
    batch_vs_custom_action = batch and interactives and cards and "browser_custom_action" in available
    helper_guidance = "".join(
        text
        for enabled, text in (
            (
                probes,
                "Before broad page snapshots, full-body scans, or generic DOM scraping, "
                "choose the smallest compact probe that matches the task. ",
            ),
            (
                interactives,
                "Use browser_probe_interactives for buttons, links, inputs, forms, navigation controls, "
                "login controls, pagination controls, menus, and other visible interactive elements. "
                "When using browser_probe_interactives only for page-level controls, prefer max_items around 20-30 "
                "unless the task explicitly requires a larger inventory. ",
            ),
            (
                cards,
                "On product pages, marketplace pages, search-result pages, catalog pages, article-list pages, "
                "or any page with repeated visible cards/listings, call browser_probe_cards before broad extraction. "
                "Use browser_probe_cards to identify compact repeated structures such as product cards, result cards, "
                "book cards, article cards, listing rows, title/author/source/summary, "
                "title/price/rating/review/availability fields, primary links, "
                "visible controls, generation-scoped target IDs, and recurring structure signatures. ",
            ),
            (
                cards and interactives,
                "For product/listing/item-data tasks, prefer browser_probe_cards first; call "
                "browser_probe_interactives "
                "only if you also need page-level navigation, filters, forms, or controls outside the cards. ",
            ),
            (
                cards,
                "If browser_probe_cards returns the fields needed for the task, including article/result-row "
                "title, link, author/source, or summary fields, use that compact result as the evidence "
                "for extraction and final reporting instead of taking screenshots, snapshots, or running extra DOM "
                "scans. "
                "After a successful card probe, use browser_evaluate, or a browser_run_code tool only when it is "
                "explicitly present, for a clearly missing required field or a specific target-based action; "
                "do not repeat broad evaluation "
                "just to re-read the same cards. ",
            ),
            (
                probes,
                "Treat page_id/generation_id/url/title/interactives/cards/"
                "field_coverage/blockers as one PageState contract. ",
            ),
            (
                probes and batch,
                "Pass the current generation_id with a "
                "probe target_id directly to browser_batch_interact; never translate it into guessed CSS. "
                "Explicit selectors are reserved for validated read-only extraction and condition waits. ",
            ),
            (
                cards,
                "If a card has primary_link or href, navigate to it directly. ",
            ),
            (
                batch_vs_custom_action,
                "browser_batch_interact is a standalone runtime helper like browser_probe_interactives and "
                "browser_probe_cards, not a browser_custom_action wrapper. Use it directly. ",
            ),
            (
                batch,
                "For multi-field forms with several known controls, search flows with autocomplete, "
                "dropdown/date-picker "
                "flows, filter panels, or any short sequence where two or more next click/type/wait/extract steps are "
                "already known, call browser_batch_interact before falling back to repeated "
                "browser_click/browser_type and separate wait turns. "
                "Extract multiple fields from the same page in one batch with named extract_text/extract_value "
                "steps, or use one card probe result that already contains every requested field. "
                "Do not force browser_batch_interact for a single uncertain click, one simple text field, or a page "
                "state "
                "that still needs inspection. Prefer generation-scoped target_id in batch steps. "
                "Use autocomplete steps for type-then-choose widgets, and condition-based "
                "wait_for_selector/wait_for_text "
                "steps instead of fixed sleeps. For dynamic pages use wait_for_url, "
                "wait_for_first_card_title, wait_for_sort_state, wait_for_result_count, "
                "wait_for_dom_text_change, wait_for_stable, or wait_for_tab with a short poll interval and total "
                "timeout. "
                "When an action opens a new tab, use click plus wait_for_tab in the same batch; wait_for_tab "
                "activates the matched tab by default. Do not wait for a new URL on the old tab. ",
            ),
            (
                True,
                "Interpret 'fully loaded' or an 'appropriate pause' as condition stability, not a fixed sleep. ",
            ),
            (
                batch,
                "Do not split click+type+wait, click+wait+click, "
                "date open+choose, or search submit+result wait into separate ReAct turns unless "
                "browser_batch_interact "
                "failed or the next target is genuinely unknown. ",
            ),
        )
        if enabled
    )
    return (
        "You are a browser worker agent.\n"
        f"Execute browser tasks step-by-step with {driver_name} tools and approved runtime helper tools only.\n"
        "Before interacting, ensure page or selector readiness.\n"
        "Keep actions targeted and avoid unnecessary page snapshots.\n"
        f"{helper_guidance}"
        "Keep using browser_fill_form for ordinary visible text fields when that official tool is enough. "
        "Use browser_snapshot only when the compact probes are insufficient, when accessibility structure is needed, "
        f"or when you need exact element references required by a {driver_name} action. "
        "Use browser_evaluate for small exact computations. Use a browser_run_code tool only when it is present "
        "in the current tool list and compact probes, evaluate, snapshot, and deterministic actions are "
        "insufficient. Never use run-code to dump the entire document body.\n"
        "If actions repeatedly fail, stop and report the exact failing action.\n"
        "If you use browser_tabs, action MUST be one of: list, new, close, select.\n"
        "Use specialized operations only when their explicit primitive tool is present in the current tool list. "
        "Do not launch nested browser tasks from the browser worker. If the available tools cannot complete an "
        "operation, report the exact blocker instead of inventing a tool name.\n"
        "IMPORTANT: Do NOT use browser_take_screenshot unless strictly necessary. "
        f"If a screenshot is needed, always save it under '{screenshot_subdir}/'. "
        "Use browser_take_screenshot when it is present; do not create screenshots through run-code.\n"
        f"If you produce any output files (reports, notes, summaries, markdown, text files, etc.), "
        f"write them to the '{artifacts_subdir}/' directory relative to the working directory. "
        "Never write output files to the project root or any other location.\n"
        f"{browser_task_rules()['en']}\n"
        "Final output MUST be a single JSON object with keys:\n"
        "ok (boolean), final (string), page (object with url and title), "
        "screenshot (string|null), error (string|null).\n"
        "Also include status (completed|partial|blocked|failed) whenever possible. "
        "If the task is not fully complete, include progress as an object with "
        "completed_steps, remaining_steps, next_step, completion_evidence, and missing_requirements.\n"
        "Set ok=true only when the exact user-visible goal is fully satisfied and you can cite concrete evidence "
        "from the page, compact probe results, browser snapshots, screenshots, or generated artifacts. "
        "If the task is incomplete or blocked, set ok=false and fill the progress fields so a continuation can "
        "resume with minimal repetition.\n"
        "Return JSON only, even on failures. "
        "Do not output markdown, code fences, or plain text outside the JSON object."
    )
