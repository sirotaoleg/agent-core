#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import pytest

from openjiuwen.core.common.logging import tool_call
from openjiuwen.core.common.logging.browser_context import (
    reset_browser_agent_log_context,
    set_browser_agent_log_context,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime import browser_logging
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
    configure_browser_agent_logging,
    get_browser_agent_logger,
    write_browser_agent_audit_artifact,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import CoreSettings


def _reset(browser_logger: logging.Logger) -> None:
    for handler in list(browser_logger.handlers):
        if getattr(handler, browser_logging._BROWSER_HANDLER_MARKER, False):
            browser_logger.removeHandler(handler)
            handler.close()
    vars(browser_logger).pop(browser_logging._AUDIT_RAW_ATTR, None)


@pytest.fixture
def browser_logger(monkeypatch):
    monkeypatch.setattr(tool_call, "_TOOL_CALL_LOGGERS", {})
    browser_logger = logging.getLogger("openjiuwen.browser_agent")
    level, propagate = browser_logger.level, browser_logger.propagate
    _reset(browser_logger)
    yield browser_logger
    _reset(browser_logger)
    browser_logger.setLevel(level)
    browser_logger.propagate = propagate


class _Recorder:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(message % args)


def _file_handlers(browser_logger: logging.Logger) -> list[str]:
    return [h.baseFilename for h in browser_logger.handlers if isinstance(h, logging.FileHandler)]


def test_settings_choose_file_level_and_mirroring(browser_logger, tmp_path: Path):
    log_file = tmp_path / "browser.log"

    configure_browser_agent_logging(
        CoreSettings(log_file=log_file, log_level=logging.WARNING, log_mirror_common=True)
    )
    browser_agent_log_info("quiet %s", 1)
    browser_agent_log_warning("loud %s", 2)

    assert browser_logger.level == logging.WARNING
    assert browser_logger.propagate is True
    text = log_file.read_text(encoding="utf-8")
    assert "| browser_agent | WARNING | loud 2" in text
    assert "quiet" not in text


def test_reconfiguring_moves_the_log_file(browser_logger, tmp_path: Path):
    first, second = tmp_path / "first.log", tmp_path / "second.log"

    configure_browser_agent_logging(CoreSettings(log_file=first))
    configure_browser_agent_logging(CoreSettings(log_file=second))
    browser_agent_log_info("after move")

    assert _file_handlers(browser_logger) == [str(second.resolve())]
    assert "after move" in second.read_text(encoding="utf-8")
    assert "after move" not in first.read_text(encoding="utf-8")


def test_disabled_log_file_falls_back_to_common_logger(browser_logger, monkeypatch):
    common = _Recorder()
    monkeypatch.setattr(browser_logging, "common_logger", common)

    configure_browser_agent_logging(CoreSettings(log_file=None))
    browser_agent_log_info("no file %s", "here")

    assert _file_handlers(browser_logger) == []
    assert common.lines == ["no file here"]


def test_unconfigured_logger_takes_the_process_settings(browser_logger, tmp_path: Path, monkeypatch):
    log_file = tmp_path / "from_env.log"
    monkeypatch.setenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", str(log_file))

    get_browser_agent_logger()

    assert _file_handlers(browser_logger) == [str(log_file.resolve())]


def test_configuration_routes_browser_tool_calls_to_browser_log(browser_logger, tmp_path: Path):
    log_file = tmp_path / "browser.log"

    configure_browser_agent_logging(CoreSettings(log_file=log_file))
    tool_call.tool_call_logger("browser_navigate")("Executing tool: %s with args: %s", "browser_navigate", {})

    assert tool_call.tool_call_logger("mcp_playwright_navigate") is browser_agent_log_info
    assert tool_call.tool_call_logger("read_file") is None
    assert "| browser_agent | INFO | Executing tool: browser_navigate with args: {}" in log_file.read_text(
        encoding="utf-8"
    )


def _audit_card_probe() -> dict:
    token = set_browser_agent_log_context(True)
    try:
        return write_browser_agent_audit_artifact("card_probe", {"cards": [{"title": "private raw card"}]})
    finally:
        reset_browser_agent_log_context(token)


def test_raw_browser_observation_is_written_only_to_audit_artifact(browser_logger, tmp_path: Path):
    configure_browser_agent_logging(CoreSettings(log_file=tmp_path / "browser_agent.log", log_audit_raw=True))

    audit = _audit_card_probe()

    artifacts = list((tmp_path / "browser_agent_audit").glob("card_probe_*.json.gz"))
    assert audit["stored"] is True
    assert len(artifacts) == 1
    with gzip.open(artifacts[0], mode="rt", encoding="utf-8") as stream:
        stored = json.load(stream)
    assert "private raw card" in stored["raw"]
    assert "private raw card" not in (tmp_path / "browser_agent.log").read_text(encoding="utf-8")


def test_raw_browser_observation_audit_is_disabled_by_default(browser_logger, tmp_path: Path):
    configure_browser_agent_logging(CoreSettings(log_file=tmp_path / "browser_agent.log"))

    audit = _audit_card_probe()

    assert audit["stored"] is False
    assert not (tmp_path / "browser_agent_audit").exists()


def test_first_use_reads_only_the_log_settings(browser_logger, monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_DRIVER", "not-a-driver")
    monkeypatch.setenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE", str(tmp_path / "browser.log"))
    monkeypatch.setenv("OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL", "WARNING")

    assert get_browser_agent_logger() is browser_logger
    assert browser_logger.level == logging.WARNING
    assert _file_handlers(browser_logger) == [str((tmp_path / "browser.log").resolve())]
