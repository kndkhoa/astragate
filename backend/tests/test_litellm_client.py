"""
Unit tests for the LiteLLM Proxy HTTP client.

Covers:
  - Auth header injection (Authorization: Bearer {master_key}) on every call
  - post_chat success returning parsed JSON
  - post_chat timeout → LiteLLMTimeoutError
  - post_chat non-2xx response → LiteLLMHTTPError
  - stream_chat yields raw SSE bytes verbatim
  - stream_chat timeout → LiteLLMTimeoutError
  - stream_chat non-2xx response → LiteLLMHTTPError before yielding
  - get_models returns the ``data`` array

Requirement 1: AC1, AC2, AC4, AC6
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.litellm_client import (
    CHAT_COMPLETIONS_PATH,
    MODELS_PATH,
    LiteLLMClient,
    LiteLLMError,
    LiteLLMHTTPError,
    LiteLLMTimeoutError,
)


BASE_URL = "http://litellm-test:4000"
MASTER_KEY = "sk-test-master-key"


def _make_client(handler, timeout: float = 30.0) -> LiteLLMClient:
    """Build a LiteLLMClient backed by an httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    return LiteLLMClient(
        base_url=BASE_URL,
        master_key=MASTER_KEY,
        timeout=timeout,
        transport=transport,
    )


# ── Auth header injection ────────────────────────────────────────────────────


class TestAuthHeader:
    @pytest.mark.asyncio
    async def test_post_chat_sends_bearer_master_key(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("authorization")
            captured["content_type"] = request.headers.get("content-type")
            return httpx.Response(200, json={"id": "ok"})

        client = _make_client(handler)
        await client.post_chat({"model": "x", "messages": []})

        assert captured["authorization"] == f"Bearer {MASTER_KEY}"
        assert captured["content_type"] == "application/json"

    @pytest.mark.asyncio
    async def test_get_models_sends_bearer_master_key(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("authorization")
            captured["path"] = request.url.path
            return httpx.Response(200, json={"data": []})

        client = _make_client(handler)
        await client.get_models()

        assert captured["authorization"] == f"Bearer {MASTER_KEY}"
        assert captured["path"] == MODELS_PATH

    @pytest.mark.asyncio
    async def test_stream_chat_sends_bearer_master_key(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("authorization")
            return httpx.Response(200, content=b"data: {}\n\n")

        client = _make_client(handler)
        async for _ in client.stream_chat({"model": "x", "stream": True}):
            pass

        assert captured["authorization"] == f"Bearer {MASTER_KEY}"


# ── post_chat success ────────────────────────────────────────────────────────


class TestPostChatSuccess:
    @pytest.mark.asyncio
    async def test_returns_parsed_json(self):
        body_seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body_seen.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-abc",
                    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
            )

        client = _make_client(handler)
        result = await client.post_chat(
            {"model": "llama-3.1-8b", "messages": [{"role": "user", "content": "hello"}]}
        )

        assert result["id"] == "chatcmpl-abc"
        assert result["usage"]["total_tokens"] == 6
        # Body was forwarded verbatim.
        assert body_seen["model"] == "llama-3.1-8b"

    @pytest.mark.asyncio
    async def test_posts_to_chat_completions_path(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["path"] = request.url.path
            return httpx.Response(200, json={"id": "ok"})

        client = _make_client(handler)
        await client.post_chat({"model": "x", "messages": []})

        assert captured["method"] == "POST"
        assert captured["path"] == CHAT_COMPLETIONS_PATH


# ── post_chat error mapping ──────────────────────────────────────────────────


class TestPostChatErrors:
    @pytest.mark.asyncio
    async def test_timeout_raises_litellm_timeout_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("simulated timeout", request=request)

        client = _make_client(handler, timeout=0.5)
        with pytest.raises(LiteLLMTimeoutError) as excinfo:
            await client.post_chat({"model": "x", "messages": []})
        assert "0.5" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_connect_timeout_raises_litellm_timeout_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("simulated connect timeout", request=request)

        client = _make_client(handler)
        with pytest.raises(LiteLLMTimeoutError):
            await client.post_chat({"model": "x", "messages": []})

    @pytest.mark.asyncio
    async def test_non_2xx_response_raises_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": {"message": "rate limited", "type": "rate_limit"}},
            )

        client = _make_client(handler)
        with pytest.raises(LiteLLMHTTPError) as excinfo:
            await client.post_chat({"model": "x", "messages": []})
        assert excinfo.value.status_code == 429
        assert "rate limited" in excinfo.value.body

    @pytest.mark.asyncio
    async def test_5xx_response_raises_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream unavailable")

        client = _make_client(handler)
        with pytest.raises(LiteLLMHTTPError) as excinfo:
            await client.post_chat({"model": "x", "messages": []})
        assert excinfo.value.status_code == 503

    @pytest.mark.asyncio
    async def test_invalid_json_raises_litellm_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"not really json",
                headers={"content-type": "text/plain"},
            )

        client = _make_client(handler)
        with pytest.raises(LiteLLMError):
            await client.post_chat({"model": "x", "messages": []})

    @pytest.mark.asyncio
    async def test_transport_error_raises_litellm_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _make_client(handler)
        with pytest.raises(LiteLLMError) as excinfo:
            await client.post_chat({"model": "x", "messages": []})
        # Not a timeout — a generic transport failure.
        assert not isinstance(excinfo.value, LiteLLMTimeoutError)


# ── stream_chat ──────────────────────────────────────────────────────────────


class TestStreamChat:
    @pytest.mark.asyncio
    async def test_yields_raw_sse_bytes(self):
        sse_bytes = (
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=sse_bytes,
                headers={"content-type": "text/event-stream"},
            )

        client = _make_client(handler)
        chunks: list[bytes] = []
        async for chunk in client.stream_chat(
            {"model": "x", "messages": [], "stream": True}
        ):
            chunks.append(chunk)

        # All bytes round-tripped exactly.
        assert b"".join(chunks) == sse_bytes

    @pytest.mark.asyncio
    async def test_timeout_raises_litellm_timeout_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("simulated stream timeout", request=request)

        client = _make_client(handler, timeout=0.25)
        with pytest.raises(LiteLLMTimeoutError):
            async for _ in client.stream_chat({"model": "x", "stream": True}):
                pass

    @pytest.mark.asyncio
    async def test_non_2xx_raises_http_error_before_yielding(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"message": "bad model"}},
            )

        client = _make_client(handler)
        chunks: list[bytes] = []
        with pytest.raises(LiteLLMHTTPError) as excinfo:
            async for chunk in client.stream_chat(
                {"model": "nope", "stream": True}
            ):
                chunks.append(chunk)
        assert excinfo.value.status_code == 400
        # No bytes leaked to the customer.
        assert chunks == []


# ── get_models ───────────────────────────────────────────────────────────────


class TestGetModels:
    @pytest.mark.asyncio
    async def test_returns_data_array(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "llama-3.1-8b", "object": "model"},
                        {"id": "deepseek-chat", "object": "model"},
                    ],
                },
            )

        client = _make_client(handler)
        models = await client.get_models()

        assert isinstance(models, list)
        assert len(models) == 2
        assert models[0]["id"] == "llama-3.1-8b"

    @pytest.mark.asyncio
    async def test_missing_data_key_returns_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"object": "list"})

        client = _make_client(handler)
        models = await client.get_models()
        assert models == []

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})

        client = _make_client(handler)
        with pytest.raises(LiteLLMHTTPError) as excinfo:
            await client.get_models()
        assert excinfo.value.status_code == 401

    @pytest.mark.asyncio
    async def test_timeout_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)

        client = _make_client(handler)
        with pytest.raises(LiteLLMTimeoutError):
            await client.get_models()


# ── base_url handling ────────────────────────────────────────────────────────


class TestBaseURL:
    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_base_url(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok"})

        transport = httpx.MockTransport(handler)
        client = LiteLLMClient(
            base_url=f"{BASE_URL}/",  # trailing slash
            master_key=MASTER_KEY,
            transport=transport,
        )
        await client.post_chat({"model": "x", "messages": []})

        # Verify there's no double slash before /v1/...
        assert captured["url"] == f"{BASE_URL}{CHAT_COMPLETIONS_PATH}"
