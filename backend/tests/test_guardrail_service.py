"""
Unit tests for the Guardrail service.

Tests keyword loading, Redis caching, input/output scanning,
violation recording, and cache invalidation.
"""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.guardrail import (
    CONTENT_SNIPPET_MAX_LENGTH,
    REDIS_GUARDRAIL_KEY,
    REDIS_GUARDRAIL_TTL,
    GuardrailResult,
    check_input,
    check_output,
    invalidate_keyword_cache,
    record_violation,
    _scan_text,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


@pytest.fixture
def sample_keywords():
    """Sample keyword list as stored in cache."""
    return [
        {"keyword": "banned_word", "scope": "both"},
        {"keyword": "input_only", "scope": "input"},
        {"keyword": "output_only", "scope": "output"},
    ]


def _make_scalars_result(rows):
    """Helper to create a mock execute result with scalars().all()."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = rows
    result.scalars.return_value = scalars_mock
    return result


def _make_keyword_row(keyword: str, scope: str):
    """Create a mock GuardrailKeyword row."""
    row = MagicMock()
    row.keyword = keyword
    row.scope = scope
    row.is_active = True
    return row


# ── Tests: _scan_text ─────────────────────────────────────────────────────────


class TestScanText:
    """Tests for the core text scanning logic."""

    def test_no_violation_when_text_is_clean(self):
        keywords = [{"keyword": "banned", "scope": "both"}]
        result = _scan_text("This is a clean message", keywords)
        assert result.violated is False
        assert result.keyword_matched is None
        assert result.content_snippet is None

    def test_detects_keyword_match(self):
        keywords = [{"keyword": "banned", "scope": "both"}]
        result = _scan_text("This contains banned content", keywords)
        assert result.violated is True
        assert result.keyword_matched == "banned"
        assert result.content_snippet is not None

    def test_case_insensitive_matching(self):
        keywords = [{"keyword": "BANNED", "scope": "both"}]
        result = _scan_text("this contains banned content", keywords)
        assert result.violated is True
        assert result.keyword_matched == "BANNED"

    def test_case_insensitive_text(self):
        keywords = [{"keyword": "banned", "scope": "both"}]
        result = _scan_text("This contains BANNED content", keywords)
        assert result.violated is True

    def test_returns_first_match(self):
        keywords = [
            {"keyword": "first", "scope": "both"},
            {"keyword": "second", "scope": "both"},
        ]
        result = _scan_text("text with first and second keywords", keywords)
        assert result.violated is True
        assert result.keyword_matched == "first"

    def test_snippet_truncated_to_100_chars(self):
        keywords = [{"keyword": "bad", "scope": "both"}]
        long_text = "bad " + "x" * 200
        result = _scan_text(long_text, keywords)
        assert result.violated is True
        assert len(result.content_snippet) == CONTENT_SNIPPET_MAX_LENGTH + 3  # +3 for "..."
        assert result.content_snippet.endswith("...")

    def test_snippet_not_truncated_for_short_text(self):
        keywords = [{"keyword": "bad", "scope": "both"}]
        short_text = "bad word"
        result = _scan_text(short_text, keywords)
        assert result.violated is True
        assert result.content_snippet == "bad word"
        assert not result.content_snippet.endswith("...")

    def test_empty_text_returns_no_violation(self):
        keywords = [{"keyword": "banned", "scope": "both"}]
        result = _scan_text("", keywords)
        assert result.violated is False

    def test_empty_keywords_returns_no_violation(self):
        result = _scan_text("some text with anything", [])
        assert result.violated is False

    def test_partial_word_match(self):
        """Keywords match as substrings (not whole words only)."""
        keywords = [{"keyword": "ban", "scope": "both"}]
        result = _scan_text("this is banned content", keywords)
        assert result.violated is True


# ── Tests: check_input ────────────────────────────────────────────────────────


class TestCheckInput:
    """Tests for check_input() — filters by 'input' and 'both' scope."""

    @pytest.mark.asyncio
    async def test_uses_cached_keywords(self, mock_redis, mock_db, sample_keywords):
        """When Redis has cached keywords, use them without DB query."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_input("contains banned_word here", mock_db)

        assert result.violated is True
        assert result.keyword_matched == "banned_word"
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_loads_from_db_on_cache_miss(self, mock_redis, mock_db):
        """When cache misses, load from DB and cache the result."""
        mock_redis.get.return_value = None
        rows = [
            _make_keyword_row("forbidden", "input"),
        ]
        mock_db.execute.return_value = _make_scalars_result(rows)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_input("this is forbidden", mock_db)

        assert result.violated is True
        assert result.keyword_matched == "forbidden"
        # Should cache the keywords
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_input_scope_keywords(self, mock_redis, mock_db, sample_keywords):
        """Only keywords with scope 'input' or 'both' are checked."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            # "output_only" keyword should NOT trigger on input check
            result = await check_input("contains output_only here", mock_db)

        assert result.violated is False

    @pytest.mark.asyncio
    async def test_input_scope_keyword_triggers(self, mock_redis, mock_db, sample_keywords):
        """Keywords with scope 'input' trigger on input check."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_input("contains input_only here", mock_db)

        assert result.violated is True
        assert result.keyword_matched == "input_only"

    @pytest.mark.asyncio
    async def test_both_scope_keyword_triggers_on_input(self, mock_redis, mock_db, sample_keywords):
        """Keywords with scope 'both' trigger on input check."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_input("contains banned_word here", mock_db)

        assert result.violated is True
        assert result.keyword_matched == "banned_word"

    @pytest.mark.asyncio
    async def test_clean_input_passes(self, mock_redis, mock_db, sample_keywords):
        """Clean text passes the input guardrail."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_input("this is a perfectly fine message", mock_db)

        assert result.violated is False


# ── Tests: check_output ───────────────────────────────────────────────────────


class TestCheckOutput:
    """Tests for check_output() — filters by 'output' and 'both' scope."""

    @pytest.mark.asyncio
    async def test_filters_output_scope_keywords(self, mock_redis, mock_db, sample_keywords):
        """Only keywords with scope 'output' or 'both' are checked."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            # "input_only" keyword should NOT trigger on output check
            result = await check_output("contains input_only here", mock_db)

        assert result.violated is False

    @pytest.mark.asyncio
    async def test_output_scope_keyword_triggers(self, mock_redis, mock_db, sample_keywords):
        """Keywords with scope 'output' trigger on output check."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_output("contains output_only here", mock_db)

        assert result.violated is True
        assert result.keyword_matched == "output_only"

    @pytest.mark.asyncio
    async def test_both_scope_keyword_triggers_on_output(self, mock_redis, mock_db, sample_keywords):
        """Keywords with scope 'both' trigger on output check."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_output("contains banned_word here", mock_db)

        assert result.violated is True
        assert result.keyword_matched == "banned_word"

    @pytest.mark.asyncio
    async def test_clean_output_passes(self, mock_redis, mock_db, sample_keywords):
        """Clean text passes the output guardrail."""
        mock_redis.get.return_value = json.dumps(sample_keywords)

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            result = await check_output("this is a perfectly fine response", mock_db)

        assert result.violated is False


# ── Tests: record_violation ───────────────────────────────────────────────────


class TestRecordViolation:
    """Tests for writing guardrail_events records."""

    @pytest.mark.asyncio
    async def test_records_violation_event(self, mock_db):
        """A violation result writes a guardrail_events record."""
        vk_id = uuid.uuid4()
        user_id = uuid.uuid4()
        result = GuardrailResult(
            violated=True,
            keyword_matched="banned",
            content_snippet="some banned content",
        )

        await record_violation(
            result=result,
            direction="input",
            db=mock_db,
            virtual_key_id=vk_id,
            user_id=user_id,
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        event = mock_db.add.call_args[0][0]
        assert event.direction == "input"
        assert event.keyword_matched == "banned"
        assert event.content_snippet == "some banned content"
        assert event.virtual_key_id == vk_id
        assert event.user_id == user_id

    @pytest.mark.asyncio
    async def test_does_not_record_when_no_violation(self, mock_db):
        """No record is written when there's no violation."""
        result = GuardrailResult(violated=False)

        await record_violation(
            result=result,
            direction="input",
            db=mock_db,
        )

        mock_db.add.assert_not_called()
        mock_db.flush.assert_not_called()


# ── Tests: invalidate_keyword_cache ───────────────────────────────────────────


class TestInvalidateKeywordCache:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_deletes_redis_key(self, mock_redis):
        """Invalidation deletes the guardrails:keywords key."""
        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            await invalidate_keyword_cache()

        mock_redis.delete.assert_called_once_with(REDIS_GUARDRAIL_KEY)

    @pytest.mark.asyncio
    async def test_handles_redis_failure_gracefully(self, mock_redis):
        """Cache invalidation failure is non-fatal."""
        mock_redis.delete.side_effect = Exception("Redis down")

        with patch("app.services.guardrail.get_redis", return_value=mock_redis):
            # Should not raise
            await invalidate_keyword_cache()


# ── Tests: Constants ──────────────────────────────────────────────────────────


class TestConstants:
    """Verify service constants match design requirements."""

    def test_cache_key_name(self):
        assert REDIS_GUARDRAIL_KEY == "guardrails:keywords"

    def test_cache_ttl_is_300_seconds(self):
        assert REDIS_GUARDRAIL_TTL == 300

    def test_snippet_max_length_is_100(self):
        assert CONTENT_SNIPPET_MAX_LENGTH == 100
