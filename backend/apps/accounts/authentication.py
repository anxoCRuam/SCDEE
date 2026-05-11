"""
Authentication plugin system (Strategy pattern).

Defines the abstract interface that ALL authentication mechanisms must
implement, and a registry that resolves the active plugin from settings.

Architecture:
    BaseAuthPlugin (abstract)
        ├── JWTAuthPlugin      (v1.0 — implemented in Bloque B)
        └── SSOAuthPlugin      (future — Fase 13)

    Views call `get_auth_plugin()` and work exclusively with
    BaseAuthPlugin. Swapping the concrete implementation requires
    only changing AUTH_PLUGIN_CLASS in settings.

References: RF-1.1, RF-1.5
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class TokenPair:
    """Immutable pair of access + refresh tokens.

    Every auth plugin must return this structure from authenticate()
    and refresh(). The frontend always receives the same shape
    regardless of the underlying auth mechanism.

    Attributes:
        access_token: Short-lived token for API access (5-30 min).
        refresh_token: Long-lived token for renewal (7 days).
    """

    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class TokenPayload:
    """Decoded token payload with business claims.

    This is the "common payload" required by RF-1.1: every plugin
    must produce tokens that decode to this structure, ensuring
    the middleware and permission system work identically.

    Attributes:
        user_id: UUID of the authenticated user.
        organization_id: UUID of the user's organization (None for superadmins).
        is_staff: Whether the user is an organization manager.
        is_superadmin: Whether the user is a system superadmin.
        jti: Unique token identifier (for revocation tracking).
        token_type: Either "access" or "refresh".
    """

    user_id: UUID
    organization_id: UUID | None
    is_staff: bool
    is_superadmin: bool
    jti: str
    token_type: str


class BaseAuthPlugin(abc.ABC):
    """Abstract base for all authentication plugins.

    Every auth mechanism (JWT, SSO, etc.) must implement this interface.
    The contract guarantees:
    1. Uniform token payload (TokenPayload) regardless of backend.
    2. Consistent API for views (authenticate, refresh, revoke).
    3. A validate method for the DRF authentication backend.

    To add a new plugin:
    1. Subclass BaseAuthPlugin.
    2. Implement all abstract methods.
    3. Set AUTH_PLUGIN_CLASS in settings to the dotted path.
    """

    @abc.abstractmethod
    def authenticate(self, email: str, password: str) -> TokenPair:
        """Verify credentials and return a token pair.

        Args:
            email: User's email address.
            password: Plain-text password to verify.

        Returns:
            TokenPair with access and refresh tokens.

        Raises:
            AuthenticationError: If credentials are invalid or user inactive.
        """

    @abc.abstractmethod
    def refresh(self, refresh_token: str) -> TokenPair:
        """Generate a new token pair from a valid refresh token.

        The old refresh token is revoked (rotation).

        Args:
            refresh_token: Current valid refresh token.

        Returns:
            New TokenPair (old refresh token is invalidated).

        Raises:
            AuthenticationError: If the refresh token is invalid or revoked.
        """

    @abc.abstractmethod
    def revoke(self, refresh_token: str) -> None:
        """Revoke a refresh token (logout).

        The token is added to the blacklist and cannot be used again.

        Args:
            refresh_token: The refresh token to invalidate.

        Raises:
            AuthenticationError: If the token is already invalid.
        """

    @abc.abstractmethod
    def validate_access_token(self, token: str) -> TokenPayload:
        """Decode and validate an access token.

        Used by the DRF authentication backend on every request.

        Args:
            token: The raw access token string from the Authorization header.

        Returns:
            TokenPayload with the decoded claims.

        Raises:
            AuthenticationError: If the token is expired, malformed, or revoked.
        """


class AuthenticationError(Exception):
    """Raised when an authentication operation fails.

    Contains a symbolic error code that maps to the API error response.

    Attributes:
        code: Machine-readable error code (e.g. "INVALID_CREDENTIALS").
        detail: Human-readable message for logging (never sent to client).
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


# ── Plugin registry ──────────────────────────────────────────

_plugin_instance: BaseAuthPlugin | None = None


def get_auth_plugin() -> BaseAuthPlugin:
    """Return the configured authentication plugin (singleton).

    Reads AUTH_PLUGIN_CLASS from settings and instantiates it once.
    Subsequent calls return the same instance.

    The setting should be a dotted path to a BaseAuthPlugin subclass:
        AUTH_PLUGIN_CLASS = "apps.accounts.jwt_plugin.JWTAuthPlugin"

    Returns:
        The active BaseAuthPlugin instance.

    Raises:
        ImproperlyConfigured: If the class doesn't exist or isn't a
            subclass of BaseAuthPlugin.
    """
    global _plugin_instance  # noqa: PLW0603

    if _plugin_instance is not None:
        return _plugin_instance

    plugin_class_path = getattr(settings, "AUTH_PLUGIN_CLASS", None)
    if plugin_class_path is None:
        raise _missing_config_error()

    plugin_class = import_string(plugin_class_path)

    if not issubclass(plugin_class, BaseAuthPlugin):
        raise TypeError(f"{plugin_class_path} is not a subclass of BaseAuthPlugin.")

    _plugin_instance = plugin_class()
    return _plugin_instance


def reset_auth_plugin() -> None:
    """Clear the cached plugin instance.

    Only used in tests to allow switching plugins between test cases.
    """
    global _plugin_instance  # noqa: PLW0603
    _plugin_instance = None


def _missing_config_error() -> Exception:
    """Build a descriptive error for missing AUTH_PLUGIN_CLASS setting."""
    from django.core.exceptions import ImproperlyConfigured

    return ImproperlyConfigured(
        "AUTH_PLUGIN_CLASS is not set in Django settings. "
        "Set it to a dotted path of a BaseAuthPlugin subclass, e.g. "
        '"apps.accounts.jwt_plugin.JWTAuthPlugin"'
    )
