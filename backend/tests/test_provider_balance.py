"""
Unit tests for the Provider Balance service.

Covers:
  - check_provider_status: cache hit / miss / Redis failure / unknown provider
  - deduct_provider_balance: balance update + log row + cache invalidation
  - check_thresholds: hard_stop activation, warning emission with 1/hour throttle,
    auto-clearing warning, hard_stop sticky until manual release.
  - Alert handler hook (used by task 28 for Resend wiring).

Requirement 5: AC2, AC4, AC5, AC6, AC7
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import provider_balance as pb
from app.services.provider_balance import (
    REDIS_PROVIDER_STATUS_PREFIX,
    REDIS_PROVIDER_STATUS_TTL,
    STATUS_HARD_STOP,
    STATUS_NORMAL,
    STATUS_WARNING,
    WARNING_ALERT_COOLDOWN,
    ProviderRoutingDecision,
    ProviderStatus,
    check_provider_status,
    check_thresholds,
    deduct_provider_balance,
    invalidate_provider_status_cache,
    register_alert_handler,
    resolve_provider_for_request,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def provider_id():
    return uuid.uuid4()


@pytest.fixture
def fallback_provider_id():
    return uuid.uuid4()


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture(autouse=True)
def clear_alert_handler():
    """Ensure each test starts with no registered handler."""
    pb._alert_handler = None
    yield
    pb._alert_handler = None


def _make_provider(
    provider_id: uuid.UUID,
    *,
    name: str = "groq",
    balance: str = "100.00",
    warning: str = "10.00",
    hard_stop: str = "2.00",
    status: str = STATUS_NORMAL,
    fallback_provider_id: uuid.UUID | None = None,
    last_warning_alert_at: datetime | None = None,
    is_active: bool = True,
):
    """Build a MagicMock that quacks like a Provider ORM row."""
    p = MagicMock()
    p.id = provider_id
    p.name = name
    p.display_name = name.capitalize()
    p.balance_usd = Decimal(balance)
    p.warning_threshold = Decimal(warning)
    p.hard_stop_threshold = Decimal(hard_stop)
    p.status = status
    p.fallback_provider_id = fallback_provider_id
    p.last_warning_alert_at = last_warning_alert_at
    p.hard_stop_activated_at = None
    p.is_active = is_active
    return p


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ── check_provider_status ────────────────────────────────────────────────────


class TestCheckProviderStatus:
    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_provider(self, provider_id, mock_db, mock_redis):
        mock_redis.get.return_value = None
        mock_db.execute.return_value = _scalar_result(None)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            result = await check_provider_status(provider_id, mock_db)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_status_from_db_and_caches(
        self, provider_id, fallback_provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(
            provider_id,
            balance="50.00",
            status=STATUS_NORMAL,
            fallback_provider_id=fallback_provider_id,
        )
        mock_redis.get.return_value = None
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            status = await check_provider_status(provider_id, mock_db)

        assert status is not None
        assert status.provider_id == provider_id
        assert status.status == STATUS_NORMAL
        assert status.balance_usd == Decimal("50.00")
        assert status.fallback_provider_id == fallback_provider_id
        assert status.has_fallback is True
        assert status.is_hard_stop is False

        # Cached the result with the right TTL.
        mock_redis.set.assert_called_once()
        args, kwargs = mock_redis.set.call_args
        assert args[0] == f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"
        assert kwargs == {"ex": REDIS_PROVIDER_STATUS_TTL}
        # Cached payload roundtrips back to the same fields.
        cached_payload = json.loads(args[1])
        assert cached_payload["status"] == STATUS_NORMAL
        assert cached_payload["balance_usd"] == "50.00"
        assert cached_payload["fallback_provider_id"] == str(fallback_provider_id)

    @pytest.mark.asyncio
    async def test_uses_cache_without_querying_db(
        self, provider_id, mock_db, mock_redis
    ):
        cached_payload = json.dumps(
            {
                "provider_id": str(provider_id),
                "name": "groq",
                "status": STATUS_HARD_STOP,
                "balance_usd": "1.50",
                "warning_threshold": "10.00",
                "hard_stop_threshold": "2.00",
                "fallback_provider_id": None,
                "is_active": True,
            }
        )
        mock_redis.get.return_value = cached_payload

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            status = await check_provider_status(provider_id, mock_db)

        assert status is not None
        assert status.is_hard_stop is True
        assert status.has_fallback is False
        # DB never touched on cache hit.
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_failure_falls_back_to_db(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(provider_id)
        mock_redis.get.side_effect = Exception("redis down")
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            status = await check_provider_status(provider_id, mock_db)

        assert status is not None
        assert status.status == STATUS_NORMAL


# ── deduct_provider_balance ──────────────────────────────────────────────────


class TestDeductProviderBalance:
    @pytest.mark.asyncio
    async def test_deducts_balance_and_writes_log(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(provider_id, balance="100.00")
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            updated = await deduct_provider_balance(
                provider_id=provider_id,
                amount=Decimal("0.001234"),
                request_id="req-abc",
                db=mock_db,
            )

        assert updated is provider
        assert provider.balance_usd == Decimal("100.00") - Decimal("0.001234")

        # A log row was added.
        mock_db.add.assert_called_once()
        log_row = mock_db.add.call_args.args[0]
        assert log_row.provider_id == provider_id
        assert log_row.change_type == "usage_deduct"
        assert log_row.amount_usd == Decimal("-0.001234")
        assert log_row.balance_before == Decimal("100.00")
        assert log_row.balance_after == Decimal("100.00") - Decimal("0.001234")
        assert "req-abc" in (log_row.note or "")

        # Cache invalidated so subsequent reads see the new balance.
        mock_redis.delete.assert_called_once_with(
            f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"
        )

    @pytest.mark.asyncio
    async def test_rejects_negative_amount(self, provider_id, mock_db):
        with pytest.raises(ValueError):
            await deduct_provider_balance(
                provider_id=provider_id,
                amount=Decimal("-1"),
                request_id="req-x",
                db=mock_db,
            )

    @pytest.mark.asyncio
    async def test_raises_for_unknown_provider(self, provider_id, mock_db):
        mock_db.execute.return_value = _scalar_result(None)
        with pytest.raises(ValueError, match="Provider not found"):
            await deduct_provider_balance(
                provider_id=provider_id,
                amount=Decimal("0.01"),
                request_id="req-x",
                db=mock_db,
            )

    @pytest.mark.asyncio
    async def test_zero_amount_is_allowed(self, provider_id, mock_db, mock_redis):
        """Cache hits bill 0 — a 0 deduction must still produce a log row."""
        provider = _make_provider(provider_id, balance="50.00")
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            await deduct_provider_balance(
                provider_id=provider_id,
                amount=Decimal("0"),
                request_id="req-cache",
                db=mock_db,
            )

        assert provider.balance_usd == Decimal("50.00")
        mock_db.add.assert_called_once()


# ── check_thresholds ─────────────────────────────────────────────────────────


class TestCheckThresholds:
    @pytest.mark.asyncio
    async def test_normal_balance_no_alert(self, provider_id, mock_db, mock_redis):
        provider = _make_provider(
            provider_id, balance="50.00", warning="10.00", hard_stop="2.00"
        )
        captured: list[tuple] = []

        async def handler(p, ev, bal):
            captured.append((p, ev, bal))

        register_alert_handler(handler)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        assert new_status == STATUS_NORMAL
        assert provider.status == STATUS_NORMAL
        assert captured == []
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_warning_emits_warning_alert(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(
            provider_id, balance="5.00", warning="10.00", hard_stop="2.00"
        )
        captured: list[tuple] = []

        async def handler(p, ev, bal):
            captured.append((p, ev, bal))

        register_alert_handler(handler)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        assert new_status == STATUS_WARNING
        assert provider.status == STATUS_WARNING
        assert provider.last_warning_alert_at is not None
        assert len(captured) == 1
        assert captured[0][1] == "warning"
        assert captured[0][2] == Decimal("5.00")
        # Status changed → cache invalidated.
        mock_redis.delete.assert_called_with(
            f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"
        )

    @pytest.mark.asyncio
    async def test_warning_alert_is_throttled_to_one_per_hour(
        self, provider_id, mock_db, mock_redis
    ):
        last_sent = datetime.now(timezone.utc) - timedelta(minutes=5)
        provider = _make_provider(
            provider_id,
            balance="5.00",
            warning="10.00",
            hard_stop="2.00",
            status=STATUS_WARNING,
            last_warning_alert_at=last_sent,
        )
        captured: list[tuple] = []

        async def handler(p, ev, bal):
            captured.append((p, ev, bal))

        register_alert_handler(handler)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        assert new_status == STATUS_WARNING
        # Within cooldown — no new alert sent.
        assert captured == []
        # last_warning_alert_at not bumped.
        assert provider.last_warning_alert_at == last_sent

    @pytest.mark.asyncio
    async def test_warning_alert_resends_after_cooldown(
        self, provider_id, mock_db, mock_redis
    ):
        last_sent = datetime.now(timezone.utc) - WARNING_ALERT_COOLDOWN - timedelta(minutes=1)
        provider = _make_provider(
            provider_id,
            balance="5.00",
            warning="10.00",
            hard_stop="2.00",
            status=STATUS_WARNING,
            last_warning_alert_at=last_sent,
        )
        captured: list[tuple] = []

        async def handler(p, ev, bal):
            captured.append((p, ev, bal))

        register_alert_handler(handler)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            await check_thresholds(provider, mock_db)

        assert len(captured) == 1
        assert captured[0][1] == "warning"

    @pytest.mark.asyncio
    async def test_below_hard_stop_activates_hard_stop_immediately(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(
            provider_id, balance="1.00", warning="10.00", hard_stop="2.00"
        )
        captured: list[tuple] = []

        async def handler(p, ev, bal):
            captured.append((p, ev, bal))

        register_alert_handler(handler)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        assert new_status == STATUS_HARD_STOP
        assert provider.status == STATUS_HARD_STOP
        assert provider.hard_stop_activated_at is not None
        # Hard stop alert is immediate, never throttled.
        assert len(captured) == 1
        assert captured[0][1] == "hard_stop"
        mock_redis.delete.assert_called_with(
            f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"
        )

    @pytest.mark.asyncio
    async def test_already_hard_stop_does_not_realert(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(
            provider_id,
            balance="0.50",
            warning="10.00",
            hard_stop="2.00",
            status=STATUS_HARD_STOP,
        )
        captured: list[tuple] = []

        async def handler(p, ev, bal):
            captured.append((p, ev, bal))

        register_alert_handler(handler)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        assert new_status == STATUS_HARD_STOP
        # Already in hard_stop — no duplicate alert spam.
        assert captured == []

    @pytest.mark.asyncio
    async def test_recovery_clears_warning_status(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(
            provider_id,
            balance="50.00",
            warning="10.00",
            hard_stop="2.00",
            status=STATUS_WARNING,
        )

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        assert new_status == STATUS_NORMAL
        assert provider.status == STATUS_NORMAL
        mock_redis.delete.assert_called_with(
            f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"
        )

    @pytest.mark.asyncio
    async def test_hard_stop_does_not_auto_clear_when_balance_recovers(
        self, provider_id, mock_db, mock_redis
    ):
        """Per AC8, hard_stop must be released manually by an admin."""
        provider = _make_provider(
            provider_id,
            balance="50.00",  # back above warning
            warning="10.00",
            hard_stop="2.00",
            status=STATUS_HARD_STOP,
        )

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            new_status = await check_thresholds(provider, mock_db)

        # Status is still hard_stop because admin hasn't run release-hard-stop.
        assert new_status == STATUS_HARD_STOP
        assert provider.status == STATUS_HARD_STOP


# ── invalidate_provider_status_cache ─────────────────────────────────────────


class TestInvalidateCache:
    @pytest.mark.asyncio
    async def test_deletes_key(self, provider_id, mock_redis):
        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            await invalidate_provider_status_cache(provider_id)
        mock_redis.delete.assert_called_once_with(
            f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"
        )

    @pytest.mark.asyncio
    async def test_redis_failure_is_swallowed(self, provider_id, mock_redis):
        mock_redis.delete.side_effect = Exception("redis down")
        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            # Should not raise.
            await invalidate_provider_status_cache(provider_id)


# ── ProviderStatus dataclass helpers ─────────────────────────────────────────


class TestProviderStatusHelpers:
    def test_is_hard_stop_flag(self, provider_id):
        s = ProviderStatus(
            provider_id=provider_id,
            name="groq",
            status=STATUS_HARD_STOP,
            balance_usd=Decimal("0"),
            warning_threshold=Decimal("10"),
            hard_stop_threshold=Decimal("2"),
            fallback_provider_id=None,
            is_active=True,
        )
        assert s.is_hard_stop is True
        assert s.has_fallback is False

    def test_has_fallback_flag(self, provider_id, fallback_provider_id):
        s = ProviderStatus(
            provider_id=provider_id,
            name="groq",
            status=STATUS_NORMAL,
            balance_usd=Decimal("100"),
            warning_threshold=Decimal("10"),
            hard_stop_threshold=Decimal("2"),
            fallback_provider_id=fallback_provider_id,
            is_active=True,
        )
        assert s.has_fallback is True
        assert s.is_hard_stop is False


# ── resolve_provider_for_request ─────────────────────────────────────────────


class TestResolveProviderForRequest:
    """Routing through the fallback chain on Hard Stop (Req 5 AC6, AC7)."""

    @pytest.mark.asyncio
    async def test_normal_provider_returns_directly(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(provider_id, balance="50.00", status=STATUS_NORMAL)
        mock_redis.get.return_value = None
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is not None
        assert decision.provider.provider_id == provider_id
        assert decision.is_fallback is False
        assert decision.reason == "ok"
        assert decision.should_block is False
        assert decision.original_provider_id == provider_id

    @pytest.mark.asyncio
    async def test_warning_status_still_serves_request(
        self, provider_id, mock_db, mock_redis
    ):
        """Warning is informational only — requests still flow."""
        provider = _make_provider(provider_id, balance="5.00", status=STATUS_WARNING)
        mock_redis.get.return_value = None
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.should_block is False
        assert decision.is_fallback is False
        assert decision.reason == "ok"

    @pytest.mark.asyncio
    async def test_hard_stop_with_fallback_routes_to_fallback(
        self, provider_id, fallback_provider_id, mock_db, mock_redis
    ):
        primary = _make_provider(
            provider_id,
            name="groq",
            balance="0.50",
            status=STATUS_HARD_STOP,
            fallback_provider_id=fallback_provider_id,
        )
        fallback = _make_provider(
            fallback_provider_id,
            name="deepseek",
            balance="50.00",
            status=STATUS_NORMAL,
        )
        mock_redis.get.return_value = None
        # Two sequential lookups: primary, then fallback.
        mock_db.execute.side_effect = [
            _scalar_result(primary),
            _scalar_result(fallback),
        ]

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is not None
        assert decision.provider.provider_id == fallback_provider_id
        assert decision.is_fallback is True
        assert decision.reason == "fallback"
        assert decision.should_block is False
        assert decision.original_provider_id == provider_id

    @pytest.mark.asyncio
    async def test_hard_stop_without_fallback_blocks(
        self, provider_id, mock_db, mock_redis
    ):
        provider = _make_provider(
            provider_id,
            balance="0.50",
            status=STATUS_HARD_STOP,
            fallback_provider_id=None,
        )
        mock_redis.get.return_value = None
        mock_db.execute.return_value = _scalar_result(provider)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is None
        assert decision.should_block is True
        assert decision.reason == "hard_stop_no_fallback"
        assert decision.is_fallback is False

    @pytest.mark.asyncio
    async def test_hard_stop_with_hard_stopped_fallback_chains(
        self, provider_id, fallback_provider_id, mock_db, mock_redis
    ):
        """Walk a 2-hop fallback chain when first fallback is also down."""
        third_id = uuid.uuid4()
        primary = _make_provider(
            provider_id,
            name="groq",
            balance="0.50",
            status=STATUS_HARD_STOP,
            fallback_provider_id=fallback_provider_id,
        )
        secondary = _make_provider(
            fallback_provider_id,
            name="deepseek",
            balance="0.10",
            status=STATUS_HARD_STOP,
            fallback_provider_id=third_id,
        )
        tertiary = _make_provider(
            third_id, name="gemini", balance="100.00", status=STATUS_NORMAL
        )
        mock_redis.get.return_value = None
        mock_db.execute.side_effect = [
            _scalar_result(primary),
            _scalar_result(secondary),
            _scalar_result(tertiary),
        ]

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is not None
        assert decision.provider.provider_id == third_id
        assert decision.is_fallback is True
        assert decision.reason == "fallback"
        assert decision.original_provider_id == provider_id

    @pytest.mark.asyncio
    async def test_unknown_provider_blocks(self, provider_id, mock_db, mock_redis):
        mock_redis.get.return_value = None
        mock_db.execute.return_value = _scalar_result(None)

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is None
        assert decision.should_block is True
        assert decision.reason == "unknown_provider"

    @pytest.mark.asyncio
    async def test_inactive_provider_with_fallback_uses_fallback(
        self, provider_id, fallback_provider_id, mock_db, mock_redis
    ):
        primary = _make_provider(
            provider_id,
            balance="100.00",
            status=STATUS_NORMAL,
            is_active=False,
            fallback_provider_id=fallback_provider_id,
        )
        fallback = _make_provider(
            fallback_provider_id, balance="50.00", status=STATUS_NORMAL
        )
        mock_redis.get.return_value = None
        mock_db.execute.side_effect = [
            _scalar_result(primary),
            _scalar_result(fallback),
        ]

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is not None
        assert decision.provider.provider_id == fallback_provider_id
        assert decision.is_fallback is True

    @pytest.mark.asyncio
    async def test_fallback_cycle_is_broken(
        self, provider_id, fallback_provider_id, mock_db, mock_redis
    ):
        """A → B → A cycle must not loop forever."""
        primary = _make_provider(
            provider_id,
            balance="0",
            status=STATUS_HARD_STOP,
            fallback_provider_id=fallback_provider_id,
        )
        secondary = _make_provider(
            fallback_provider_id,
            balance="0",
            status=STATUS_HARD_STOP,
            fallback_provider_id=provider_id,  # cycle!
        )
        mock_redis.get.return_value = None
        mock_db.execute.side_effect = [
            _scalar_result(primary),
            _scalar_result(secondary),
        ]

        with patch("app.services.provider_balance.get_redis", return_value=mock_redis):
            decision = await resolve_provider_for_request(provider_id, mock_db)

        assert decision.provider is None
        assert decision.should_block is True
        assert decision.reason == "hard_stop_no_fallback"


# ── ProviderRoutingDecision ──────────────────────────────────────────────────


class TestProviderRoutingDecision:
    def test_should_block_when_provider_is_none(self, provider_id):
        d = ProviderRoutingDecision(
            provider=None,
            is_fallback=False,
            reason="hard_stop_no_fallback",
            original_provider_id=provider_id,
        )
        assert d.should_block is True

    def test_should_not_block_when_provider_is_set(
        self, provider_id, fallback_provider_id
    ):
        status = ProviderStatus(
            provider_id=fallback_provider_id,
            name="deepseek",
            status=STATUS_NORMAL,
            balance_usd=Decimal("50"),
            warning_threshold=Decimal("10"),
            hard_stop_threshold=Decimal("2"),
            fallback_provider_id=None,
            is_active=True,
        )
        d = ProviderRoutingDecision(
            provider=status,
            is_fallback=True,
            reason="fallback",
            original_provider_id=provider_id,
        )
        assert d.should_block is False
