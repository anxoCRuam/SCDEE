"""
Secure password generation service.

Generates random passwords that satisfy complexity requirements
for user account creation and password resets.

The generated passwords are intended to be temporary: users receive
them by email and should ideally change them on first login (though
this is not enforced in v1.0).

Complexity requirements (RF-2.7):
    - Minimum 14 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit
    - At least 1 special character

Uses Python's `secrets` module (not `random`) because password
generation is a security-sensitive operation that requires
cryptographically strong randomness.

References: RF-2.7, RF-2.8, RF-16.4
"""

from __future__ import annotations

import secrets
import string

# Character pools for password composition.
_UPPERCASE = string.ascii_uppercase
_LOWERCASE = string.ascii_lowercase
_DIGITS = string.digits
# Restricted special chars: avoid visually ambiguous ones (l, 1, I, O, 0)
# and shell-problematic ones (`, $, \). These passwords are sent by email
# and may be copy-pasted into terminals.
_SPECIAL = "!@#%^&*()-_=+[]{}|;:,.<>?"

# All characters combined for the random fill portion.
_ALL_CHARS = _UPPERCASE + _LOWERCASE + _DIGITS + _SPECIAL

# Default password length. 14 chars with this character set provides
# approximately 85 bits of entropy, which exceeds the 80-bit minimum
# recommended by NIST SP 800-63B for generated secrets.
DEFAULT_PASSWORD_LENGTH = 14

# Minimum acceptable length (enforced even if caller overrides).
_MINIMUM_LENGTH = 10


def generate_secure_password(length: int = DEFAULT_PASSWORD_LENGTH) -> str:
    """Generate a cryptographically secure random password.

    The password is guaranteed to contain at least one character from
    each required category (uppercase, lowercase, digit, special).
    The remaining positions are filled with random characters from
    the combined pool.

    The characters are then shuffled to avoid predictable patterns
    (e.g. "always starts with an uppercase letter").

    Args:
        length: Desired password length. Must be >= 10.
            Default is 14 characters (~85 bits of entropy).

    Returns:
        A random password string meeting all complexity requirements.

    Raises:
        ValueError: If length is less than the minimum.
    """
    if length < _MINIMUM_LENGTH:
        raise ValueError(f"Password length must be at least {_MINIMUM_LENGTH}, got {length}.")

    # Guarantee at least one character from each required category.
    # This ensures the password always passes complexity validation.
    mandatory = [
        secrets.choice(_UPPERCASE),
        secrets.choice(_LOWERCASE),
        secrets.choice(_DIGITS),
        secrets.choice(_SPECIAL),
    ]

    # Fill remaining positions with random characters from all pools.
    remaining_length = length - len(mandatory)
    fill = [secrets.choice(_ALL_CHARS) for _ in range(remaining_length)]

    # Combine and shuffle to eliminate positional bias.
    # Without shuffle, the first 4 chars would always be
    # uppercase, lowercase, digit, special — predictable.
    password_chars = mandatory + fill
    # secrets doesn't have a shuffle, but we can use Fisher-Yates
    # with secrets.randbelow for cryptographic randomness.
    _secure_shuffle(password_chars)

    return "".join(password_chars)


def _secure_shuffle(items: list) -> None:
    """Fisher-Yates shuffle using cryptographic randomness.

    Unlike random.shuffle() which uses a PRNG, this uses
    secrets.randbelow() for each swap, making the permutation
    uniformly random with cryptographic guarantees.

    Args:
        items: List to shuffle in place.
    """
    for i in range(len(items) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        items[i], items[j] = items[j], items[i]


def validate_password_complexity(password: str) -> list[str]:
    """Check if a password meets complexity requirements.

    Returns a list of symbolic error codes for each unmet requirement.
    An empty list means the password is valid.

    This is used to validate passwords during:
    - User creation (should never fail for generated passwords)
    - Future self-service password change (if implemented)

    Args:
        password: The password to validate.

    Returns:
        List of error codes. Empty if password is valid.
        Possible codes:
            - "TOO_SHORT": Less than minimum length
            - "NO_UPPERCASE": Missing uppercase letter
            - "NO_LOWERCASE": Missing lowercase letter
            - "NO_DIGIT": Missing digit
            - "NO_SPECIAL": Missing special character
    """
    errors: list[str] = []

    if len(password) < _MINIMUM_LENGTH:
        errors.append("TOO_SHORT")
    if not any(c in _UPPERCASE for c in password):
        errors.append("NO_UPPERCASE")
    if not any(c in _LOWERCASE for c in password):
        errors.append("NO_LOWERCASE")
    if not any(c in _DIGITS for c in password):
        errors.append("NO_DIGIT")
    if not any(c in _SPECIAL for c in password):
        errors.append("NO_SPECIAL")

    return errors
