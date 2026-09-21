# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for AnthropicModelClient shared/cached AsyncAnthropic client lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import AnthropicModelClient
from openjiuwen.core.foundation.llm.schema.config import LLMAuthMode


def _build_mock_anthropic_response(text: str = "ok") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage = None
    resp.stop_reason = "end_turn"
    return resp


def _make_model(use_shared: bool = True) -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.Anthropic,
            api_key="sk-ant",
            api_base="https://api.anthropic.com",
            verify_ssl=False,
            use_shared_llm_http_client=use_shared,
        ),
        model_config=ModelRequestConfig(model="claude-opus-4"),
    )


def _mock_async_client() -> AsyncMock:
    mc = AsyncMock()
    mc.messages.create = AsyncMock(return_value=_build_mock_anthropic_response())
    return mc


@pytest.fixture(autouse=True)
def _clear_client_cache():
    AnthropicModelClient._client_cache.clear()
    yield
    AnthropicModelClient._client_cache.clear()


class TestSharedClientPooling:
    @pytest.mark.asyncio
    async def test_shared_client_is_built_once_and_reused_without_hot_path_close(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_anthropic_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")])
            await model.invoke(messages=[UserMessage(content="hi again")])

        assert build_mock.call_count == 1
        assert mock_async_client.messages.create.call_count == 2
        assert mock_async_client.close.call_count == 0

    @pytest.mark.asyncio
    async def test_per_request_timeout_forwarded_without_rebuilding_client(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_anthropic_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")], timeout=15.0)

        assert build_mock.call_count == 1
        assert mock_async_client.messages.create.call_args.kwargs["timeout"] == 15.0

    @pytest.mark.asyncio
    async def test_aclose_closes_cached_clients_and_clears_cache(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_anthropic_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")])

        assert AnthropicModelClient._client_cache

        await AnthropicModelClient.aclose()

        assert mock_async_client.close.call_count == 1
        assert AnthropicModelClient._client_cache == {}


class TestSharedClientAcrossEventLoops:
    """Regression tests for a batch runner driving tasks through separate
    ``asyncio.run()`` calls, each with its own (short-lived) event loop.
    """

    def test_a_client_cached_from_a_closed_loop_is_rebuilt_not_reused(self):
        model = _make_model(use_shared=True)
        client = model._client

        built = []

        def fake_build(timeout=None):
            mc = _mock_async_client()
            built.append(mc)
            return mc

        with patch.object(client, "_build_async_anthropic_client", side_effect=fake_build):
            asyncio.run(model.invoke(messages=[UserMessage(content="hi")]))
            # The loop from the first asyncio.run() call is now closed. Reusing
            # the client built on it would raise "RuntimeError: Event loop is
            # closed" on the first request of the second run.
            asyncio.run(model.invoke(messages=[UserMessage(content="hi again")]))

        # A fresh client was built for the second (new) loop, not reused from
        # the first (closed) one.
        assert len(built) == 2
        assert built[0].messages.create.call_count == 1
        assert built[1].messages.create.call_count == 1

    def test_stale_entries_from_closed_loops_are_evicted_on_the_next_cache_miss(self):
        model = _make_model(use_shared=True)
        client = model._client

        with patch.object(
            client, "_build_async_anthropic_client", side_effect=lambda timeout=None: _mock_async_client()
        ):
            asyncio.run(model.invoke(messages=[UserMessage(content="hi")]))
            assert len(AnthropicModelClient._client_cache) == 1

            asyncio.run(model.invoke(messages=[UserMessage(content="hi again")]))

        # The first loop's entry was dropped (its loop is closed); only the
        # second, live-loop entry remains -- the cache does not grow forever.
        assert len(AnthropicModelClient._client_cache) == 1


class TestAcloseConnections:
    def _cfg(self, api_base: str) -> ModelClientConfig:
        return ModelClientConfig(
            client_provider=ProviderType.Anthropic,
            api_key="sk-ant",
            api_base=api_base,
            verify_ssl=False,
        )

    @pytest.mark.asyncio
    async def test_aclose_connections_closes_only_given(self):
        cfg_keep = self._cfg("https://api.anthropic.com")
        cfg_drop = self._cfg("https://old.example.com")
        keep_client, drop_client = AsyncMock(), AsyncMock()
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg_keep)] = keep_client
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg_drop)] = drop_client

        await AnthropicModelClient.aclose_connections([cfg_drop])

        # Only the targeted connection is closed; the untouched one stays.
        assert drop_client.close.call_count == 1
        assert keep_client.close.call_count == 0
        assert AnthropicModelClient.connection_key(cfg_keep) in AnthropicModelClient._client_cache
        assert AnthropicModelClient.connection_key(cfg_drop) not in AnthropicModelClient._client_cache

    @pytest.mark.asyncio
    async def test_aclose_connections_unknown_config_is_noop(self):
        cfg = self._cfg("https://api.anthropic.com")
        client = AsyncMock()
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg)] = client

        await AnthropicModelClient.aclose_connections([self._cfg("https://other.example.com")])

        assert client.close.call_count == 0
        assert AnthropicModelClient.connection_key(cfg) in AnthropicModelClient._client_cache

    @pytest.mark.asyncio
    async def test_aclose_connections_closes_every_loop_entry_for_a_dropped_connection(self):
        # A connection identity can have several cache entries, one per event
        # loop it has ever been used from (see ``_client_cache_key``).
        # aclose_connections must close all of them, not just one.
        cfg_keep = self._cfg("https://api.anthropic.com")
        cfg_drop = self._cfg("https://old.example.com")
        keep_client = AsyncMock()
        drop_client_loop_a, drop_client_loop_b = AsyncMock(), AsyncMock()
        base_drop_key = AnthropicModelClient.connection_key(cfg_drop)
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg_keep)] = keep_client
        AnthropicModelClient._client_cache[(*base_drop_key, "loop-a")] = drop_client_loop_a
        AnthropicModelClient._client_cache[(*base_drop_key, "loop-b")] = drop_client_loop_b

        await AnthropicModelClient.aclose_connections([cfg_drop])

        assert drop_client_loop_a.close.call_count == 1
        assert drop_client_loop_b.close.call_count == 1
        assert keep_client.close.call_count == 0
        assert AnthropicModelClient.connection_key(cfg_keep) in AnthropicModelClient._client_cache
        assert not any(key[: len(base_drop_key)] == base_drop_key for key in AnthropicModelClient._client_cache)

    def test_connection_key_includes_auth_mode(self):
        api_key_cfg = self._cfg("https://api.anthropic.com")
        custom_headers_cfg = ModelClientConfig(
            client_provider=ProviderType.Anthropic,
            api_key="sk-ant",
            api_base="https://api.anthropic.com",
            auth_mode=LLMAuthMode.CustomHeaders,
            verify_ssl=False,
        )

        assert (
            AnthropicModelClient.connection_key(api_key_cfg)
            != AnthropicModelClient.connection_key(custom_headers_cfg)
        )


class TestFallbackClient:
    @pytest.mark.asyncio
    async def test_fallback_builds_and_closes_client_per_request(self):
        model = _make_model(use_shared=False)
        client = model._client

        built = []

        def fake_build(timeout=None):
            mc = _mock_async_client()
            built.append(mc)
            return mc

        with patch.object(client, "_build_async_anthropic_client", side_effect=fake_build):
            await model.invoke(messages=[UserMessage(content="hi")])
            await model.invoke(messages=[UserMessage(content="hi again")])

        assert len(built) == 2
        assert all(mc.close.call_count == 1 for mc in built)
        assert AnthropicModelClient._client_cache == {}


class TestSdkHttpClientBinding:
    @pytest.mark.asyncio
    async def test_build_uses_sdk_default_async_httpx_client(self):
        from anthropic import DefaultAsyncHttpxClient

        from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import (
            _anthropic_sdk_httpx_module,
        )

        model = _make_model()
        sdk_client = model._client._build_async_anthropic_client()
        try:
            inner = sdk_client._client
            assert isinstance(inner, DefaultAsyncHttpxClient)
            expected_async_client = _anthropic_sdk_httpx_module().AsyncClient
            assert isinstance(inner, expected_async_client)
            limits = getattr(inner, "_limits", None)
            if limits is not None:
                assert limits.keepalive_expiry == 60.0
        finally:
            await sdk_client.close()
