"""
Authentication API views.

Three endpoints:
    POST /api/v1/auth/login/   — Authenticate with email + password (RF-1.2)
    POST /api/v1/auth/refresh/ — Renew tokens from refresh token (RF-1.3)
    POST /api/v1/auth/logout/  — Revoke refresh token (RF-1.4)

Design decision — APIView vs ViewSet:
    APIView is the correct choice here because these are action-based
    endpoints, not CRUD operations on a resource. ViewSet would force
    us into a resource metaphor (list/create/retrieve/update/delete)
    that doesn't fit authentication semantics.

All three endpoints are public (``AllowAny``) because the user is not
yet authenticated when calling login, and refresh/logout only need
the token in the request body (not in the Authorization header).
The OpenAPI postprocessing hook detects the public security marker
(``security: [{}]``) and skips the 401/403 generic injections.

References: RF-1.2, RF-1.3, RF-1.4, RF-16.1.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import AuthenticationError, get_auth_plugin
from apps.accounts.openapi import (
    LOGIN_REQUEST_EXAMPLE,
    LOGIN_RESPONSE_EXAMPLE,
    LOGOUT_REQUEST_EXAMPLE,
    REFRESH_REQUEST_EXAMPLE,
    REFRESH_RESPONSE_EXAMPLE,
)
from apps.accounts.serializers.auth import (
    LoginRequestSerializer,
    LogoutRequestSerializer,
    RefreshRequestSerializer,
    TokenPairResponseSerializer,
)
from apps.audit.services import (
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
    LOGOUT,
    get_client_ip,
    log_event,
)
from apps.core.openapi.openapi import ErrorCode, error_response, validation_error_response
from apps.core.throttling import LoginRateThrottle

logger = logging.getLogger(__name__)


class LoginView(APIView):
    """Authenticate a user with email and password.

    On success, returns an access token (short-lived) and a refresh
    token (long-lived). The access token is used in the Authorization
    header for subsequent API requests. The refresh token is used to
    obtain new tokens without re-entering credentials.

    On failure, returns HTTP 401 with a symbolic error code identifying
    the precise reason (``INVALID_CREDENTIALS``, ``USER_INACTIVE`` or
    ``ORG_INACTIVE``) so the frontend can react accordingly.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No auth needed for login.
    # Brute-force protection (RNF-6): 10 login attempts per minute per IP.
    throttle_classes = [LoginRateThrottle]

    @extend_schema(
        operation_id="auth_login",
        tags=["Authentication"],
        summary="Authenticate with email and password (RF-1.2)",
        description=(
            "**Authenticate against the active auth plugin and emit a JWT pair.**\n\n"
            "The plugin is configured by the ``AUTH_PLUGIN_CLASS`` setting. In "
            "v1.0 it is ``JWTAuthPlugin``; future versions may swap in an SSO "
            "plugin without changing this endpoint's contract (RF-1.1, RF-1.5).\n\n"
            "**Process**\n\n"
            "1. Validate the request body shape (``email`` is RFC-5322; "
            "``password`` is non-empty).\n"
            "2. Look up the user by email; reject if missing, inactive, or "
            "the organisation is inactive.\n"
            "3. Verify the password against the stored hash (Argon2 by "
            "default — see RF-16.4).\n"
            "4. Mint an access token (``JWT_ACCESS_TOKEN_LIFETIME_MINUTES``, "
            "default 15 min) and a refresh token "
            "(``JWT_REFRESH_TOKEN_LIFETIME_DAYS``, default 7 days).\n"
            "5. Append a ``LOGIN_SUCCESS`` (or ``LOGIN_FAILURE`` on error) "
            "row to the audit log (RF-16.1).\n\n"
            "**Output**\n\n"
            "The access token's payload includes ``user_id``, ``organization_id`` "
            "and ``is_staff`` so the multi-tenant middleware can resolve the "
            "tenant on every subsequent request without an extra DB lookup.\n\n"
            "**Rate limiting**\n\n"
            "10 requests per minute per IP, regardless of organisation. Exceeding "
            "this returns HTTP 429."
        ),
        request=LoginRequestSerializer,
        responses={
            200: TokenPairResponseSerializer,
            400: validation_error_response(
                {
                    "email": ["FIELD_REQUIRED", "INVALID"],
                    "password": ["FIELD_REQUIRED", "BLANK"],
                },
            ),
            401: error_response(
                [
                    ErrorCode.INVALID_CREDENTIALS,
                    ErrorCode.USER_INACTIVE,
                    ErrorCode.ORG_INACTIVE,
                ],
                status_code=401,
                description=(
                    "Login refused. ``INVALID_CREDENTIALS`` is also returned "
                    "for unknown e-mails to avoid leaking which addresses are "
                    "registered."
                ),
            ),
        },
        examples=[LOGIN_REQUEST_EXAMPLE, LOGIN_RESPONSE_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]
        ip_address = get_client_ip(request)

        try:
            plugin = get_auth_plugin()
            token_pair = plugin.authenticate(email, password)
        except AuthenticationError as exc:
            # Log failed attempt for security monitoring.
            log_event(
                event_type=LOGIN_FAILURE,
                ip_address=ip_address,
                payload={"email": email, "reason": exc.code},
            )
            return Response(
                {"error_code": exc.code},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Load user for audit log and response data.
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        user = user_model.objects.get(email=email)

        log_event(
            event_type=LOGIN_SUCCESS,
            actor=user,
            organization=user.organization,
            entity=user,
            ip_address=ip_address,
        )

        response_data = {
            "access_token": token_pair.access_token,
            "refresh_token": token_pair.refresh_token,
            "user": user,
        }

        return Response(
            TokenPairResponseSerializer(response_data).data,
            status=status.HTTP_200_OK,
        )


class RefreshView(APIView):
    """Obtain a new token pair from a valid refresh token.

    The old refresh token is revoked (rotation) to prevent reuse.
    This endpoint does not require an Authorization header — the
    refresh token is sent in the request body.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        operation_id="auth_refresh",
        tags=["Authentication"],
        summary="Rotate the refresh token (RF-1.3)",
        description=(
            "**Renew the JWT pair without re-entering credentials.**\n\n"
            "**Process**\n\n"
            "1. Decode the refresh token; reject if signature, expiry or "
            "``token_type`` is wrong.\n"
            "2. Reject if the token's ``jti`` is in the Redis blacklist or "
            "the user's token generation has advanced (logout-all).\n"
            "3. Mint a new access + refresh pair; add the *old* refresh "
            "token's ``jti`` to the blacklist so it cannot be reused.\n\n"
            "**Output**\n\n"
            "The new pair. ``user`` is intentionally ``null`` — the frontend "
            "already has user metadata from the original login response.\n\n"
            "**Frontend contract**\n\n"
            "The client MUST replace its stored refresh token immediately "
            "after a successful refresh. Storing both old and new will "
            "invalidate both at the next refresh attempt (rotation enforces "
            "single-use)."
        ),
        request=RefreshRequestSerializer,
        responses={
            200: TokenPairResponseSerializer,
            400: validation_error_response(
                {"refresh_token": ["FIELD_REQUIRED", "BLANK"]},
            ),
            401: error_response(
                [
                    ErrorCode.INVALID_TOKEN,
                    ErrorCode.INVALID_TOKEN_TYPE,
                    ErrorCode.TOKEN_EXPIRED,
                    ErrorCode.TOKEN_REVOKED,
                ],
                status_code=401,
                description=(
                    "Refresh token cannot be honoured. ``TOKEN_REVOKED`` "
                    "is returned both when the token was explicitly "
                    "logged out and when the user's token generation "
                    "has advanced (forced logout-all)."
                ),
            ),
        },
        examples=[REFRESH_REQUEST_EXAMPLE, REFRESH_RESPONSE_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = serializer.validated_data["refresh_token"]

        try:
            plugin = get_auth_plugin()
            token_pair = plugin.refresh(refresh_token)
        except AuthenticationError as exc:
            return Response(
                {"error_code": exc.code},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # We don't include user data in refresh responses — the frontend
        # already has it from the initial login.
        response_data = {
            "access_token": token_pair.access_token,
            "refresh_token": token_pair.refresh_token,
        }

        return Response(
            TokenPairResponseSerializer(response_data).data,
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """Revoke a refresh token to end a session.

    The refresh token is added to the Redis blacklist. The access token
    will expire naturally (short-lived, 15 min by default).

    Requires authentication because we want to log WHO logged out.
    If the access token is already expired but the refresh token is
    still valid, the client should call this endpoint without the
    Authorization header — we accept both authenticated and
    unauthenticated logout to avoid trapping the user.
    """

    # Allow both authenticated and unauthenticated requests.
    # If authenticated, we log the actor. If not, we still revoke.
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_logout",
        tags=["Authentication"],
        summary="Revoke refresh token (RF-1.4)",
        description=(
            "**Invalidate a refresh token (idempotent).**\n\n"
            "Adds the token's ``jti`` to the Redis blacklist with a TTL "
            "matching the token's remaining lifetime. The associated "
            "access token is *not* invalidated by this call — it expires "
            "naturally within minutes.\n\n"
            "**Idempotency**\n\n"
            "Sending an already-revoked, expired or malformed refresh "
            "token still returns ``204``. This intentional behaviour "
            "lets the frontend treat logout as fire-and-forget without "
            "needing to handle ``401``.\n\n"
            "**Audit**\n\n"
            "If the request carries a valid access token, a ``LOGOUT`` "
            "row is appended to the audit log with the actor identified. "
            "Otherwise the row is anonymous (only IP)."
        ),
        request=LogoutRequestSerializer,
        responses={
            204: None,
            400: validation_error_response(
                {"refresh_token": ["FIELD_REQUIRED", "BLANK"]},
            ),
        },
        examples=[LOGOUT_REQUEST_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = serializer.validated_data["refresh_token"]

        try:
            plugin = get_auth_plugin()
            plugin.revoke(refresh_token)
        except AuthenticationError:
            # Even if the token is already invalid, we return 204.
            # This is intentional: logout should be idempotent.
            # The client doesn't need to know if the token was already
            # revoked or expired.
            pass

        # Log the logout event if we know who the user is.
        actor = (
            request.user if hasattr(request, "user") and request.user.is_authenticated else None
        )

        log_event(
            event_type=LOGOUT,
            actor=actor,
            organization=getattr(actor, "organization", None),
            ip_address=get_client_ip(request),
        )

        return Response(status=status.HTTP_204_NO_CONTENT)
