"""
Integration tests for profile and user listing (RF-2.9 through RF-2.12).

Tests verify:
1. Profile GET returns own data with decrypted DNI (RF-2.10).
2. Profile PATCH allows email and notification changes (RF-2.9, RF-2.11).
3. Profile PATCH rejects duplicate email (RF-2.9).
4. User listing returns paginated results (RF-2.12).
5. User listing supports search and filters (RF-2.12).
6. Tenant isolation: listing only returns users from same org.
"""

import uuid

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.organizations.models.organization import Organization

pytestmark = pytest.mark.django_db

VALID_KEY = "a" * 64


def _create_org(name="Test Org"):
    return Organization.objects.create(
        name=name,
        subdomain=f"org-{uuid.uuid4().hex[:8]}",
    )


def _create_user_in_org(org, email=None, password="Pass123!", **kwargs):  # noqa: S107
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    return user_model.objects.create_user(
        email=email,
        password=password,
        first_name=kwargs.get("first_name", "Test"),
        last_name=kwargs.get("last_name", "User"),
        organization=org,
        is_staff=kwargs.get("is_staff", False),
        is_active=kwargs.get("is_active", True),
        nia=kwargs.get("nia", ""),
    )


def _get_auth_client(user, password="Pass123!"):  # noqa: S107
    reset_auth_plugin()
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}")
    return client


# ── Profile GET tests (RF-2.10) ─────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestProfileGet(TestCase):
    """GET /api/v1/profile/ (RF-2.10)."""

    def test_returns_own_profile(self):
        """Authenticated user sees their own data."""
        org = _create_org()
        user = _create_user_in_org(org, first_name="María", last_name="López")
        client = _get_auth_client(user)

        response = client.get("/api/v1/profile/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == user.email
        assert response.data["first_name"] == "María"
        assert response.data["last_name"] == "López"

    def test_includes_decrypted_dni(self):
        """Profile includes the user's own decrypted DNI."""
        from apps.accounts.services.encryption import encrypt_dni

        org = _create_org()
        encrypted_data, nonce = encrypt_dni("12345678X")

        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        user = user_model.objects.create_user(
            email="dni-user@example.com",
            password="Pass123!",  # noqa: S106
            first_name="DNI",
            last_name="User",
            organization=org,
            encrypted_dni=encrypted_data,
            dni_nonce=nonce,
        )
        client = _get_auth_client(user)

        response = client.get("/api/v1/profile/")

        assert response.data["dni"] == "12345678X"

    def test_no_dni_returns_empty_string(self):
        """Profile without DNI returns empty string."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        response = client.get("/api/v1/profile/")

        assert response.data["dni"] == ""

    def test_includes_notification_preference(self):
        """Profile includes email_notifications_enabled."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        response = client.get("/api/v1/profile/")

        assert "email_notifications_enabled" in response.data
        assert response.data["email_notifications_enabled"] is True

    def test_unauthenticated_rejected(self):
        """Unauthenticated request returns 401."""
        client = APIClient()

        response = client.get("/api/v1/profile/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Profile PATCH tests (RF-2.9, RF-2.11) ───────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestProfilePatch(TestCase):
    """PATCH /api/v1/profile/ (RF-2.9, RF-2.11)."""

    def test_update_email(self):
        """User can change their own email (RF-2.9)."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        response = client.patch(
            "/api/v1/profile/",
            {"email": "newemail@example.com"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == "newemail@example.com"

    def test_update_notification_preference(self):
        """User can toggle notification preference (RF-2.11)."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        response = client.patch(
            "/api/v1/profile/",
            {"email_notifications_enabled": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["email_notifications_enabled"] is False

    def test_duplicate_email_rejected(self):
        """Changing to an existing email returns 409."""
        org = _create_org()
        _create_user_in_org(org, email="taken@example.com")
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        response = client.patch(
            "/api/v1/profile/",
            {"email": "taken@example.com"},
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_no_change_returns_200(self):
        """Sending the same email returns 200 without audit entry."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        response = client.patch(
            "/api/v1/profile/",
            {"email": user.email},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_profile_update_creates_audit_log(self):
        """Profile changes are recorded in audit log."""
        from apps.audit.models.auditlog import AuditLog

        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user)

        client.patch(
            "/api/v1/profile/",
            {"email_notifications_enabled": False},
            format="json",
        )

        log = AuditLog.objects.filter(
            event_type="USER_UPDATED",
            payload__self_update=True,
        ).first()
        assert log is not None


# ── User listing tests (RF-2.12) ────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUserListing(TestCase):
    """GET /api/v1/users/ (RF-2.12)."""

    def test_returns_paginated_results(self):
        """List endpoint returns paginated response."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        for i in range(5):
            _create_user_in_org(org, first_name=f"User{i}")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/")

        assert response.status_code == status.HTTP_200_OK
        assert "count" in response.data
        assert "results" in response.data
        assert "page" in response.data
        assert "total_pages" in response.data
        # 5 created + 1 manager = 6 total
        assert response.data["count"] == 6

    def test_search_by_name(self):
        """Search filter matches partial first_name or last_name."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True, first_name="Admin")
        _create_user_in_org(org, first_name="María", last_name="García")
        _create_user_in_org(org, first_name="Pedro", last_name="Martínez")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?search=garc")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["first_name"] == "María"

    def test_filter_by_active_status(self):
        """is_active filter returns only matching users."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        _create_user_in_org(org, is_active=True)
        _create_user_in_org(org, is_active=False)
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?is_active=false")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["is_active"] is False

    def test_filter_by_staff(self):
        """is_staff filter returns only managers or non-managers."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        _create_user_in_org(org, is_staff=False)
        _create_user_in_org(org, is_staff=False)
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?is_staff=true")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # Only the manager

    def test_search_by_email(self):
        """Search matches partial email."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        _create_user_in_org(org, email="unique-needle@example.com")
        _create_user_in_org(org, email="other@example.com")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?search=needle")

        assert response.data["count"] == 1

    def test_filter_by_nia(self):
        """NIA filter does exact (case-insensitive) match."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        _create_user_in_org(org, nia="ABC123")
        _create_user_in_org(org, nia="XYZ789")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?nia=abc123")

        assert response.data["count"] == 1

    def test_custom_page_size(self):
        """Client can request a custom page size."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        for _ in range(10):
            _create_user_in_org(org)
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?page_size=3")

        assert response.data["page_size"] == 3
        assert len(response.data["results"]) == 3
        assert response.data["total_pages"] == 4  # 11 users / 3 per page

    def test_tenant_isolation(self):
        """Manager only sees users from their own organization."""
        org1 = _create_org("Org 1")
        org2 = _create_org("Org 2")

        manager1 = _create_user_in_org(org1, is_staff=True)
        _create_user_in_org(org1)  # User in org1
        _create_user_in_org(org2)  # User in org2 (should be invisible)

        client = _get_auth_client(manager1)

        response = client.get("/api/v1/users/")

        # Should see 2 users (manager + 1 user), not 3.
        assert response.data["count"] == 2

    def test_non_manager_cannot_list(self):
        """Non-manager user gets 403."""
        org = _create_org()
        user = _create_user_in_org(org, is_staff=False)
        client = _get_auth_client(user)

        response = client.get("/api/v1/users/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_combined_filters(self):
        """Multiple filters can be combined."""
        org = _create_org()
        manager = _create_user_in_org(org, is_staff=True)
        _create_user_in_org(org, first_name="Ana", is_active=True)
        _create_user_in_org(org, first_name="Ana", is_active=False)
        _create_user_in_org(org, first_name="Pedro", is_active=True)
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/?search=ana&is_active=true")

        assert response.data["count"] == 1
        assert response.data["results"][0]["first_name"] == "Ana"
        assert response.data["results"][0]["is_active"] is True
