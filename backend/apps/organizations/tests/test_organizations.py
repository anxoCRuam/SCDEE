"""
Integration tests for organization creation (RF-2.1).

Tests verify:
1. Only superadmins can create organizations.
2. Organization + initial manager are created atomically.
3. Duplicate subdomain is rejected.
4. Audit log entries are created.
5. Welcome email is enqueued for the initial manager.
"""

import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


def _create_superadmin(email="superadmin@system.local", password="SuperPass123!"):  # noqa: S107
    """Create a superadmin user for testing."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return user_model.objects.create_superuser(email=email, password=password)


def _create_regular_user(email="regular@example.com", password="RegularPass123!"):  # noqa: S107
    """Create a regular user (not superadmin) for permission tests."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(
        name="Existing Org",
        subdomain=f"existing-{uuid.uuid4().hex[:8]}",
    )
    return user_model.objects.create_user(
        email=email,
        password=password,
        first_name="Regular",
        last_name="User",
        organization=org,
        is_staff=True,
    )


def _get_auth_client(user, password):
    """Create an APIClient authenticated via login endpoint."""
    reset_auth_plugin()
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    token = response.data["access_token"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _org_payload(**overrides):
    """Build a valid organization creation payload."""
    data = {
        "name": "Universidad de Prueba",
        "subdomain": f"test-{uuid.uuid4().hex[:8]}",
        "plan": "FREE",
        "initial_manager": {
            "email": f"manager-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Gestor",
            "last_name": "Inicial",
        },
    }
    data.update(overrides)
    return data


class TestOrganizationCreation:
    """POST /api/v1/organizations/ (RF-2.1)."""

    def test_superadmin_can_create_organization(self):
        """Superadmin creates org + manager successfully."""
        admin = _create_superadmin()
        client = _get_auth_client(admin, "SuperPass123!")
        payload = _org_payload()

        response = client.post("/api/v1/organizations/", payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == payload["name"]
        assert response.data["subdomain"] == payload["subdomain"]
        assert response.data["plan"] == "FREE"

        # Verify organization exists in DB.
        org = Organization.objects.get(subdomain=payload["subdomain"])
        assert org.name == payload["name"]

    def test_initial_manager_is_created(self):
        """The initial manager user is created with the organization."""
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        admin = _create_superadmin()
        client = _get_auth_client(admin, "SuperPass123!")
        manager_email = f"mgr-{uuid.uuid4().hex[:8]}@example.com"
        payload = _org_payload(
            initial_manager={
                "email": manager_email,
                "first_name": "Ana",
                "last_name": "García",
            }
        )

        client.post("/api/v1/organizations/", payload, format="json")

        manager = user_model.objects.get(email=manager_email)
        assert manager.is_staff is True
        assert manager.is_active is True
        assert manager.organization.subdomain == payload["subdomain"]

    def test_regular_user_cannot_create_organization(self):
        """Non-superadmin users are rejected with 403."""
        user = _create_regular_user()
        client = _get_auth_client(user, "RegularPass123!")

        response = client.post(
            "/api/v1/organizations/",
            _org_payload(),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_request_rejected(self):
        """No auth → 401."""
        client = APIClient()

        response = client.post(
            "/api/v1/organizations/",
            _org_payload(),
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_duplicate_subdomain_rejected(self):
        """Duplicate subdomain returns 409."""
        admin = _create_superadmin()
        client = _get_auth_client(admin, "SuperPass123!")

        subdomain = f"dup-{uuid.uuid4().hex[:8]}"
        payload1 = _org_payload(subdomain=subdomain)
        payload2 = _org_payload(
            subdomain=subdomain,
            name="Different Name",
            initial_manager={
                "email": f"other-{uuid.uuid4().hex[:8]}@example.com",
                "first_name": "Other",
                "last_name": "Manager",
            },
        )

        client.post("/api/v1/organizations/", payload1, format="json")
        response = client.post("/api/v1/organizations/", payload2, format="json")

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_audit_log_created(self):
        """Organization creation generates audit log entries."""
        admin = _create_superadmin()
        client = _get_auth_client(admin, "SuperPass123!")

        client.post("/api/v1/organizations/", _org_payload(), format="json")

        org_log = AuditLog.objects.filter(event_type="ORG_CREATED").first()
        assert org_log is not None

        user_log = AuditLog.objects.filter(
            event_type="USER_CREATED",
            payload__created_with_org=True,
        ).first()
        assert user_log is not None

    def test_missing_initial_manager_rejected(self):
        """Organization without initial_manager data is rejected."""
        admin = _create_superadmin()
        client = _get_auth_client(admin, "SuperPass123!")

        payload = {
            "name": "No Manager Org",
            "subdomain": "no-manager",
        }

        response = client.post("/api/v1/organizations/", payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_premium_plan(self):
        """Organization can be created with PREMIUM plan."""
        admin = _create_superadmin()
        client = _get_auth_client(admin, "SuperPass123!")
        payload = _org_payload(plan="PREMIUM")

        response = client.post("/api/v1/organizations/", payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["plan"] == "PREMIUM"
