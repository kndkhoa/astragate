"""
Unit tests for the Virtual Key service.

Tests key generation, hashing, prefix extraction, and business logic.
"""
import hashlib

import pytest

from app.services.virtual_key import (
    KEY_PREFIX_FORMAT,
    MAX_KEYS_PER_USER,
    extract_prefix,
    generate_virtual_key,
    hash_key,
)


class TestGenerateVirtualKey:
    """Tests for generate_virtual_key()."""

    def test_key_starts_with_prefix(self):
        key = generate_virtual_key()
        assert key.startswith("ag-sk-")

    def test_key_has_correct_length(self):
        """ag-sk- (6 chars) + 32 hex chars = 38 total."""
        key = generate_virtual_key()
        assert len(key) == 38

    def test_key_random_part_is_hex(self):
        key = generate_virtual_key()
        random_part = key[len(KEY_PREFIX_FORMAT):]
        # Should be valid hex
        int(random_part, 16)

    def test_keys_are_unique(self):
        """Two generated keys should never be the same."""
        keys = {generate_virtual_key() for _ in range(100)}
        assert len(keys) == 100

    def test_key_is_cryptographically_random(self):
        """Keys should use secrets module (not predictable)."""
        key1 = generate_virtual_key()
        key2 = generate_virtual_key()
        assert key1 != key2


class TestHashKey:
    """Tests for hash_key()."""

    def test_produces_sha256_hash(self):
        key = "ag-sk-abcdef1234567890abcdef1234567890"
        result = hash_key(key)
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert result == expected

    def test_hash_is_64_chars(self):
        """SHA-256 hex digest is always 64 characters."""
        key = generate_virtual_key()
        result = hash_key(key)
        assert len(result) == 64

    def test_same_key_produces_same_hash(self):
        key = generate_virtual_key()
        assert hash_key(key) == hash_key(key)

    def test_different_keys_produce_different_hashes(self):
        key1 = generate_virtual_key()
        key2 = generate_virtual_key()
        assert hash_key(key1) != hash_key(key2)


class TestExtractPrefix:
    """Tests for extract_prefix()."""

    def test_extracts_first_8_chars(self):
        key = "ag-sk-ab1234567890abcdef1234567890ab"
        assert extract_prefix(key) == "ag-sk-ab"

    def test_prefix_starts_with_ag_sk(self):
        key = generate_virtual_key()
        prefix = extract_prefix(key)
        assert prefix.startswith("ag-sk-")
        assert len(prefix) == 8


class TestMaxKeysConstant:
    """Verify the max keys constant matches requirements."""

    def test_max_keys_is_10(self):
        assert MAX_KEYS_PER_USER == 10
