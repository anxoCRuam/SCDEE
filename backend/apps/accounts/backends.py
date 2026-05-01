"""
DRF authentication backend for the auth plugin system.

This backend is registered in DRF's DEFAULT_AUTHENTICATION_CLASSES.
On every request, it:
1. Extracts the Bearer token from the Authorization header.
2. Delegates validation to the active auth plugin (BaseAuthPlugin).
3. Returns the authenticated User instance and the decoded TokenPayload.

The User object is then available as `request.user` and the payload
as `request.auth` in all DRF views. The OrganizationMiddleware reads
`request.user.organization_id` to set the tenant context.

References: RF-1.1, RF-1.2
"""

from __future__ import annotations

import logging

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from apps.accounts.authentication import AuthenticationError, get_auth_plugin
from apps.core.tenancy.tenant_context import set_current_organization_id

logger = logging.getLogger(__name__)


class JWTAuthentication(BaseAuthentication):
    """DRF authentication backend using the auth plugin system.

    Registered in settings as:
        DEFAULT_AUTHENTICATION_CLASSES = [
            "apps.accounts.backends.JWTAuthentication",
        ]

    The `authenticate()` method is called by DRF for every request
    that reaches a view with IsAuthenticated permission.
    """

    keyword = "Bearer"

    def authenticate(self, request: Request):
        """Extract and validate the Bearer token from the request.

        Returns:
            Tuple of (user, token_payload) if a valid token is present.
            None if no Authorization header is present (allows other
            backends or AnonymousUser fallback).

        Raises:
            AuthenticationFailed: If a token is present but invalid.
        """
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header:
            return None

        parts = auth_header.split()

        if len(parts) != 2 or parts[0] != self.keyword:
            # Not a Bearer token — skip to next backend or return None.
            return None

        raw_token = parts[1]

        try:
            plugin = get_auth_plugin()
            token_payload = plugin.validate_access_token(raw_token)
        except AuthenticationError as exc:
            # Map plugin errors to DRF's AuthenticationFailed (HTTP 401).
            logger.debug("Authentication failed: %s (%s)", exc.code, exc.detail)
            raise AuthenticationFailed(detail=exc.code, code=exc.code) from exc

        user = self._load_user(token_payload)

        # Set tenant context here — this is the earliest point where we have
        # the authenticated user. Django middleware runs before DRF auth, so
        # OrganizationMiddleware cannot read request.user reliably.
        # The middleware's finally-block still handles clearing the context.
        if not getattr(user, "is_superadmin", False):
            set_current_organization_id(getattr(user, "organization_id", None))

        return (user, token_payload)

    def authenticate_header(self, request: Request) -> str:
        """Return the WWW-Authenticate header value for 401 responses.

        This tells the client that Bearer token authentication is expected.
        """
        return self.keyword

    def _load_user(self, token_payload):
        """Load the User instance from the token's user_id.

        We load from the database on every request (not from the token)
        because user state can change (deactivated, role changed) and
        the token may not reflect the latest state.

        Args:
            token_payload: Decoded TokenPayload from the plugin.

        Returns:
            User model instance.

        Raises:
            AuthenticationFailed: If the user doesn't exist or is inactive.
        """
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        try:
            # Use unfiltered manager: tenant context is not set yet at this point.
            user = user_model.unfiltered.get(pk=token_payload.user_id)
        except user_model.DoesNotExist:
            raise AuthenticationFailed(
                detail="USER_NOT_FOUND",
                code="USER_NOT_FOUND",
            ) from None

        if not user.is_active:
            raise AuthenticationFailed(
                detail="USER_INACTIVE",
                code="USER_INACTIVE",
            )

        return user
