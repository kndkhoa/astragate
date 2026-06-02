"""
Unit tests for the post-processing background task.

Covers:
  - Success path: token extraction, cost computation, settle_credit invoked,
    deduct_provider_balance invoked, usage_records inserted, virtual_keys
    counters bumped, check_thresholds invoked.
  - Cache hit path: zero base_cost / billed_amount, release_hold instead of
    settle_credit, no provider balance deduction, no threshold check, but
    usage_record is still written.
  - Error path: missing model / provider does not crash; an exception inside
    the session is caught and logged (no propagation).
  - Token count extraction handles missing / partial usage payloads.
  - Cache-hit detection picks up both top-level and ``_hidden_params`` flags.

Requirement 1 AC3, Requirement 3 AC4 AC5, Requirement 5 AC2, Requirement 12 AC1
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import post_process as pp
from app.services.post_process import (
    _compute_base_cost,
    _detect_cache_hit,
    _extract_usage,
    post_process_usage,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def request_id() -> str:
    return "req-test-123"


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def virtual_key_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def model_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def provider_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_model(model_id: uuid.UUID, *, input_price="0.05", output_price="0.10") -> MagicMock:
    m = MagicMock()
    m.id = model_id
    m.model_id = "groq/llama-3.1-8b-instant"
    m.display_name = "Llama 3.1 8B"
    m.input_price_per_1m = Decimal(input_price)
    m.output_price_per_1m = Decimal(output_price)
    return m


def _make_provider(provider_id: uuid.UUID, name: str = "groq") -> MagicMock:
    p = MagicMock()
    p.id = provider_id
    p.name = name
    p.balance_usd = Decimal("100.00")
    return p


def _make_session(*, model=None, provider=None) -> AsyncMock:
    """
    Build an AsyncMock that quacks like an AsyncSession. ``db.get(Model, id)``
    returns ``model``; ``db.get(Provider, id)`` returns ``provider``.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.execute = AsyncMock()

    async def fake_get(cls, pk):
        # Match by class name to avoid importing the ORM in the test.
        if cls.__name__ == "Model":
            return model
        if cls.__name__ == "Provider":
            return provider
        return None

    db.get = AsyncMock(side_effect=fake_get)
    return db


def _make_session_factory(db: AsyncMock):
    """Return a callable that, when invoked, returns an async-context-manager
    yielding ``db``. Mirrors how ``async_sessionmaker()`` behaves."""

    class _CM:
        async def __aenter__(self_inner):
            return db

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    def factory():
        return _CM()

    return factory


def _ok_response(prompt=10, completion=20, model_name="groq/llama-3.1-8b-instant") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": model_name,
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    }


# ── Pure helper functions ────────────────────────────────────────────────────


class TestExtractUsage:
    def test_returns_tokens_from_usage_block(self):
        prompt, completion, total = _extract_usage(_ok_response(10, 20))
        assert prompt == 10
        assert completion == 20
        assert total == 30

    def test_defaults_to_zero_when_usage_missing(self):
        prompt, completion, total = _extract_usage({})
        assert (prompt, completion, total) == (0, 0, 0)

    def test_computes_total_when_only_partial_usage_present(self):
        prompt, completion, total = _extract_usage(
            {"usage": {"prompt_tokens": 7, "completion_tokens": 3}}
        )
        # total_tokens missing → derived from prompt + completion
        assert (prompt, completion, total) == (7, 3, 10)


class TestDetectCacheHit:
    def test_top_level_flag(self):
        assert _detect_cache_hit({"cache_hit": True}) is True

    def test_hidden_params_flag(self):
        assert _detect_cache_hit({"_hidden_params": {"cache_hit": True}}) is True

    def test_default_false(self):
        assert _detect_cache_hit({}) is False
        assert _detect_cache_hit({"cache_hit": False}) is False
        assert _detect_cache_hit({"_hidden_params": {}}) is False


class TestComputeBaseCost:
    def test_combines_input_and_output_costs(self):
        # 1,000,000 input tokens @ $0.05 + 500,000 output tokens @ $0.10
        cost = _compute_base_cost(
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            input_price_per_1m=Decimal("0.05"),
            output_price_per_1m=Decimal("0.10"),
        )
        assert cost == Decimal("0.05") + Decimal("0.05")

    def test_zero_tokens_yields_zero_cost(self):
        cost = _compute_base_cost(0, 0, Decimal("100"), Decimal("200"))
        assert cost == Decimal("0")


# ── post_process_usage: success path ─────────────────────────────────────────


class TestPostProcessSuccess:
    @pytest.mark.asyncio
    async def test_full_success_path(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        model = _make_model(model_id, input_price="0.05", output_price="0.10")
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        with patch.object(pp, "settle_credit", new=AsyncMock()) as mock_settle, patch.object(
            pp, "deduct_provider_balance", new=AsyncMock(return_value=provider)
        ) as mock_deduct, patch.object(
            pp, "release_hold", new=AsyncMock()
        ) as mock_release, patch.object(
            pp, "check_thresholds", new=AsyncMock(return_value="normal")
        ) as mock_thresholds:
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(prompt=1_000_000, completion=500_000),
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        # Cache release_hold not called on a non-cache-hit response.
        mock_release.assert_not_awaited()

        # settle_credit called with billed_amount = base_cost * (1 + markup_rate)
        # base = 1M tokens × 0.05 / 1M + 500k × 0.10 / 1M = 0.05 + 0.05 = 0.10
        # billed = 0.10 × 1.20 = 0.12
        mock_settle.assert_awaited_once()
        settle_kwargs = mock_settle.call_args.kwargs
        assert settle_kwargs["user_id"] == user_id
        assert settle_kwargs["request_id"] == request_id
        assert settle_kwargs["actual_cost"] == Decimal("0.12")
        assert settle_kwargs["db"] is db

        # deduct_provider_balance called with base cost (no markup)
        mock_deduct.assert_awaited_once()
        deduct_kwargs = mock_deduct.call_args.kwargs
        assert deduct_kwargs["provider_id"] == provider_id
        assert deduct_kwargs["amount"] == Decimal("0.10")
        assert deduct_kwargs["request_id"] == request_id

        # check_thresholds invoked once for the alerting hook.
        mock_thresholds.assert_awaited_once()
        assert mock_thresholds.call_args.args[0] is provider

        # Usage record was inserted.
        added = [c.args[0] for c in db.add.call_args_list]
        usage_records = [r for r in added if type(r).__name__ == "UsageRecord"]
        assert len(usage_records) == 1
        ur = usage_records[0]
        assert ur.virtual_key_id == virtual_key_id
        assert ur.user_id == user_id
        assert ur.model_id == model_id
        assert ur.provider_id == provider_id
        assert ur.input_tokens == 1_000_000
        assert ur.output_tokens == 500_000
        assert ur.total_tokens == 1_500_000
        assert ur.base_cost_usd == Decimal("0.10")
        assert ur.billed_amount_usd == Decimal("0.12")
        assert ur.markup_rate == Decimal("0.20")
        assert ur.cache_hit is False
        assert ur.is_fallback is False
        assert ur.status == "success"
        assert ur.provider_name == "groq"

        # Virtual key counters update was issued.
        assert db.execute.await_count == 1
        # Commit was called.
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_records_is_fallback_flag(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        model = _make_model(model_id)
        provider = _make_provider(provider_id, name="deepseek")
        db = _make_session(model=model, provider=provider)

        with patch.object(pp, "settle_credit", new=AsyncMock()), patch.object(
            pp, "deduct_provider_balance", new=AsyncMock(return_value=provider)
        ), patch.object(pp, "check_thresholds", new=AsyncMock()):
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(),
                markup_rate=0.20,
                start_time=time.monotonic(),
                is_fallback=True,
                session_factory=_make_session_factory(db),
            )

        added = [c.args[0] for c in db.add.call_args_list]
        usage_records = [r for r in added if type(r).__name__ == "UsageRecord"]
        assert usage_records[0].is_fallback is True

    @pytest.mark.asyncio
    async def test_uses_response_model_field_for_model_name(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        """If LiteLLM rewrote the model (fallback), record the actual model used."""
        model = _make_model(model_id)
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        with patch.object(pp, "settle_credit", new=AsyncMock()), patch.object(
            pp, "deduct_provider_balance", new=AsyncMock(return_value=provider)
        ), patch.object(pp, "check_thresholds", new=AsyncMock()):
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(model_name="deepseek/deepseek-chat"),
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        added = [c.args[0] for c in db.add.call_args_list]
        ur = next(r for r in added if type(r).__name__ == "UsageRecord")
        assert ur.model_name == "deepseek/deepseek-chat"


# ── post_process_usage: cache hit path ───────────────────────────────────────


class TestPostProcessCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_zero_billed_releases_hold_and_skips_provider(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        model = _make_model(model_id)
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        response = _ok_response(prompt=100, completion=50)
        response["cache_hit"] = True

        with patch.object(pp, "settle_credit", new=AsyncMock()) as mock_settle, patch.object(
            pp, "deduct_provider_balance", new=AsyncMock()
        ) as mock_deduct, patch.object(
            pp, "release_hold", new=AsyncMock()
        ) as mock_release, patch.object(
            pp, "check_thresholds", new=AsyncMock()
        ) as mock_thresholds:
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=response,
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        # Cache hit: no charge, no provider deduction, no threshold alert.
        mock_settle.assert_not_awaited()
        mock_deduct.assert_not_awaited()
        mock_thresholds.assert_not_awaited()

        # Hold released so the customer is refunded.
        mock_release.assert_awaited_once()
        release_kwargs = mock_release.call_args.kwargs
        assert release_kwargs["user_id"] == user_id
        assert release_kwargs["request_id"] == request_id

        # Usage record still written, with cache_hit=True and zero costs.
        added = [c.args[0] for c in db.add.call_args_list]
        ur = next(r for r in added if type(r).__name__ == "UsageRecord")
        assert ur.cache_hit is True
        assert ur.base_cost_usd == Decimal("0")
        assert ur.billed_amount_usd == Decimal("0")
        assert ur.input_tokens == 100
        assert ur.output_tokens == 50

        # Virtual key counters still updated for cache hits.
        assert db.execute.await_count == 1
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_hit_via_hidden_params(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        model = _make_model(model_id)
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        response = _ok_response()
        response["_hidden_params"] = {"cache_hit": True}

        with patch.object(pp, "settle_credit", new=AsyncMock()) as mock_settle, patch.object(
            pp, "deduct_provider_balance", new=AsyncMock()
        ) as mock_deduct, patch.object(pp, "release_hold", new=AsyncMock()) as mock_release:
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=response,
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        mock_settle.assert_not_awaited()
        mock_deduct.assert_not_awaited()
        mock_release.assert_awaited_once()


# ── post_process_usage: error handling ───────────────────────────────────────


class TestPostProcessErrorHandling:
    @pytest.mark.asyncio
    async def test_missing_model_logs_and_returns(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        provider = _make_provider(provider_id)
        # Model lookup returns None.
        db = _make_session(model=None, provider=provider)

        with patch.object(pp, "settle_credit", new=AsyncMock()) as mock_settle, patch.object(
            pp, "deduct_provider_balance", new=AsyncMock()
        ) as mock_deduct:
            # Should NOT raise.
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(),
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        # Nothing downstream was called.
        mock_settle.assert_not_awaited()
        mock_deduct.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_settle_credit_failure_is_caught(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        """Exceptions inside the session must be swallowed (logged), not raised."""
        model = _make_model(model_id)
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        with patch.object(
            pp,
            "settle_credit",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch.object(pp, "deduct_provider_balance", new=AsyncMock()), patch.object(
            pp, "check_thresholds", new=AsyncMock()
        ):
            # Should NOT raise — background tasks must always be quiet.
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(),
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        # Rolled back instead of committed.
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_thresholds_invoked_after_deduction(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        """check_thresholds is the trigger for provider balance alerts (Req 5 AC4-5)."""
        model = _make_model(model_id)
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        with patch.object(pp, "settle_credit", new=AsyncMock()), patch.object(
            pp, "deduct_provider_balance", new=AsyncMock(return_value=provider)
        ), patch.object(
            pp, "check_thresholds", new=AsyncMock(return_value="warning")
        ) as mock_thresholds:
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(),
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        mock_thresholds.assert_awaited_once()
        assert mock_thresholds.call_args.args[0] is provider


# ── post_process_usage: virtual_keys counter update ──────────────────────────


class TestVirtualKeyCounterUpdate:
    @pytest.mark.asyncio
    async def test_virtual_key_update_includes_token_count(
        self, request_id, user_id, virtual_key_id, model_id, provider_id
    ):
        """The UPDATE to virtual_keys must include the request's total_tokens."""
        model = _make_model(model_id)
        provider = _make_provider(provider_id)
        db = _make_session(model=model, provider=provider)

        with patch.object(pp, "settle_credit", new=AsyncMock()), patch.object(
            pp, "deduct_provider_balance", new=AsyncMock(return_value=provider)
        ), patch.object(pp, "check_thresholds", new=AsyncMock()):
            await post_process_usage(
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=_ok_response(prompt=42, completion=58),
                markup_rate=0.20,
                start_time=time.monotonic(),
                session_factory=_make_session_factory(db),
            )

        assert db.execute.await_count == 1
        update_stmt = db.execute.call_args.args[0]
        # SQLAlchemy Update: inspect the compiled values dict.
        compiled = update_stmt.compile(compile_kwargs={"literal_binds": False})
        params = compiled.params
        # total_tokens added by the request = 100.
        assert params["total_tokens_1"] == 100
        assert params["total_requests_1"] == 1
        # last_used_at param is present (datetime value)
        assert "last_used_at" in params
