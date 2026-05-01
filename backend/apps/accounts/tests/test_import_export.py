"""
Integration tests for user import and export (RF-2.3, RF-2.13).

Tests verify:
1. CSV import creates/reactivates/updates users correctly.
2. JSON import works identically.
3. Per-row errors are reported without aborting.
4. Welcome emails are enqueued for new/reactivated users.
5. Export returns CSV/JSON with decrypted DNIs.
6. Export respects filters.
7. Audit log entries are created.
8. Permission checks (managers only).
"""

import io
import json
import uuid
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

VALID_KEY = "a" * 64


def _create_org(name="Test Org"):
    return Organization.objects.create(
        name=name,
        subdomain=f"org-{uuid.uuid4().hex[:8]}",
    )


def _create_manager(org, password="ManagerPass123!"):  # noqa: S107
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"manager-{uuid.uuid4().hex[:8]}@example.com",
        password=password,
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )


def _create_user_in_org(org, email=None, **kwargs):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    return user_model.objects.create_user(
        email=email,
        password="Pass123!",  # noqa: S106
        first_name=kwargs.get("first_name", "Test"),
        last_name=kwargs.get("last_name", "User"),
        organization=org,
        is_active=kwargs.get("is_active", True),
        nia=kwargs.get("nia", ""),
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


def _build_csv(rows: list[dict]) -> str:
    """Build a CSV string from a list of dicts."""
    output = io.StringIO()
    output.write("first_name,last_name,email,dni,nia\n")
    for row in rows:
        output.write(
            f"{row.get('first_name', '')},{row.get('last_name', '')},"
            f"{row.get('email', '')},{row.get('dni', '')},{row.get('nia', '')}\n"
        )
    return output.getvalue()


def _upload_csv(client, csv_content: str):
    """Upload a CSV file to the import endpoint."""
    csv_file = io.BytesIO(csv_content.encode("utf-8"))
    csv_file.name = "users.csv"
    return client.post(
        "/api/v1/users/import/",
        {"file": csv_file},
        format="multipart",
    )


def _upload_json(client, data: list[dict]):
    """Upload a JSON file to the import endpoint."""
    json_content = json.dumps(data).encode("utf-8")
    json_file = io.BytesIO(json_content)
    json_file.name = "users.json"
    return client.post(
        "/api/v1/users/import/",
        {"file": json_file},
        format="multipart",
    )


# ── CSV Import tests (RF-2.3) ───────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCSVImport(TestCase):
    """POST /api/v1/users/import/ with CSV file."""

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_creates_new_users(self, mock_email):
        """CSV import creates new users."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        csv_content = _build_csv(
            [
                {
                    "first_name": "Ana",
                    "last_name": "García",
                    "email": "ana@example.com",
                    "dni": "12345678X",
                    "nia": "NIA001",
                },
                {
                    "first_name": "Pedro",
                    "last_name": "López",
                    "email": "pedro@example.com",
                    "dni": "",
                    "nia": "NIA002",
                },
            ]
        )

        response = _upload_csv(client, csv_content)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["created"] == 2
        assert response.data["total_errors"] == 0

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_reactivates_inactive_user(self, mock_email):
        """Import reactivates inactive user and updates data."""
        org = _create_org()
        manager = _create_manager(org)
        inactive_user = _create_user_in_org(
            org,
            email="inactive@example.com",
            is_active=False,
            first_name="Old",
            last_name="Name",
        )
        client = _get_auth_client(manager)

        csv_content = _build_csv(
            [
                {
                    "first_name": "New",
                    "last_name": "Name",
                    "email": "inactive@example.com",
                    "dni": "",
                    "nia": "",
                },
            ]
        )

        response = _upload_csv(client, csv_content)

        assert response.data["reactivated"] == 1
        inactive_user.refresh_from_db()
        assert inactive_user.is_active is True
        assert inactive_user.first_name == "New"

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_updates_existing_active_user(self, mock_email):
        """Import updates data for existing active users."""
        org = _create_org()
        manager = _create_manager(org)
        existing = _create_user_in_org(
            org,
            email="existing@example.com",
            first_name="Old",
            last_name="Name",
        )
        client = _get_auth_client(manager)

        csv_content = _build_csv(
            [
                {
                    "first_name": "Updated",
                    "last_name": "Name",
                    "email": "existing@example.com",
                    "dni": "",
                    "nia": "",
                },
            ]
        )

        response = _upload_csv(client, csv_content)

        assert response.data["updated"] == 1
        existing.refresh_from_db()
        assert existing.first_name == "Updated"

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_reports_per_row_errors(self, mock_email):
        """Rows with validation errors are reported but don't abort."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        csv_content = _build_csv(
            [
                {
                    "first_name": "Valid",
                    "last_name": "User",
                    "email": "valid@example.com",
                    "dni": "",
                    "nia": "",
                },
                {
                    "first_name": "",
                    "last_name": "NoName",
                    "email": "noname@example.com",
                    "dni": "",
                    "nia": "",
                },  # Missing first_name
            ]
        )

        response = _upload_csv(client, csv_content)

        assert response.data["created"] == 1
        assert response.data["total_errors"] == 1
        assert response.data["errors"][0]["row"] == 2

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_welcome_emails_enqueued(self, mock_email):
        """Welcome emails are enqueued for created and reactivated users."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        csv_content = _build_csv(
            [
                {
                    "first_name": "New",
                    "last_name": "User",
                    "email": "new@example.com",
                    "dni": "",
                    "nia": "",
                },
            ]
        )

        _upload_csv(client, csv_content)

        assert mock_email.call_count == 1

    def test_non_manager_rejected(self):
        """Non-manager gets 403."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user, password="Pass123!")  # noqa: S106

        response = _upload_csv(client, "first_name,last_name,email,dni,nia\n")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_audit_log_created(self, mock_email):
        """Import creates an audit log entry with summary."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        csv_content = _build_csv(
            [
                {
                    "first_name": "A",
                    "last_name": "B",
                    "email": "ab@example.com",
                    "dni": "",
                    "nia": "",
                },
            ]
        )

        _upload_csv(client, csv_content)

        log = AuditLog.objects.filter(event_type="DATA_IMPORTED").first()
        assert log is not None
        assert log.payload["created"] == 1
        assert log.payload["format"] == "csv"

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_missing_columns_rejected(self, mock_email):
        """CSV missing required columns is rejected."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        bad_csv = "name,correo\nJuan,juan@x.com\n"
        response = _upload_csv(client, bad_csv)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == "INVALID_FILE_FORMAT"


# ── JSON Import tests (RF-2.3) ──────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestJSONImport(TestCase):
    """POST /api/v1/users/import/ with JSON file."""

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_json_import_creates_users(self, mock_email):
        """JSON import works identically to CSV."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        data = [
            {
                "first_name": "Ana",
                "last_name": "García",
                "email": "ana-j@example.com",
                "dni": "12345678X",
                "nia": "NIA001",
            },
        ]

        response = _upload_json(client, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["created"] == 1

    @patch("apps.accounts.views.import_export.send_welcome_email.delay")
    def test_invalid_json_rejected(self, mock_email):
        """Malformed JSON is rejected."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        bad_json = io.BytesIO(b"not valid json {{{")
        bad_json.name = "users.json"

        response = client.post(
            "/api/v1/users/import/",
            {"file": bad_json},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ── Export tests (RF-2.13) ───────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUserExport(TestCase):
    """GET /api/v1/users/export/ (RF-2.13)."""

    def test_export_csv(self):
        """Export returns CSV with user data."""
        org = _create_org()
        manager = _create_manager(org)
        _create_user_in_org(org, first_name="Ana", last_name="García")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/export/")

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"] == 'attachment; filename="users.csv"'

        content = response.content.decode("utf-8")
        assert "first_name" in content  # Header
        assert "Ana" in content
        assert "García" in content

    def test_export_json(self):
        """Export with format=json returns JSON."""
        org = _create_org()
        manager = _create_manager(org)
        _create_user_in_org(org, first_name="Pedro")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/export/?format=json")

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/json"

        data = json.loads(response.content)
        assert isinstance(data, list)
        assert any(u["first_name"] == "Pedro" for u in data)

    def test_export_includes_decrypted_dni(self):
        """Exported data includes decrypted DNI."""
        from django.contrib.auth import get_user_model

        from apps.accounts.services.encryption import encrypt_dni

        user_model = get_user_model()

        org = _create_org()
        manager = _create_manager(org)

        encrypted_data, nonce = encrypt_dni("99887766Z")
        user_model.objects.create_user(
            email="dni-export@example.com",
            password="Pass123!",  # noqa: S106
            first_name="DNI",
            last_name="User",
            organization=org,
            encrypted_dni=encrypted_data,
            dni_nonce=nonce,
        )

        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/export/?format=json")

        data = json.loads(response.content)
        dni_user = next(u for u in data if u["email"] == "dni-export@example.com")
        assert dni_user["dni"] == "99887766Z"

    def test_export_with_filter(self):
        """Export respects query filters."""
        org = _create_org()
        manager = _create_manager(org)
        _create_user_in_org(org, first_name="Active", is_active=True)
        _create_user_in_org(org, first_name="Inactive", is_active=False)
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/export/?format=json&is_active=true")

        data = json.loads(response.content)
        # All exported users should be active (manager + "Active" user).
        assert all(u["is_active"] for u in data)

    def test_export_creates_audit_log(self):
        """Export creates an audit log entry."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.get("/api/v1/users/export/")

        log = AuditLog.objects.filter(event_type="DATA_EXPORTED").first()
        assert log is not None
        assert log.payload["entity_type"] == "User"

    def test_non_manager_cannot_export(self):
        """Non-manager gets 403."""
        org = _create_org()
        user = _create_user_in_org(org)
        client = _get_auth_client(user, password="Pass123!")  # noqa: S106

        response = client.get("/api/v1/users/export/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_tenant_isolation_on_export(self):
        """Export only includes users from the manager's org."""
        org1 = _create_org("Org 1")
        org2 = _create_org("Org 2")
        manager = _create_manager(org1)
        _create_user_in_org(org1, first_name="InOrg1")
        _create_user_in_org(org2, first_name="InOrg2")
        client = _get_auth_client(manager)

        response = client.get("/api/v1/users/export/?format=json")

        data = json.loads(response.content)
        names = [u["first_name"] for u in data]
        assert "InOrg1" in names
        assert "InOrg2" not in names
