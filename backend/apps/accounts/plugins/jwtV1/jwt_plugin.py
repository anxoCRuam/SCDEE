"""
JWT authentication plugin — concrete implementation for v1.0.

This plugin implements BaseAuthPlugin using:
- PyJWT for token encoding/decoding (HS256 signing with SECRET_KEY).
- Redis for token revocation (blacklist with TTL).

Token structure:
    Access token (short-lived, 15 min default):
        - user_id: UUID of the authenticated user
        - organization_id: UUID of the user's org (null for superadmins)
        - is_staff: boolean (organization manager)
        - is_superadmin: boolean (system admin)
        - jti: unique token ID (for revocation)
        - token_type: "access"
        - user_gen: token generation counter (for bulk invalidation)
        - exp: expiration timestamp
        - iat: issued-at timestamp

    Refresh token (long-lived, 7 days default):
        - user_id: UUID of the authenticated user
        - jti: unique token ID (for revocation)
        - token_type: "refresh"
        - user_gen: token generation counter
        - exp: expiration timestamp
        - iat: issued-at timestamp

Why HS256 and not RS256?
    In v1.0, the same backend both issues and validates tokens.
    HS256 (symmetric) is simpler and faster for this single-service
    architecture. RS256 (asymmetric) would be needed if a separate
    service validated tokens without access to the signing key.
    The auth plugin pattern allows switching to RS256 in the future.

References: RF-1.2, RF-1.3, RF-1.4
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model

from apps.accounts.authentication import (
    AuthenticationError,
    BaseAuthPlugin,
    TokenPair,
    TokenPayload,
)
from apps.accounts.blacklist import (
    add_to_blacklist,
    get_user_token_generation,
    is_blacklisted,
)

logger = logging.getLogger(__name__)

User = get_user_model()


class JWTAuthPlugin(BaseAuthPlugin):
    """JWT-based authentication using PyJWT + Redis blacklist.

    Reads configuration from Django settings:
        - SECRET_KEY: signing key for HS256
        - JWT_ACCESS_TOKEN_LIFETIME_MINUTES: access token TTL (default 15)
        - JWT_REFRESH_TOKEN_LIFETIME_DAYS: refresh token TTL (default 7)
        - JWT_ALGORITHM: signing algorithm (default HS256)
    """

    def authenticate(self, email: str, password: str) -> TokenPair:
        """Verify email + password and return a token pair.

        Args:
            email: User's email address.
            password: Plain-text password to verify against stored hash.

        Returns:
            TokenPair with access and refresh tokens.

        Raises:
            AuthenticationError: INVALID_CREDENTIALS if email/password wrong.
            AuthenticationError: USER_INACTIVE if user is deactivated.
            AuthenticationError: ORG_INACTIVE if organization is deactivated.
        """
        user = self._get_user_by_email(email)

        if not user.check_password(password):
            raise AuthenticationError(
                code="INVALID_CREDENTIALS",
                detail=f"Invalid password for {email}.",
            )

        self._validate_user_can_authenticate(user)

        return self._generate_token_pair(user)

    def refresh(self, refresh_token: str) -> TokenPair:
        """Generate a new token pair from a valid refresh token.

        The old refresh token is blacklisted (rotation) to prevent reuse.

        Args:
            refresh_token: Current valid refresh token string.

        Returns:
            New TokenPair.

        Raises:
            AuthenticationError: If the refresh token is expired, invalid,
                or already revoked.
        """
        payload = self._decode_token(refresh_token)

        if payload.get("token_type") != "refresh":
            raise AuthenticationError(
                code="INVALID_TOKEN_TYPE",
                detail="Expected a refresh token.",
            )

        jti = payload.get("jti", "")
        if is_blacklisted(jti):
            raise AuthenticationError(
                code="TOKEN_REVOKED",
                detail="This refresh token has been revoked.",
            )

        # Check user generation counter for bulk invalidation.
        user_id = payload.get("user_id", "")
        token_gen = payload.get("user_gen", 0)
        current_gen = get_user_token_generation(str(user_id))
        if token_gen < current_gen:
            raise AuthenticationError(
                code="TOKEN_REVOKED",
                detail="All tokens for this user have been invalidated.",
            )

        # Load user to ensure they're still active.
        user = self._get_user_by_id(user_id)
        self._validate_user_can_authenticate(user)

        # Blacklist the old refresh token (rotation).
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        remaining = exp - datetime.now(tz=UTC)
        add_to_blacklist(jti, remaining)

        return self._generate_token_pair(user)

    def revoke(self, refresh_token: str, access_token: str | None = None) -> None:
        """Revoke a refresh token (logout).

        Args:
            refresh_token: The refresh token to invalidate.
            access_token:  If provided (extracted from the Authorization header
                during logout), also blacklist it so it cannot be used anymore.
                This closes the window where a stolen access token remains
                active after its owner logged out.
        """
        self._revoke_token_by_type(refresh_token, expected_type="refresh")

        if access_token:
            self._revoke_token_by_type(access_token, expected_type="access")

    def validate_access_token(self, token: str) -> TokenPayload:
        """Decode and validate an access token for request authentication.

        Called by the DRF authentication backend on every request.

        Args:
            token: Raw access token from the Authorization header.

        Returns:
            TokenPayload with decoded claims.

        Raises:
            AuthenticationError: If the token is invalid, expired, or revoked.
        """
        payload = self._decode_token(token)

        if payload.get("token_type") != "access":
            raise AuthenticationError(
                code="INVALID_TOKEN_TYPE",
                detail="Expected an access token.",
            )

        jti = payload.get("jti", "")
        if is_blacklisted(jti):
            raise AuthenticationError(
                code="TOKEN_REVOKED",
                detail="This access token has been revoked.",
            )

        # Check user generation counter.
        user_id = payload.get("user_id", "")
        token_gen = payload.get("user_gen", 0)
        current_gen = get_user_token_generation(str(user_id))
        if token_gen < current_gen:
            raise AuthenticationError(
                code="TOKEN_REVOKED",
                detail="All tokens for this user have been invalidated.",
            )

        org_id_raw = payload.get("organization_id")

        return TokenPayload(
            user_id=uuid.UUID(str(user_id)),
            organization_id=uuid.UUID(str(org_id_raw)) if org_id_raw else None,
            is_staff=payload.get("is_staff", False),
            is_superadmin=payload.get("is_superadmin", False),
            jti=jti,
            token_type="access",  # noqa: S106
        )

    # ── Private helpers ──────────────────────────────────────

    def _revoke_token_by_type(self, token: str, expected_type: str) -> None:
        """Decode a token and blacklist it if it matches the expected type."""
        try:
            payload = self._decode_token(token)
        except AuthenticationError:
            # If the token is already invalid, there's nothing to revoke.
            return

        if payload.get("token_type") != expected_type:
            # Not the type we're trying to revoke — ignore silently.
            return

        jti = payload.get("jti", "")
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        remaining = exp - datetime.now(tz=UTC)
        add_to_blacklist(jti, remaining)

    def _generate_token_pair(self, user: User) -> TokenPair:
        """Create a new access + refresh token pair for a user."""
        now = datetime.now(tz=UTC)
        user_gen = get_user_token_generation(str(user.pk))

        access_payload = {
            "user_id": str(user.pk),
            "organization_id": str(user.organization_id) if user.organization_id else None,
            "is_staff": user.is_staff,
            "is_superadmin": user.is_superadmin,
            "jti": uuid.uuid4().hex,
            "token_type": "access",
            "user_gen": user_gen,
            "iat": now,
            "exp": now + timedelta(minutes=self._access_lifetime_minutes),
        }

        refresh_payload = {
            "user_id": str(user.pk),
            "jti": uuid.uuid4().hex,
            "token_type": "refresh",
            "user_gen": user_gen,
            "iat": now,
            "exp": now + timedelta(days=self._refresh_lifetime_days),
        }

        access_token = jwt.encode(
            access_payload,
            self._signing_key,
            algorithm=self._algorithm,
        )
        refresh_token = jwt.encode(
            refresh_payload,
            self._signing_key,
            algorithm=self._algorithm,
        )

        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    def _decode_token(self, token: str) -> dict:
        """Decode and verify a JWT token's signature and expiration.

        Args:
            token: Raw JWT string.

        Returns:
            Decoded payload dict.

        Raises:
            AuthenticationError: If the token is expired, malformed, or
                has an invalid signature.
        """
        try:
            return jwt.decode(
                token,
                self._signing_key,
                algorithms=[self._algorithm],
                options={"require": ["exp", "iat", "jti", "user_id", "token_type"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError(
                code="TOKEN_EXPIRED",
                detail="The token has expired.",
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(
                code="INVALID_TOKEN",
                detail=f"Token validation failed: {exc}",
            ) from exc

    def _get_user_by_email(self, email: str):
        """Load user by email, raising AuthenticationError if not found."""
        try:
            return User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise AuthenticationError(
                code="INVALID_CREDENTIALS",
                detail=f"No user with email {email}.",
            ) from exc

    def _get_user_by_id(self, user_id: str):
        """Load user by ID, raising AuthenticationError if not found."""
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            raise AuthenticationError(
                code="USER_NOT_FOUND",
                detail=f"User {user_id} does not exist.",
            ) from exc

    def _validate_user_can_authenticate(self, user) -> None:
        """Check that the user and their organization are active."""
        if not user.is_active:
            raise AuthenticationError(
                code="USER_INACTIVE",
                detail=f"User {user.email} is deactivated.",
            )

        # Superadmins don't have an organization.
        if user.organization and not user.organization.is_active:
            raise AuthenticationError(
                code="ORG_INACTIVE",
                detail=f"Organization {user.organization.name} is deactivated.",
            )

    # ── Settings accessors (read once, cached on instance) ───

    @property
    def _signing_key(self) -> str:
        return settings.SECRET_KEY

    @property
    def _algorithm(self) -> str:
        return getattr(settings, "JWT_ALGORITHM", "HS256")

    @property
    def _access_lifetime_minutes(self) -> int:
        return getattr(settings, "JWT_ACCESS_TOKEN_LIFETIME_MINUTES", 15)

    @property
    def _refresh_lifetime_days(self) -> int:
        return getattr(settings, "JWT_REFRESH_TOKEN_LIFETIME_DAYS", 7)
