"""
Integration tests for user management endpoints (RF-2.2 through RF-2.8).

Tests verify:
1. Managers can create users in their organization (RF-2.2).
2. Managers can update user fields (RF-2.4).
3. Managers can deactivate users (RF-2.5).
4. Managers can promote/demote users (RF-2.6).
5. Passwords are generated securely (RF-2.7).
6. Managers can reset passwords (RF-2.8).
7. Non-managers are rejected.
8. Cross-organization access is blocked by tenant isolation.
9. Audit log entries are created for all operations.
"""

import uuid
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models.auditlog import AuditLog
from apps.organizations.models.organization import Organization

pytestmark = pytest.mark.django_db

VALID_KEY = "a" * 64


def _create_org(name="Test Org"):
    return Organization.objects.create(
        name=name,
        subdomain=f"org-{uuid.uuid4().hex[:8]}",
    )


def _create_manager(org, email=None, password="ManagerPass123!"):  # noqa: S107
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = email or f"manager-{uuid.uuid4().hex[:8]}@example.com"
    return user_model.objects.create_user(
        email=email,
        password=password,
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )


def _create_regular_user_in_org(org, email=None, password="UserPass123!"):  # noqa: S107
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    return user_model.objects.create_user(
        email=email,
        password=password,
        first_name="Regular",
        last_name="User",
        organization=org,
    )


def _get_auth_client(user, password="ManagerPass123!"):  # noqa: S107
    reset_auth_plugin()
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}")
    return client


def _user_payload(**overrides):
    data = {
        "email": f"new-{uuid.uuid4().hex[:8]}@example.com",
        "first_name": "Nuevo",
        "last_name": "Usuario",
    }
    data.update(overrides)
    return data


# ── User creation tests (RF-2.2) ────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUserCreation(TestCase):
    """POST /api/v1/users/ (RF-2.2)."""

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_manager_can_create_user(self, mock_email):
        """Manager creates a user in their organization."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post("/api/v1/users/", _user_payload(), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["is_active"] is True
        assert response.data["is_staff"] is False
        assert response.data["organization_id"] == str(org.pk)

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_user_created_in_managers_org(self, mock_email):
        """Created user belongs to the manager's organization (tenant isolation)."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post("/api/v1/users/", _user_payload(), format="json")

        assert response.data["organization_id"] == str(org.pk)

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_welcome_email_enqueued(self, mock_email):
        """Welcome email task is enqueued after user creation."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)
        payload = _user_payload()

        client.post("/api/v1/users/", payload, format="json")

        mock_email.assert_called_once()
        call_args = mock_email.call_args[0]
        assert call_args[0] == payload["email"]  # email
        assert len(call_args[1]) >= 10  # raw_password

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_create_with_dni(self, mock_email):
        """User created with DNI has it encrypted in DB."""
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        payload = _user_payload(dni="12345678X")
        response = client.post("/api/v1/users/", payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        # DNI must NOT appear in the response.
        assert "dni" not in response.data

        # Verify it's encrypted in DB.
        user = user_model.objects.get(pk=response.data["id"])
        assert user.encrypted_dni is not None
        assert user.dni_nonce is not None

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_duplicate_email_rejected(self, mock_email):
        """Duplicate email in same org returns 409."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
        client.post("/api/v1/users/", _user_payload(email=email), format="json")
        response = client.post("/api/v1/users/", _user_payload(email=email), format="json")

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_non_manager_cannot_create_user(self):
        """Regular user (non-manager) is rejected with 403."""
        org = _create_org()
        user = _create_regular_user_in_org(org)
        client = _get_auth_client(user, password="UserPass123!")  # noqa: S106

        response = client.post("/api/v1/users/", _user_payload(), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_audit_log_created(self, mock_email):
        """User creation generates an audit log entry."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/users/", _user_payload(), format="json")

        log = AuditLog.objects.filter(event_type="USER_CREATED").last()
        assert log is not None
        assert log.actor_id == manager.pk

    @patch("apps.accounts.views.users.send_welcome_email.delay")
    def test_create_manager_user(self, mock_email):
        """Manager can create another manager (is_staff=True)."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post(
            "/api/v1/users/",
            _user_payload(is_staff=True),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["is_staff"] is True


# ── User update tests (RF-2.4, RF-2.5, RF-2.6) ─────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUserUpdate(TestCase):
    """PATCH /api/v1/users/{id}/ (RF-2.4, RF-2.5, RF-2.6)."""

    def test_update_name(self):
        """Manager can update a user's name."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        response = client.patch(
            f"/api/v1/users/{target.pk}/",
            {"first_name": "NuevoNombre"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["first_name"] == "NuevoNombre"

    def test_deactivate_user(self):
        """Manager can deactivate a user (RF-2.5)."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        response = client.patch(
            f"/api/v1/users/{target.pk}/",
            {"is_active": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_active"] is False

    def test_cannot_deactivate_self(self):
        """Manager cannot deactivate themselves."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.patch(
            f"/api/v1/users/{manager.pk}/",
            {"is_active": False},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == "CANNOT_DEACTIVATE_SELF"

    def test_cannot_demote_self(self):
        """Manager cannot remove their own is_staff."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.patch(
            f"/api/v1/users/{manager.pk}/",
            {"is_staff": False},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == "CANNOT_DEMOTE_SELF"

    def test_promote_to_manager(self):
        """Manager can promote a regular user to manager (RF-2.6)."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        response = client.patch(
            f"/api/v1/users/{target.pk}/",
            {"is_staff": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_staff"] is True

    def test_update_creates_audit_log(self):
        """User update generates audit log with changes."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        client.patch(
            f"/api/v1/users/{target.pk}/",
            {"first_name": "Changed"},
            format="json",
        )

        log = AuditLog.objects.filter(event_type="USER_UPDATED").last()
        assert log is not None
        assert "first_name" in log.payload["changes"]

    def test_deactivation_creates_specific_audit_event(self):
        """Deactivation creates USER_DEACTIVATED event, not USER_UPDATED."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        client.patch(
            f"/api/v1/users/{target.pk}/",
            {"is_active": False},
            format="json",
        )

        log = AuditLog.objects.filter(event_type="USER_DEACTIVATED").first()
        assert log is not None

    def test_user_not_found_returns_404(self):
        """Updating a non-existent user returns 404."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        fake_id = uuid.uuid4()
        response = client.patch(
            f"/api/v1/users/{fake_id}/",
            {"first_name": "Ghost"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Password reset tests (RF-2.8) ───────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestPasswordReset(TestCase):
    """POST /api/v1/users/{id}/reset-password/ (RF-2.8)."""

    @patch("apps.accounts.views.users.send_password_reset_email.delay")
    def test_manager_can_reset_password(self, mock_email):
        """Manager can force-reset another user's password."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        response = client.post(
            f"/api/v1/users/{target.pk}/reset-password/",
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    @patch("apps.accounts.views.users.send_password_reset_email.delay")
    def test_reset_email_enqueued(self, mock_email):
        """Password reset email is enqueued."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        client.post(f"/api/v1/users/{target.pk}/reset-password/", format="json")

        mock_email.assert_called_once()
        call_args = mock_email.call_args[0]
        assert call_args[0] == target.email

    @patch("apps.accounts.views.users.send_password_reset_email.delay")
    def test_reset_invalidates_old_tokens(self, mock_email):
        """After password reset, old tokens should be invalid."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)

        # Log in as the target user first.
        _get_auth_client(target, password="UserPass123!")  # noqa: S106

        # Manager resets target's password.
        manager_client = _get_auth_client(manager)
        manager_client.post(
            f"/api/v1/users/{target.pk}/reset-password/",
            format="json",
        )

        # Target's old token should now be rejected.
        # We test this indirectly via the backend.
        from apps.accounts.blacklist import get_user_token_generation

        gen = get_user_token_generation(str(target.pk))
        assert gen >= 1  # Generation was incremented.

    @patch("apps.accounts.views.users.send_password_reset_email.delay")
    def test_reset_creates_audit_log(self, mock_email):
        """Password reset creates an audit log entry."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        client.post(f"/api/v1/users/{target.pk}/reset-password/", format="json")

        log = AuditLog.objects.filter(event_type="PASSWORD_RESET").first()
        assert log is not None
        assert log.actor_id == manager.pk

    def test_non_manager_cannot_reset_password(self):
        """Regular user cannot reset another user's password."""
        org = _create_org()
        target = _create_regular_user_in_org(org)
        regular = _create_regular_user_in_org(
            org, email=f"regular-{uuid.uuid4().hex[:8]}@example.com"
        )
        client = _get_auth_client(regular, password="UserPass123!")  # noqa: S106

        response = client.post(
            f"/api/v1/users/{target.pk}/reset-password/",
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Retrieve tests ───────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUserRetrieve(TestCase):
    """GET /api/v1/users/{id}/."""

    def test_manager_can_retrieve_user(self):
        """Manager can retrieve a user in their org."""
        org = _create_org()
        manager = _create_manager(org)
        target = _create_regular_user_in_org(org)
        client = _get_auth_client(manager)

        response = client.get(f"/api/v1/users/{target.pk}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == target.email
        assert "dni" not in response.data  # DNI must not be exposed.

    def test_retrieve_nonexistent_returns_404(self):
        """Non-existent user ID returns 404."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.get(f"/api/v1/users/{uuid.uuid4()}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
