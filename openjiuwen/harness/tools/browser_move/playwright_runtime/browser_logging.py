# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from pathlib import Path
from threading import Lock
from typing import Any

from openjiuwen.core.common.logging import logger as common_logger
from openjiuwen.core.common.logging.browser_context import is_browser_agent_log_context
from openjiuwen.core.common.logging.tool_call import register_tool_call_logger

from .config import CoreSettings

_BROWSER_LOGGER_NAME = "openjiuwen.browser_agent"
_BROWSER_HANDLER_MARKER = "_openjiuwen_browser_agent_file_handler"
_BROWSER_LOG_ANNOUNCED_MARKER = "_openjiuwen_browser_agent_file_announced"
# The browser logger is process-wide (stdlib registry), so the settings it was configured with live on it.
_AUDIT_RAW_ATTR = "_openjiuwen_browser_agent_audit_raw"
_LOCK = Lock()
_AUDIT_LOCK = Lock()


def _is_browser_tool_name(tool_name: str) -> bool:
    lowered_name = tool_name.lower()
    return "browser_" in lowered_name or lowered_name.startswith("mcp_playwright")


def configure_browser_agent_logging(settings: CoreSettings) -> logging.Logger:
    """Apply the logging part of ``settings`` and send browser tool-call lines to the browser-agent log."""
    browser_logger = _apply_settings(settings)
    register_tool_call_logger(_is_browser_tool_name, browser_agent_log_info)
    return browser_logger


def get_browser_agent_logger() -> logging.Logger:
    """Return the dedicated browser-agent logger, configured from the process settings until configured explicitly."""
    browser_logger = logging.getLogger(_BROWSER_LOGGER_NAME)
    if not hasattr(browser_logger, _AUDIT_RAW_ATTR):
        _apply_settings(CoreSettings.from_process_env())
    return browser_logger


def _apply_settings(settings: CoreSettings) -> logging.Logger:
    browser_logger = logging.getLogger(_BROWSER_LOGGER_NAME)
    log_path = None if settings.log_file is None else settings.log_file.expanduser().resolve()
    with _LOCK:
        browser_logger.setLevel(settings.log_level)
        browser_logger.propagate = settings.log_mirror_common
        kept = False
        for handler in list(browser_logger.handlers):
            if not getattr(handler, _BROWSER_HANDLER_MARKER, False):
                continue
            if log_path is not None and getattr(handler, "baseFilename", None) == str(log_path):
                handler.setLevel(settings.log_level)
                kept = True
                continue
            browser_logger.removeHandler(handler)
            handler.close()
        if log_path is not None and not kept:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
            setattr(file_handler, _BROWSER_HANDLER_MARKER, True)
            file_handler.setLevel(settings.log_level)
            file_handler.setFormatter(logging.Formatter("%(asctime)s | browser_agent | %(levelname)s | %(message)s"))
            browser_logger.addHandler(file_handler)
            if not getattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, False):
                common_logger.info("[BROWSER_AGENT_LOG] dedicated browser log file enabled: %s", str(log_path))
                setattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, True)
        setattr(browser_logger, _AUDIT_RAW_ATTR, settings.log_audit_raw)
    return browser_logger


def _log_path(browser_logger: logging.Logger) -> Path | None:
    for handler in browser_logger.handlers:
        if getattr(handler, _BROWSER_HANDLER_MARKER, False):
            return Path(handler.baseFilename)
    return None


def browser_agent_log_info(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.info(message, *args)

    if _log_path(browser_logger) is None:
        common_logger.info(message, *args)


def browser_agent_log_warning(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.warning(message, *args)

    if _log_path(browser_logger) is None:
        common_logger.warning(message, *args)


def browser_agent_log_error(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.error(message, *args)

    if _log_path(browser_logger) is None:
        common_logger.error(message, *args)


def write_browser_agent_audit_artifact(kind: str, raw: Any) -> dict[str, Any]:
    """Persist an opted-in, content-addressed raw browser observation."""
    if isinstance(raw, str):
        raw_text = raw
    else:
        raw_text = json.dumps(raw, ensure_ascii=False, default=str)
    raw_bytes = raw_text.encode("utf-8", "ignore")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    audit = {
        "raw_size_bytes": len(raw_bytes),
        "raw_sha256": digest[:16],
        "stored": False,
    }
    browser_logger = get_browser_agent_logger()
    if not is_browser_agent_log_context() or not getattr(browser_logger, _AUDIT_RAW_ATTR):
        return audit

    log_path = _log_path(browser_logger)
    if log_path is None:
        return audit
    safe_kind = re.sub(r"[^a-z0-9_-]+", "_", str(kind or "observation").lower()).strip("_")
    safe_kind = safe_kind or "observation"
    artifact_name = f"{safe_kind}_{digest[:16]}.json.gz"
    artifact_path = log_path.parent / "browser_agent_audit" / artifact_name
    try:
        with _AUDIT_LOCK:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            if not artifact_path.exists():
                with gzip.open(artifact_path, mode="wt", encoding="utf-8") as stream:
                    json.dump(
                        {"kind": safe_kind, "sha256": digest, "raw": raw_text},
                        stream,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
        audit["stored"] = True
        browser_logger.info(
            "[BROWSER_AUDIT] kind=%s sha256=%s size=%s artifact=%s",
            safe_kind,
            digest[:16],
            len(raw_bytes),
            artifact_name,
        )
    except OSError as exc:
        browser_logger.warning(
            "[BROWSER_AUDIT_ERROR] kind=%s sha256=%s error=%s",
            safe_kind,
            digest[:16],
            exc,
        )
    return audit
