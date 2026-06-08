"""
Mini LiteLLM shim for LOCAL DEV (no Docker, no Rust toolchain required).

The real LiteLLM proxy can't be pip-installed on Python 3.14 yet (orjson has
no prebuilt wheel and needs a Rust compiler). This lightweight FastAPI app
emulates just enough of the LiteLLM Proxy surface that AstraGate's
litellm_client.py talks to:

  - POST /v1/chat/completions   (non-streaming + SSE streaming)
  - POST /v1/embeddings
  - GET  /v1/models
  - GET  /health

It translates OpenAI-format requests into Google Gemini generateContent calls
and translates the responses back into OpenAI format (including usage tokens).

Run:
  set GEMINI_API_KEY=...           (Windows CMD)
  $env:GEMINI_API_KEY="..."        (PowerShell)
  uvicorn litellm.mock_litellm:app --port 4000

Auth: expects `Authorization: Bearer <LITELLM_MASTER_KEY>` like the real proxy,
but does not strictly enforce it (dev only).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Every model name AstraGate might send is mapped to a real, currently-available
# Gemini model. gemini-1.5-flash is retired, so we use gemini-2.0-flash.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

app = FastAPI(title="Mock LiteLLM Proxy (Gemini)")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _messages_to_gemini(messages: list[dict]) -> tuple[list[dict], str | None]:
    """Convert OpenAI chat messages into Gemini `contents` + optional system."""
    contents = []
    system_text = None
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if isinstance(content, list):
            # OpenAI multi-part content — flatten text parts
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if role == "system":
            system_text = (system_text or "") + str(content)
            continue
        gem_role = "model" if role == "assistant" else "user"
        contents.append({"role": gem_role, "parts": [{"text": str(content)}]})
    return contents, system_text


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ── Endpoints ───────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "backend": "gemini", "model": GEMINI_MODEL}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": "gemini-flash", "object": "model", "owned_by": "google"},
            {"id": "gemini/gemini-1.5-flash", "object": "model", "owned_by": "google"},
            {"id": "llama-3.1-8b", "object": "model", "owned_by": "google"},
            {"id": "deepseek-chat", "object": "model", "owned_by": "google"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    requested_model = body.get("model", "gemini-flash")
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    max_tokens = body.get("max_tokens", 1024)

    contents, system_text = _messages_to_gemini(messages)
    gen_config: dict[str, Any] = {}
    if isinstance(max_tokens, int) and max_tokens > 0:
        # Clamp absurdly large values so Gemini doesn't reject the request.
        gen_config["maxOutputTokens"] = min(max_tokens, 8192)

    payload: dict[str, Any] = {"contents": contents}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    if gen_config:
        payload["generationConfig"] = gen_config

    if not GEMINI_API_KEY:
        text = "Hello! This is a fallback mock response because GEMINI_API_KEY is not set."
        prompt_tokens = _estimate_tokens(" ".join(str(m.get("content", "")) for m in messages))
        completion_tokens = _estimate_tokens(text)
        total_tokens = prompt_tokens + completion_tokens
    else:
        url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code >= 400:
            return JSONResponse(
                status_code=resp.status_code,
                content={"error": {"message": resp.text}},
            )

        data = resp.json()
        # Extract text from the first candidate
        text = ""
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            text = ""

        # Token usage from Gemini, fall back to estimate
        usage_meta = data.get("usageMetadata", {})
        prompt_tokens = usage_meta.get("promptTokenCount") or _estimate_tokens(
            " ".join(str(m.get("content", "")) for m in messages)
        )
        completion_tokens = usage_meta.get("candidatesTokenCount") or _estimate_tokens(text)
        total_tokens = usage_meta.get("totalTokenCount") or (prompt_tokens + completion_tokens)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if not stream:
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": requested_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }

    # Streaming: chunk the already-complete text into SSE deltas so AstraGate's
    # StreamingResponse path can be exercised end-to-end.
    async def sse() -> AsyncIterator[bytes]:
        # Split into ~20-char chunks to simulate streaming
        chunk_size = 20
        for i in range(0, len(text), chunk_size):
            piece = text[i : i + chunk_size]
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": requested_model,
                "choices": [
                    {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
        # Final chunk with usage + finish_reason
        final = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": requested_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
        yield f"data: {json.dumps(final)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    requested_model = body.get("model", "gemini-flash")
    input_val = body.get("input", "")
    texts = input_val if isinstance(input_val, list) else [input_val]

    data_items = []
    total_tokens = 0
    if not GEMINI_API_KEY:
        for idx, t in enumerate(texts):
            data_items.append(
                {"object": "embedding", "embedding": [0.1] * 1536, "index": idx}
            )
            total_tokens += _estimate_tokens(str(t))
    else:
        embed_model = "text-embedding-004"
        url = f"{GEMINI_BASE}/models/{embed_model}:embedContent?key={GEMINI_API_KEY}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            for idx, t in enumerate(texts):
                payload = {
                    "model": f"models/{embed_model}",
                    "content": {"parts": [{"text": str(t)}]},
                }
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    return JSONResponse(
                        status_code=resp.status_code,
                        content={"error": {"message": resp.text}},
                    )
                emb = resp.json().get("embedding", {}).get("values", [])
                data_items.append(
                    {"object": "embedding", "embedding": emb, "index": idx}
                )
                total_tokens += _estimate_tokens(str(t))

    return {
        "object": "list",
        "data": data_items,
        "model": requested_model,
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }
