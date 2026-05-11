"""
Organization management API views.

POST /api/v1/organizations/ — Create organization with initial manager.

APIView is used instead of ViewSet because:
- Only creation is exposed via API (superadmin-only).
- Listing, editing, and deactivation are done via DB/admin
  as specified by the user ("eso se hace desde DB manualmente").
- A single POST action doesn't fit the ViewSet CRUD pattern.

References: RF-2.1, RF-16.1
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models.permissions import IsSuperAdmin
from apps.accounts.services.user_service import create_user
from apps.accounts.tasks import send_welcome_email
from apps.audit.services.auditlog import ORG_CREATED, USER_CREATED, get_client_ip, log_event
from apps.organizations.models.organization import Organization
from apps.organizations.serializers.organization import (
    CreateOrganizationSerializer,
    OrganizationResponseSerializer,
)

logger = logging.getLogger(__name__)


class OrganizationCreateView(APIView):
    """Create a new organization with its initial manager.

    Only accessible by system superadmins. Creates both the
    organization and its first manager in a single atomic
    transaction, ensuring we never have an org without a manager.
    """

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        request=CreateOrganizationSerializer,
        responses={201: OrganizationResponseSerializer},
        tags=["Organizations"],
        summary="Create organization with initial manager",
        description=(
            "Creates an organization and its first manager user in one "
            "atomic transaction. Only accessible by superadmins (RF-2.1)."
        ),
    )
    def post(self, request: Request) -> Response:
        serializer = CreateOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        manager_data = data.pop("initial_manager")

        ip_address = get_client_ip(request)

        try:
            with transaction.atomic():
                # Step 1: Create the organization.
                org = Organization.objects.create(
                    name=data["name"],
                    subdomain=data["subdomain"],
                    plan=data.get("plan", "FREE"),
                )

                # Step 2: Create the initial manager.
                manager, raw_password = create_user(
                    organization=org,
                    email=manager_data["email"],
                    first_name=manager_data["first_name"],
                    last_name=manager_data["last_name"],
                    dni=manager_data.get("dni", ""),
                    nia=manager_data.get("nia", ""),
                    is_staff=True,  # First user is always a manager.
                )

        except IntegrityError as exc:
            error_msg = str(exc).lower()
            if "subdomain" in error_msg:
                return Response(
                    {
                        "error_code": "SUBDOMAIN_ALREADY_EXISTS",
                        "errors": {"subdomain": ["SUBDOMAIN_ALREADY_EXISTS"]},
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {"error_code": "ORGANIZATION_CREATION_FAILED"},
                status=status.HTTP_409_CONFLICT,
            )

        # Audit logs — organization creation and manager creation.
        log_event(
            event_type=ORG_CREATED,
            actor=request.user,
            entity=org,
            ip_address=ip_address,
            payload={
                "name": org.name,
                "subdomain": org.subdomain,
                "plan": org.plan,
            },
        )
        log_event(
            event_type=USER_CREATED,
            actor=request.user,
            organization=org,
            entity=manager,
            ip_address=ip_address,
            payload={
                "email": manager.email,
                "is_staff": True,
                "created_with_org": True,
            },
        )

        # Enqueue welcome email for the manager.
        send_welcome_email.delay(manager.email, raw_password, manager.first_name)

        return Response(
            OrganizationResponseSerializer(org).data,
            status=status.HTTP_201_CREATED,
        )
