"""
Tests for the DNI encryption service (RF-16.3).

These tests verify:
1. Encrypt → decrypt round-trip returns the original value.
2. Each encryption produces a unique nonce (no reuse).
3. Same plaintext encrypts to different ciphertext (nonce uniqueness).
4. Decryption fails cleanly with wrong key / corrupted data.
5. Master key validation catches misconfiguration.
"""

import os

import pytest
from django.test import SimpleTestCase, override_settings

from apps.accounts.services.encryption import (
    EncryptionError,
    decrypt_dni,
    encrypt_dni,
)

# A valid 32-byte key, hex-encoded (64 hex chars).
VALID_KEY_HEX = "a" * 64


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY_HEX)
class TestEncryptDecryptRoundTrip(SimpleTestCase):
    """Verify that encrypt → decrypt returns the original DNI."""

    def test_simple_dni(self) -> None:
        """Standard Spanish DNI format."""
        dni = "12345678X"
        encrypted_data, nonce = encrypt_dni(dni)
        result = decrypt_dni(encrypted_data, nonce)
        assert result == dni

    def test_dni_with_leading_zeros(self) -> None:
        """DNIs can have leading zeros — they must be preserved."""
        dni = "00123456Y"
        encrypted_data, nonce = encrypt_dni(dni)
        assert decrypt_dni(encrypted_data, nonce) == dni

    def test_nie_format(self) -> None:
        """NIE (foreigner ID) starts with a letter."""
        dni = "X1234567L"
        encrypted_data, nonce = encrypt_dni(dni)
        assert decrypt_dni(encrypted_data, nonce) == dni

    def test_unicode_characters(self) -> None:
        """Edge case: DNI with non-ASCII characters (unlikely but safe)."""
        dni = "ñ1234567Ü"
        encrypted_data, nonce = encrypt_dni(dni)
        assert decrypt_dni(encrypted_data, nonce) == dni


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY_HEX)
class TestNonceUniqueness(SimpleTestCase):
    """Verify that each encryption produces a unique nonce."""

    def test_same_plaintext_different_nonce(self) -> None:
        """Encrypting the same DNI twice must use different nonces."""
        _, nonce1 = encrypt_dni("12345678X")
        _, nonce2 = encrypt_dni("12345678X")
        assert nonce1 != nonce2

    def test_same_plaintext_different_ciphertext(self) -> None:
        """Different nonce → different ciphertext (prevents frequency analysis)."""
        encrypted1, _ = encrypt_dni("12345678X")
        encrypted2, _ = encrypt_dni("12345678X")
        assert encrypted1 != encrypted2

    def test_nonce_is_12_bytes(self) -> None:
        """GCM standard requires 12-byte (96-bit) nonce."""
        _, nonce = encrypt_dni("12345678X")
        assert len(nonce) == 12


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY_HEX)
class TestDecryptionFailures(SimpleTestCase):
    """Verify that decryption fails cleanly on bad input."""

    def test_wrong_key_fails(self) -> None:
        """Decrypting with a different key must raise EncryptionError."""
        encrypted_data, nonce = encrypt_dni("12345678X")

        different_key = "b" * 64
        with (
            override_settings(ENCRYPTION_MASTER_KEY=different_key),
            pytest.raises(EncryptionError),
        ):
            decrypt_dni(encrypted_data, nonce)

    def test_corrupted_ciphertext_fails(self) -> None:
        """Flipping a bit in the ciphertext must be detected by GCM tag."""
        encrypted_data, nonce = encrypt_dni("12345678X")

        # Flip the first byte of ciphertext.
        corrupted = bytes([encrypted_data[0] ^ 0xFF]) + encrypted_data[1:]

        with pytest.raises(EncryptionError):
            decrypt_dni(corrupted, nonce)

    def test_wrong_nonce_fails(self) -> None:
        """Using a different nonce for decryption must fail."""
        encrypted_data, _ = encrypt_dni("12345678X")
        wrong_nonce = os.urandom(12)

        with pytest.raises(EncryptionError):
            decrypt_dni(encrypted_data, wrong_nonce)

    def test_empty_data_fails(self) -> None:
        """Missing encrypted data must raise EncryptionError."""
        with pytest.raises(EncryptionError):
            decrypt_dni(b"", b"some_nonce__")

    def test_none_data_fails(self) -> None:
        """None values must raise EncryptionError."""
        with pytest.raises(EncryptionError):
            decrypt_dni(None, None)  # type: ignore[arg-type]


class TestKeyValidation(SimpleTestCase):
    """Verify that master key misconfiguration is caught early."""

    @override_settings(ENCRYPTION_MASTER_KEY="")
    def test_empty_key_raises(self) -> None:
        with pytest.raises(EncryptionError, match="not configured"):
            encrypt_dni("12345678X")

    @override_settings(ENCRYPTION_MASTER_KEY="not-hex-at-all")
    def test_non_hex_key_raises(self) -> None:
        with pytest.raises(EncryptionError, match="not valid hex"):
            encrypt_dni("12345678X")

    @override_settings(ENCRYPTION_MASTER_KEY="aabb")
    def test_short_key_raises(self) -> None:
        """Key shorter than 32 bytes must be rejected."""
        with pytest.raises(EncryptionError, match="32 bytes"):
            encrypt_dni("12345678X")


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY_HEX)
class TestEdgeCases(SimpleTestCase):
    """Edge cases for the encryption service."""

    def test_empty_plaintext_raises(self) -> None:
        """Empty DNI must be rejected (not silently encrypted)."""
        with pytest.raises(ValueError, match="cannot be empty"):
            encrypt_dni("")

    def test_long_dni_works(self) -> None:
        """Unusually long input should still work (GCM has no practical limit)."""
        long_dni = "X" * 500
        encrypted_data, nonce = encrypt_dni(long_dni)
        assert decrypt_dni(encrypted_data, nonce) == long_dni

    def test_encrypted_data_is_bytes(self) -> None:
        """Output types must be compatible with BinaryField storage."""
        encrypted_data, nonce = encrypt_dni("12345678X")
        assert isinstance(encrypted_data, bytes)
        assert isinstance(nonce, bytes)
