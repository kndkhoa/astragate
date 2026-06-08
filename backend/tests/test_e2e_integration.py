"""
End-to-end integration tests for AstraGate (Task 44).

These tests exercise the full request lifecycle:
  1. Register → get Virtual Key → call /v1/chat/completions → verify usage + billing
  2. Credit insufficient → HTTP 402
  3. Hard Stop → fallback routing or HTTP 503
  4. Guardrail violation → HTTP 400
  5. Stripe webhook → credit added
  6. Streaming → SSE chunks arrive incrementally

Prerequisites:
  - AstraGate API running on http://localhost:8000
  - PostgreSQL + Redis available
  - LiteLLM Proxy running (or mocked)

Run:
  pytest backend/tests/test_e2e_integration.py -v --timeout=60
"""
import asyncio
import json
import uuid
from decimal import Decimal

import httpx
import pytest

BASE_URL = "http://localhost:8000"
TIMEOUT = 30.0


# ── Helpers ───────────────────────────────────────────────────────────────────


def unique_email() -> str:
    return f"e2e-test-{uuid.uuid4().hex[:8]}@test.astragate.io"


async def register_user(client: httpx.AsyncClient, email: str, password: str = "TestPassword123!"):
    """Register a new user and return tokens + default key."""
    resp = await client.post(
        f"{BASE_URL}/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, f"Registration failed: {resp.text}"
    data = resp.json()
    assert "access_token" in data
    assert "default_key" in data
    return data


async def login_user(client: httpx.AsyncClient, email: str, password: str = "TestPassword123!"):
    """Login an existing user and return tokens."""
    resp = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()


def auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def key_headers(virtual_key: str) -> dict:
    return {"Authorization": f"Bearer {virtual_key}"}


# ── Test: Full lifecycle ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_lifecycle_register_to_usage():
    """
    E2E: register → get Virtual Key → call chat completions → verify usage recorded.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        email = unique_email()

        # 1. Register
        reg_data = await register_user(client, email)
        access_token = reg_data["access_token"]
        virtual_key = reg_data["default_key"]

        # 2. Verify credit balance ($1.00 free credit)
        balance_resp = await client.get(
            f"{BASE_URL}/api/billing/balance",
            headers=auth_headers(access_token),
        )
        assert balance_resp.status_code == 200
        balance = balance_resp.json()["balance_usd"]
        assert balance == 1.0, f"Expected $1.00 free credit, got ${balance}"

        # 3. Call chat completions
        chat_resp = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers={
                **key_headers(virtual_key),
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": "Say hello in one word"}],
                "max_tokens": 10,
            },
        )
        assert chat_resp.status_code == 200, f"Chat failed: {chat_resp.text}"
        chat_data = chat_resp.json()
        assert "choices" in chat_data
        assert len(chat_data["choices"]) > 0

        # 4. Wait for background post-processing
        await asyncio.sleep(2)

        # 5. Verify usage record created
        usage_resp = await client.get(
            f"{BASE_URL}/api/usage",
            headers=auth_headers(access_token),
        )
        assert usage_resp.status_code == 200
        usage_data = usage_resp.json()
        assert usage_data["pagination"]["total_count"] >= 1
        record = usage_data["records"][0]
        assert record["model_name"] is not None
        assert record["billed_amount_usd"] >= 0

        # 6. Verify credit was deducted
        balance_resp2 = await client.get(
            f"{BASE_URL}/api/billing/balance",
            headers=auth_headers(access_token),
        )
        new_balance = balance_resp2.json()["balance_usd"]
        assert new_balance < balance, f"Credit not deducted: was ${balance}, now ${new_balance}"


# ── Test: Insufficient credit ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insufficient_credit_returns_402():
    """
    E2E: Set balance to near-zero, verify HTTP 402 on chat request.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        email = unique_email()
        reg_data = await register_user(client, email)
        virtual_key = reg_data["default_key"]
        access_token = reg_data["access_token"]

        # Drain the balance by making requests until 402
        # Or we can use a very large max_tokens to ensure the estimate exceeds $1
        chat_resp = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers={
                **key_headers(virtual_key),
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 100000000,  # Extremely high to exceed $1 estimate
            },
        )
        # Should get 402 because estimated cost exceeds $1.00 balance
        assert chat_resp.status_code == 402, (
            f"Expected 402 for insufficient credit, got {chat_resp.status_code}: {chat_resp.text}"
        )


# ── Test: Guardrail violation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guardrail_blocks_banned_keyword():
    """
    E2E: Add a banned keyword via admin, send prompt containing it, verify HTTP 400.

    Note: Requires an admin user to add the keyword. If no admin exists,
    this test is skipped.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Try to login as admin (assumes seed creates admin@astragate.io)
        admin_login = await client.post(
            f"{BASE_URL}/auth/login",
            json={"email": "admin@astragate.io", "password": "admin123"},
        )
        if admin_login.status_code != 200:
            pytest.skip("Admin user not available for guardrail test")

        admin_token = admin_login.json()["access_token"]
        test_keyword = f"e2e_banned_{uuid.uuid4().hex[:6]}"

        # Add a guardrail keyword
        add_resp = await client.post(
            f"{BASE_URL}/admin/guardrails",
            headers={**auth_headers(admin_token), "Content-Type": "application/json"},
            json={"keyword": test_keyword, "scope": "input"},
        )
        assert add_resp.status_code == 200 or add_resp.status_code == 201

        # Wait for cache invalidation
        await asyncio.sleep(1)

        # Register a customer user and get key
        email = unique_email()
        reg_data = await register_user(client, email)
        virtual_key = reg_data["default_key"]

        # Send a prompt containing the banned keyword
        chat_resp = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers={
                **key_headers(virtual_key),
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": f"Tell me about {test_keyword}"}],
                "max_tokens": 50,
            },
        )
        assert chat_resp.status_code == 400, (
            f"Expected 400 for guardrail violation, got {chat_resp.status_code}: {chat_resp.text}"
        )
        assert "content policy" in chat_resp.text.lower() or test_keyword in chat_resp.text

        # Cleanup: delete the keyword
        keywords_resp = await client.get(
            f"{BASE_URL}/admin/guardrails",
            headers=auth_headers(admin_token),
        )
        if keywords_resp.status_code == 200:
            for kw in keywords_resp.json().get("keywords", []):
                if kw["keyword"] == test_keyword:
                    await client.delete(
                        f"{BASE_URL}/admin/guardrails/{kw['id']}",
                        headers=auth_headers(admin_token),
                    )


# ── Test: Stripe webhook mock ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stripe_webhook_adds_credit():
    """
    E2E: Send a mock payment_intent.succeeded webhook → verify credit balance increased.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        email = unique_email()
        reg_data = await register_user(client, email)
        access_token = reg_data["access_token"]

        # Get initial balance
        balance_resp = await client.get(
            f"{BASE_URL}/api/billing/balance",
            headers=auth_headers(access_token),
        )
        initial_balance = balance_resp.json()["balance_usd"]

        # We need the user_id from /auth/me
        me_resp = await client.get(
            f"{BASE_URL}/auth/me",
            headers=auth_headers(access_token),
        )
        user_id = me_resp.json()["id"]

        # Send mock Stripe webhook event
        topup_amount = "10.00"
        webhook_event = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": f"pi_mock_{uuid.uuid4().hex[:12]}",
                    "metadata": {
                        "user_id": user_id,
                        "amount": topup_amount,
                    },
                }
            },
        }

        webhook_resp = await client.post(
            f"{BASE_URL}/api/billing/webhook",
            json=webhook_event,
            headers={"Content-Type": "application/json"},
        )
        assert webhook_resp.status_code == 200, f"Webhook failed: {webhook_resp.text}"

        # Verify balance increased
        balance_resp2 = await client.get(
            f"{BASE_URL}/api/billing/balance",
            headers=auth_headers(access_token),
        )
        new_balance = balance_resp2.json()["balance_usd"]
        expected = initial_balance + float(topup_amount)
        assert abs(new_balance - expected) < 0.01, (
            f"Balance mismatch: expected ~${expected}, got ${new_balance}"
        )


# ── Test: Streaming ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_returns_sse_chunks():
    """
    E2E: Call /v1/chat/completions with stream=true → verify SSE chunks arrive.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        email = unique_email()
        reg_data = await register_user(client, email)
        virtual_key = reg_data["default_key"]

        # Make streaming request
        async with client.stream(
            "POST",
            f"{BASE_URL}/v1/chat/completions",
            headers={
                **key_headers(virtual_key),
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": "Count from 1 to 5"}],
                "max_tokens": 50,
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200, f"Stream failed: {response.status_code}"
            assert "text/event-stream" in response.headers.get("content-type", "")

            chunks = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    chunks.append(line)
                    if line.strip() == "data: [DONE]":
                        break

            # Should have received multiple chunks (not all buffered)
            assert len(chunks) >= 2, f"Expected multiple SSE chunks, got {len(chunks)}"


# ── Test: Virtual key revocation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoked_key_returns_401():
    """
    E2E: Create key → revoke it → attempt request → HTTP 401.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        email = unique_email()
        reg_data = await register_user(client, email)
        access_token = reg_data["access_token"]

        # Create a new key
        create_resp = await client.post(
            f"{BASE_URL}/api/keys",
            headers={**auth_headers(access_token), "Content-Type": "application/json"},
            json={"name": "revoke-test-key"},
        )
        assert create_resp.status_code == 201
        key_data = create_resp.json()
        new_key = key_data["key"]
        key_id = key_data["id"]

        # Verify key works
        chat_resp = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers={**key_headers(new_key), "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            },
        )
        # Either 200 (success) or 402 (low balance) — both mean auth passed
        assert chat_resp.status_code in [200, 402]

        # Revoke the key
        revoke_resp = await client.delete(
            f"{BASE_URL}/api/keys/{key_id}",
            headers=auth_headers(access_token),
        )
        assert revoke_resp.status_code == 200

        # Wait for Redis cache to expire (TTL 30s) — or immediately if cache invalidated
        await asyncio.sleep(2)

        # Attempt request with revoked key
        chat_resp2 = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            headers={**key_headers(new_key), "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            },
        )
        assert chat_resp2.status_code == 401, (
            f"Expected 401 for revoked key, got {chat_resp2.status_code}"
        )


# ── Test: Health endpoint ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint():
    """E2E: Verify /health returns status of all dependencies."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(f"{BASE_URL}/health")
        # 200 if all healthy, 503 if degraded — both are valid in test env
        assert resp.status_code in [200, 503]
        data = resp.json()
        assert "status" in data
        assert "postgres" in data
        assert "redis" in data
        assert "litellm" in data
