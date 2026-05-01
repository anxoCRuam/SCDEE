"""
AES-256-GCM encryption service for sensitive fields.

Used exclusively for encrypting the DNI (national ID document) field
in the User model. The design prioritizes:

1. Confidentiality: AES-256 encryption prevents reading DNI from DB dumps.
2. Integrity: GCM's authentication tag detects any tampering with the
   ciphertext or associated data.
3. Nonce uniqueness: Each encryption generates a random 12-byte nonce,
   so identical DNIs produce different ciphertext (no frequency analysis).
4. Key separation: The master key lives exclusively in environment
   variables, never in the database or source code.

Storage layout in the User model:
    encrypted_dni: ciphertext || GCM_tag (variable length + 16 bytes)
    dni_nonce: 12 random bytes used for this specific encryption

Key rotation is planned for v2.0 (would require re-encrypting all DNIs
in a migration with both old and new keys).

References: RF-16.3
"""

from __future__ import annotations

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# GCM standard nonce size (96 bits = 12 bytes).
# NIST SP 800-38D recommends this size for random nonces.
_GCM_NONCE_SIZE = 12

# GCM authentication tag size (128 bits = 16 bytes).
# Maximum security — detects any tampering with ciphertext.
_GCM_TAG_SIZE = 16


class EncryptionError(Exception):
    """Raised when encryption or decryption fails.

    Wraps lower-level cryptographic errors with context about
    what went wrong (invalid key, corrupted data, etc.).
    """


def _get_master_key() -> bytes:
    """Read and validate the AES-256 master key from settings.

    The key is stored as a hex-encoded string in the ENCRYPTION_MASTER_KEY
    environment variable. This function decodes it to raw bytes and
    validates its length.

    Returns:
        32 bytes (256 bits) for AES-256.

    Raises:
        EncryptionError: If the key is missing, not hex, or wrong length.
    """
    hex_key = getattr(settings, "ENCRYPTION_MASTER_KEY", None)
    if not hex_key:
        raise EncryptionError(
            "ENCRYPTION_MASTER_KEY is not configured. "
            "Set it as a 64-character hex string in your environment."
        )

    try:
        key_bytes = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise EncryptionError(
            "ENCRYPTION_MASTER_KEY is not valid hex. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        ) from exc

    if len(key_bytes) != 32:
        raise EncryptionError(
            f"ENCRYPTION_MASTER_KEY must be 32 bytes (64 hex chars), got {len(key_bytes)} bytes."
        )

    return key_bytes


def encrypt_dni(plaintext: str) -> tuple[bytes, bytes]:
    """Encrypt a DNI string with AES-256-GCM.

    Each call generates a unique random nonce, so encrypting the same
    DNI twice produces different ciphertext. This prevents frequency
    analysis attacks against the encrypted column.

    Args:
        plaintext: The raw DNI string (e.g. "12345678X").

    Returns:
        Tuple of (encrypted_data, nonce):
            - encrypted_data: ciphertext + 16-byte GCM tag (bytes)
            - nonce: 12-byte random nonce used (bytes)

    Raises:
        EncryptionError: If the master key is misconfigured.
        ValueError: If plaintext is empty.
    """
    if not plaintext:
        raise ValueError("DNI plaintext cannot be empty.")

    # Import here to fail fast with a clear message if cryptography
    # is not installed, rather than at module import time.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_master_key()
    nonce = os.urandom(_GCM_NONCE_SIZE)

    aesgcm = AESGCM(key)
    # GCM encrypt returns ciphertext || tag (tag is appended automatically).
    encrypted_data = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    return encrypted_data, nonce


def decrypt_dni(encrypted_data: bytes, nonce: bytes) -> str:
    """Decrypt a DNI from its encrypted form.

    Verifies the GCM authentication tag to ensure the data hasn't
    been tampered with in the database.

    Args:
        encrypted_data: The ciphertext + GCM tag as stored in the DB.
        nonce: The 12-byte nonce used during encryption.

    Returns:
        The original DNI string.

    Raises:
        EncryptionError: If decryption fails (wrong key, corrupted data,
            tampered ciphertext). The specific cause is deliberately
            not exposed to prevent oracle attacks.
    """
    if not encrypted_data or not nonce:
        raise EncryptionError("Cannot decrypt: missing encrypted data or nonce.")

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_master_key()
    aesgcm = AESGCM(key)

    try:
        plaintext_bytes = aesgcm.decrypt(nonce, bytes(encrypted_data), None)
    except InvalidTag as exc:
        # Deliberately vague error message — don't leak whether the
        # failure was due to wrong key vs corrupted data.
        raise EncryptionError(
            "DNI decryption failed. The data may be corrupted or the "
            "encryption key may have changed."
        ) from exc
    except Exception as exc:
        raise EncryptionError(f"DNI decryption failed: {type(exc).__name__}") from exc

    return plaintext_bytes.decode("utf-8")
