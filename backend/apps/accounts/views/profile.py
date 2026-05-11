"""
Own profile API view.

GET  /api/v1/profile/ — View own profile (RF-2.10)
PATCH /api/v1/profile/ — Update email or notification preferences (RF-2.9, RF-2.11)

Why APIView instead of ViewSet?
    The profile is a singleton — it always refers to request.user.
    There's no collection to list, no ID in the URL, no create/delete.
    APIView with get() and patch() maps exactly to the two operations.

Why not reuse the UserViewSet?
    Different permissions (any authenticated user, not just managers),
    different output (includes DNI), different input (only email and
    notification prefs), different URL pattern (no /{id}/).

References: RF-2.9, RF-2.10, RF-2.11, RF-16.1
"""

from __future__ import annotations

import logging

from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers.profile import (
    ProfileResponseSerializer,
    UpdateProfileSerializer,
)
from apps.accounts.services.user_service import get_decrypted_dni
from apps.audit.services.auditlog import USER_UPDATED, get_client_ip, log_event
from apps.core.openapi.errors import ErrorCode, error_response

logger = logging.getLogger(__name__)


class ProfileView(APIView):
    """Manage the authenticated user's own profile.

    Any authenticated user can access this endpoint — no manager
    or superadmin role required.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="profile_retrieve",
        responses={200: ProfileResponseSerializer},
        tags=["Users"],
        summary="View own profile (RF-2.10)",
        description=(
            "**Return the authenticated user's full profile.**\n\n"
            "Includes the *decrypted* DNI — by RF-2.10, the DNI is shown "
            "in plaintext only when the caller views their own profile or "
            "is an organisation manager. The DNI is stored encrypted at "
            "rest with AES-256-GCM (RF-16.3) and decrypted only on this "
            "code path.\n\n"
            "Other endpoints that serialise users (lists, search) do *not* "
            "include the DNI."
        ),
    )
    def get(self, request: Request) -> Response:
        """Return the authenticated user's profile data (RF-2.10)."""
        user = request.user
        profile_data = self._build_profile_data(user)
        return Response(ProfileResponseSerializer(profile_data).data)

    @extend_schema(
        operation_id="profile_partial_update",
        request=UpdateProfileSerializer,
        responses={
            200: ProfileResponseSerializer,
            409: error_response(
                [ErrorCode.EMAIL_ALREADY_EXISTS],
                status_code=409,
                description=(
                    "The new email is already in use by another user in "
                    "the same organisation. Note: this endpoint also "
                    "returns the validation-error shape "
                    '(``errors.email = ["EMAIL_ALREADY_EXISTS"]``) so '
                    "the frontend can attach the message to the email field."
                ),
            ),
        },
        tags=["Users"],
        summary="Update own email or notification preferences (RF-2.9, RF-2.11)",
        description=(
            "**Patch a subset of the caller's own profile.**\n\n"
            "Two fields are accepted:\n"
            "- ``email`` (RF-2.9) — new contact address. Must be unique "
            "within the organisation.\n"
            "- ``email_notifications_enabled`` (RF-2.11) — when ``false``, "
            "the system stops sending email mirrors of in-app notifications.\n\n"
            "**Unchanged fields are silently no-ops.** A request with the same "
            "email and same notification preference returns ``200`` without "
            "writing to the database (and without an audit row).\n\n"
            "**Audit**\n\n"
            "Effective changes are logged with ``USER_UPDATED`` and the "
            "before/after values, marked ``self_update=true`` (RF-16.1)."
        ),
    )
    def patch(self, request: Request) -> Response:
        """Update the authenticated user's email or preferences."""
        serializer = UpdateProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        changes: dict = {}

        # Update email if provided and different.
        if "email" in serializer.validated_data:
            new_email = serializer.validated_data["email"]
            if new_email != user.email:
                old_email = user.email
                user.email = new_email
                changes["email"] = {"old": old_email, "new": new_email}

        # Update notification preference if provided and different.
        if "email_notifications_enabled" in serializer.validated_data:
            new_pref = serializer.validated_data["email_notifications_enabled"]
            if new_pref != user.email_notifications_enabled:
                changes["email_notifications_enabled"] = {
                    "old": user.email_notifications_enabled,
                    "new": new_pref,
                }
                user.email_notifications_enabled = new_pref

        if not changes:
            profile_data = self._build_profile_data(user)
            return Response(ProfileResponseSerializer(profile_data).data)

        try:
            user.save()
        except IntegrityError:
            return Response(
                {
                    "error_code": "EMAIL_ALREADY_EXISTS",
                    "errors": {"email": ["EMAIL_ALREADY_EXISTS"]},
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Audit log for profile changes.
        log_event(
            event_type=USER_UPDATED,
            actor=user,
            organization=user.organization,
            entity=user,
            ip_address=get_client_ip(request),
            payload={"changes": changes, "self_update": True},
        )

        profile_data = self._build_profile_data(user)
        return Response(ProfileResponseSerializer(profile_data).data)

    def _build_profile_data(self, user) -> dict:
        """Build the profile response dict with decrypted DNI.

        Extracts all fields needed by ProfileResponseSerializer
        and adds the decrypted DNI (only shown to the user themselves).
        """
        return {
            "id": user.pk,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "nia": user.nia,
            "dni": get_decrypted_dni(user),
            "organization_id": user.organization_id,
            "email_notifications_enabled": user.email_notifications_enabled,
        }
