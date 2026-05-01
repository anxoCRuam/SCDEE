"""
Tests for the password generation service (RF-2.7).

These tests verify:
1. Generated passwords meet all complexity requirements.
2. Length constraints are respected.
3. Generated passwords are sufficiently random (no duplicates in batch).
4. The validation function correctly identifies missing requirements.
"""

import string

import pytest

from apps.accounts.services.password import (
    DEFAULT_PASSWORD_LENGTH,
    generate_secure_password,
    validate_password_complexity,
)


class TestGenerateSecurePassword:
    """Verify that generated passwords meet RF-2.7 complexity requirements."""

    def test_default_length(self) -> None:
        """Default password should be 14 characters."""
        password = generate_secure_password()
        assert len(password) == DEFAULT_PASSWORD_LENGTH
        assert DEFAULT_PASSWORD_LENGTH == 14

    def test_custom_length(self) -> None:
        """Caller can request a longer password."""
        password = generate_secure_password(length=20)
        assert len(password) == 20

    def test_minimum_length_enforced(self) -> None:
        """Passwords shorter than minimum must be rejected."""
        with pytest.raises(ValueError, match="at least"):
            generate_secure_password(length=5)

    def test_has_uppercase(self) -> None:
        """Every generated password must contain at least one uppercase letter."""
        # Run multiple times to reduce chance of false positive.
        for _ in range(50):
            password = generate_secure_password()
            assert any(
                c in string.ascii_uppercase for c in password
            ), f"Password missing uppercase: {password}"

    def test_has_lowercase(self) -> None:
        """Every generated password must contain at least one lowercase letter."""
        for _ in range(50):
            password = generate_secure_password()
            assert any(
                c in string.ascii_lowercase for c in password
            ), f"Password missing lowercase: {password}"

    def test_has_digit(self) -> None:
        """Every generated password must contain at least one digit."""
        for _ in range(50):
            password = generate_secure_password()
            assert any(c in string.digits for c in password), f"Password missing digit: {password}"

    def test_has_special_character(self) -> None:
        """Every generated password must contain at least one special character."""
        special = set("!@#%^&*()-_=+[]{}|;:,.<>?")
        for _ in range(50):
            password = generate_secure_password()
            assert any(
                c in special for c in password
            ), f"Password missing special char: {password}"

    def test_passes_own_validation(self) -> None:
        """Generated passwords must always pass complexity validation."""
        for _ in range(100):
            password = generate_secure_password()
            errors = validate_password_complexity(password)
            assert errors == [], f"Password {password!r} failed validation: {errors}"

    def test_no_duplicates_in_batch(self) -> None:
        """100 generated passwords should all be unique (randomness check)."""
        passwords = {generate_secure_password() for _ in range(100)}
        assert len(passwords) == 100, "Duplicate password detected — RNG may be broken."


class TestValidatePasswordComplexity:
    """Verify the validation function catches missing requirements."""

    def test_valid_password(self) -> None:
        errors = validate_password_complexity("Abcdefgh1!----")
        assert errors == []

    def test_too_short(self) -> None:
        errors = validate_password_complexity("Ab1!")
        assert "TOO_SHORT" in errors

    def test_no_uppercase(self) -> None:
        errors = validate_password_complexity("abcdefghij1!")
        assert "NO_UPPERCASE" in errors
        assert "NO_LOWERCASE" not in errors

    def test_no_lowercase(self) -> None:
        errors = validate_password_complexity("ABCDEFGHIJ1!")
        assert "NO_LOWERCASE" in errors

    def test_no_digit(self) -> None:
        errors = validate_password_complexity("Abcdefghij!!")
        assert "NO_DIGIT" in errors

    def test_no_special(self) -> None:
        errors = validate_password_complexity("Abcdefghij12")
        assert "NO_SPECIAL" in errors

    def test_all_missing(self) -> None:
        """A password of only spaces fails all checks."""
        errors = validate_password_complexity("          ")
        assert "NO_UPPERCASE" in errors
        assert "NO_LOWERCASE" in errors
        assert "NO_DIGIT" in errors
        assert "NO_SPECIAL" in errors

    def test_empty_password(self) -> None:
        errors = validate_password_complexity("")
        assert "TOO_SHORT" in errors
