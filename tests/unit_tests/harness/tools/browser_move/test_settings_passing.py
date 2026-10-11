# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Entry points build settings once; everything below them uses the settings it is given, not the process env."""

from __future__ import annotations

import asyncio
import platform
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.drivers.browser_use import transport as transport_module
from openjiuwen.harness.tools.browser_move.drivers.browser_use.driver import create_browser_use_driver
from openjiuwen.harness.tools.browser_move.drivers.browser_use.sidecar.exceptions import DriverUnsupported
from openjiuwen.harness.tools.browser_move.playwright_runtime import main as main_module
from openjiuwen.harness.tools.browser_move.playwright_runtime import site_profiles
from openjiuwen.harness.tools.browser_move.playwright_runtime.agents import (
    build_browser_worker_agent,
    ensure_execute_signature_compat,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_tools import build_browser_runtime_mcp_config
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserRunGuardrails,
    CoreSettings,
    RuntimeServerSettings,
    build_runtime_settings,
    read_demo_inputs,
    runtime_server_child_env,
    runtime_server_environ,
)
from openjiuwen.harness.tools.browser_move.contract.testing import FakeDriver
from openjiuwen.harness.tools.browser_move.playwright_runtime import service as service_module
from openjiuwen.harness.tools.browser_move.playwright_runtime.assembly import DriverAssembly
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.surfaces.browser.profile import create_browser_surface
from openjiuwen.harness.tools.browser_move.playwright_runtime.status_logging import (
    is_browser_subagent_status_log_enabled,
)
from openjiuwen.harness.tools.browser_move.provisioning import ProvisionedDevice
from openjiuwen.harness.tools.browser_move.provisioning.managed_browser import _default_chrome_user_data_dir
from openjiuwen.harness.tools.browser_move.surfaces.browser.config import BrowserSettings
from openjiuwen.harness.tools.browser_move.utils.upload_paths import resolve_upload_file_paths


def _mcp_cfg() -> McpServerConfig:
    return McpServerConfig(
        server_id="test-playwright-runtime",
        server_name="test-playwright-runtime",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )


def _guardrails() -> BrowserRunGuardrails:
    return BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False)


class TestRuntimeServerSettings:
    def test_client_reads_playwright_names_before_browser_aliases(self) -> None:
        settings = RuntimeServerSettings.from_env(
            {
                "PLAYWRIGHT_RUNTIME_MCP_HOST": "pw-host",
                "BROWSER_RUNTIME_MCP_HOST": "alias-host",
                "BROWSER_RUNTIME_MCP_PORT": "9001",
                "BROWSER_RUNTIME_MCP_ENABLED": "yes",
                "BROWSER_RUNTIME_LOG_DIR": "/tmp/rt-logs",
            }
        )
        assert (settings.host, settings.port, settings.enabled) == ("pw-host", "9001", True)
        assert settings.log_dir == Path("/tmp/rt-logs")

    def test_client_defaults(self) -> None:
        settings = RuntimeServerSettings.from_env({})
        assert settings == RuntimeServerSettings()
        assert (settings.client_type, settings.timeout_s, settings.auto_http_fallback) == ("streamable-http", 300, True)

    @pytest.mark.parametrize(("raw", "expected"), [("0", 0), ("abc", 0), ("45", 45)])
    def test_client_timeout_that_is_not_an_int_disables_the_timeout(self, raw: str, expected: int) -> None:
        assert RuntimeServerSettings.from_env({"PLAYWRIGHT_RUNTIME_MCP_TIMEOUT_S": raw}).timeout_s == expected

    @pytest.mark.parametrize(("raw", "expected"), [("", True), ("0", False), ("maybe", False), ("on", True)])
    def test_client_auto_http_fallback(self, raw: str, expected: bool) -> None:
        settings = RuntimeServerSettings.from_env({"PLAYWRIGHT_RUNTIME_MCP_AUTO_HTTP_FALLBACK": raw})
        assert settings.auto_http_fallback is expected

    def test_server_ignores_browser_aliases_except_the_log_dir(self) -> None:
        settings = RuntimeServerSettings.server_from_env(
            {
                "BROWSER_RUNTIME_MCP_HOST": "alias-host",
                "BROWSER_RUNTIME_MCP_PATH": "/alias",
                "BROWSER_RUNTIME_LOG_DIR": "/tmp/rt-logs",
                "PLAYWRIGHT_RUNTIME_MCP_TRANSPORT": " SSE ",
            }
        )
        assert (settings.host, settings.path, settings.transport) == ("127.0.0.1", "", "sse")
        assert settings.log_dir == Path("/tmp/rt-logs")

    @pytest.mark.parametrize(("raw", "expected"), [("", None), ("yes", True), ("off", False), ("maybe", None)])
    def test_server_stateless_http_is_tri_state(self, raw: str, expected: bool | None) -> None:
        settings = RuntimeServerSettings.server_from_env({"PLAYWRIGHT_RUNTIME_MCP_STATELESS_HTTP": raw})
        assert settings.stateless_http is expected

    def test_core_settings_nest_the_client_side(self) -> None:
        core = CoreSettings.from_env({"PLAYWRIGHT_RUNTIME_MCP_ENABLED": "1"})
        assert core.runtime_server == RuntimeServerSettings(enabled=True)


class TestEnvironMappings:
    def test_runtime_server_environ_fills_tool_timeout_defaults(self) -> None:
        assert runtime_server_environ({}) == {"BROWSER_TIMEOUT_S": "600", "PLAYWRIGHT_TOOL_TIMEOUT_S": "600"}
        assert runtime_server_environ({"BROWSER_TIMEOUT_S": "90"})["PLAYWRIGHT_TOOL_TIMEOUT_S"] == "90"

    def test_runtime_server_environ_leaves_the_input_alone(self) -> None:
        environ = {"PLAYWRIGHT_TOOL_TIMEOUT_S": "5"}
        assert runtime_server_environ(environ)["PLAYWRIGHT_TOOL_TIMEOUT_S"] == "5"
        assert environ == {"PLAYWRIGHT_TOOL_TIMEOUT_S": "5"}

    def test_child_env_is_built_from_the_given_mapping_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "from-process")
        env = runtime_server_child_env({"API_KEY": "given", "API_BASE": "https://api.example/v1"})
        assert env == {
            "API_KEY": "given",
            "API_BASE": "https://api.example/v1",
            "OPENAI_API_KEY": "given",
            "OPENAI_BASE_URL": "https://api.example/v1",
        }

    def test_read_demo_inputs(self) -> None:
        assert read_demo_inputs({}) == ("", "demo-browser-session")
        assert read_demo_inputs({"AGENT_QUERY": " find x ", "AGENT_SESSION_ID": "s1"}) == ("find x", "s1")


class TestEntryPoints:
    def test_main_builds_settings_once_and_passes_them_to_the_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY", "key")
        monkeypatch.setenv("BROWSER_UPLOAD_ROOT", str(Path.cwd()))
        monkeypatch.setenv("PLAYWRIGHT_TOOL_TIMEOUT_S", "17")
        monkeypatch.setenv("AGENT_QUERY", "exit")
        runtime = MagicMock()
        runtime.ensure_started = AsyncMock()
        runtime.shutdown = AsyncMock()
        runtime.agent_parts.return_value.tools = ()
        runtime_cls = MagicMock(return_value=runtime)
        with (
            patch.object(main_module, "load_repo_dotenv"),
            patch.object(main_module, "BrowserAgentRuntime", runtime_cls),
        ):
            asyncio.run(main_module.main())

        kwargs = runtime_cls.call_args.kwargs
        assert kwargs["core_settings"].tool_timeout_s == 17.0
        assert kwargs["browser_settings"].upload_root == Path.cwd().resolve()
        assert kwargs["api_key"] == "key"
        runtime.run_browser_task.assert_not_called()

    def test_build_runtime_settings_uses_the_given_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_NAME", "from-process")
        monkeypatch.setenv("PLAYWRIGHT_MCP_COMMAND", "from-process")
        settings = build_runtime_settings(
            core_settings=CoreSettings(model_name="given", api_key="k"),
            browser_settings=BrowserSettings(mcp_command="given-npx"),
        )
        assert settings.model_name == "given"
        assert settings.mcp_cfg.params["command"] == "given-npx"

    def test_runtime_passes_its_settings_to_the_service(self) -> None:
        core = CoreSettings(status_log=False)
        browser = BrowserSettings(upload_root=Path.cwd())
        runtime = BrowserAgentRuntime(
            provider="openai",
            api_key="test-key",
            api_base="https://example.invalid/v1",
            model_name="test-model",
            mcp_cfg=_mcp_cfg(),
            guardrails=_guardrails(),
            core_settings=core,
            browser_settings=browser,
        )
        assert runtime.core_settings is core
        assert runtime.service._settings is browser
        assert runtime.service._core_settings is core

    def test_runtime_registers_builtin_actions_with_the_settings_upload_root(self) -> None:
        runtime = BrowserAgentRuntime(
            provider="openai",
            api_key="test-key",
            api_base="https://example.invalid/v1",
            model_name="test-model",
            mcp_cfg=_mcp_cfg(),
            guardrails=_guardrails(),
            browser_settings=BrowserSettings(upload_root=Path.cwd()),
        )
        controller = MagicMock()
        runtime._controller = controller
        bundle = create_browser_surface({})
        device = ProvisionedDevice(kind="chrome", endpoint="", extras={})
        assembly = DriverAssembly(FakeDriver(), device, None, surface=bundle)
        with patch.object(service_module, "assemble_driver", AsyncMock(return_value=assembly)):
            asyncio.run(runtime.ensure_runtime_ready())
        controller.register_builtin_actions.assert_called_once_with(upload_root=Path.cwd())
        assert sorted(name for name, _ in (c.args for c in controller.register_action.call_args_list)) == [
            "browser_drag_and_drop",
            "browser_get_element_coordinates",
            "browser_set_input_files",
        ]


class TestValuesFromSettings:
    def test_browser_runtime_mcp_config_reads_the_given_settings_and_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PLAYWRIGHT_RUNTIME_MCP_ENABLED", "0")
        monkeypatch.setenv("API_KEY", "from-process")
        cfg = build_browser_runtime_mcp_config(
            RuntimeServerSettings(enabled=True, client_type="stdio", timeout_s=0), environ={"API_KEY": "given"}
        )
        assert cfg is not None
        assert cfg.params["env"]["API_KEY"] == "given"
        assert "timeout_s" not in cfg.params

    def test_execute_wrapper_uses_the_given_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLAYWRIGHT_TOOL_TIMEOUT_S", "999")

        async def _hang(**_kwargs):
            await asyncio.sleep(10)

        agent = MagicMock()
        agent.ability_manager.execute = _hang
        ensure_execute_signature_compat(agent, tool_timeout_s=0.01)
        with pytest.raises(RuntimeError, match="timeout_s=0.0"):
            asyncio.run(agent.ability_manager.execute())

    def test_worker_sampling_comes_from_the_given_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BROWSER_WORKER_TEMPERATURE", "1.5")
        worker = MagicMock()
        worker.configure.return_value = worker
        with patch("openjiuwen.harness.tools.browser_move.playwright_runtime.agents.ReActAgent", return_value=worker):
            build_browser_worker_agent(
                provider="openai",
                api_key="key",
                api_base="https://example.invalid/v1",
                model_name="model",
                max_steps=3,
                settings=CoreSettings(worker_temperature=0.7, worker_top_p=0.3),
            )
        model_config = worker.configure.call_args.args[0].model_config_obj
        assert (model_config.temperature, model_config.top_p) == (0.7, 0.3)

    def test_status_log_flag_comes_from_the_given_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BROWSER_SUBAGENT_STATUS_LOG", "1")
        assert is_browser_subagent_status_log_enabled(CoreSettings(status_log=False)) is False

    def test_selector_cache_path_applies_when_the_cache_is_created(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(site_profiles, "_SELECTOR_CACHE", None)
        monkeypatch.setenv("OPENJIUWEN_BROWSER_SELECTOR_CACHE", str(tmp_path / "from-process.json"))
        assert site_profiles.get_selector_cache(tmp_path / "given.json").path == tmp_path / "given.json"
        assert site_profiles.get_selector_cache(tmp_path / "ignored.json").path == tmp_path / "given.json"

    def test_selector_cache_default_path_ignores_the_process_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(site_profiles, "_SELECTOR_CACHE", None)
        monkeypatch.setenv("OPENJIUWEN_BROWSER_SELECTOR_CACHE", "/tmp/from-process.json")
        assert site_profiles.get_selector_cache().path == Path.home() / ".openjiuwen" / "browser_selector_cache.json"

    def test_upload_paths_without_a_root_ignore_the_process_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "staged.txt").write_text("x", encoding="utf-8")
        monkeypatch.setenv("BROWSER_UPLOAD_ROOT", str(tmp_path))
        existing, error = resolve_upload_file_paths(["staged.txt"])
        assert existing == []
        assert error is not None and "not found" in error

    def test_default_chrome_user_data_dir_uses_the_given_local_app_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.name", "nt")
        assert _default_chrome_user_data_dir("C:/Local") == str(Path("C:/Local") / "Google" / "Chrome" / "User Data")


class TestBrowserUseSidecar:
    def test_sidecar_env_is_built_from_the_given_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BROWSER_USE_FROM_PROCESS", "1")
        env = transport_module._sidecar_env(
            {"PATH": "/bin", "BROWSER_USE_LOGGING_LEVEL": "debug", "BROWSER_USE_SIDECAR_PYTHON": "/py", "API_KEY": "k"},
            Path("/uploads"),
        )
        assert env["PATH"] == "/bin"
        assert env["BROWSER_USE_LOGGING_LEVEL"] == "debug"
        assert env["BROWSER_UPLOAD_ROOT"] == str(Path("/uploads"))
        assert {"BROWSER_USE_SIDECAR_PYTHON", "BROWSER_USE_FROM_PROCESS", "API_KEY"}.isdisjoint(env)

    @pytest.mark.skipif(platform.system() != "Windows", reason="the sidecar gets SystemRoot only on Windows")
    def test_sidecar_env_keeps_systemroot_from_an_upper_case_snapshot(self) -> None:
        env = transport_module._sidecar_env({"PATH": "C:/bin", "SYSTEMROOT": "C:/Windows"}, None)

        assert env["SystemRoot"] == "C:/Windows"

    def test_missing_sidecar_python_override_is_unsupported(self, tmp_path: Path) -> None:
        with pytest.raises(DriverUnsupported, match="BROWSER_USE_SIDECAR_PYTHON"):
            transport_module.discover_sidecar_python(tmp_path / "missing-python")

    def test_factory_takes_sidecar_python_and_upload_root_from_surface_settings(self) -> None:
        device = ProvisionedDevice(kind="chrome", endpoint="http://127.0.0.1:9333", extras={}, cleanup=None)
        settings = BrowserSettings(sidecar_python=Path("/py"), upload_root=Path("/uploads"))
        config = create_browser_use_driver(device, settings)._config
        assert (config.cdp_url, config.sidecar_python, config.upload_root) == (
            "http://127.0.0.1:9333",
            Path("/py"),
            Path("/uploads"),
        )
