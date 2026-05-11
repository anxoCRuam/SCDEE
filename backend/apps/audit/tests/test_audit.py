# backend/apps/audit/tests/test_audit_views.py
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models.auditlog import AuditLog
from apps.organizations.models.organization import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager():
    org = Organization.objects.create(name="TestOrg", subdomain=f"org-{uuid.uuid4().hex[:8]}")
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"mgr-{uuid.uuid4().hex[:8]}@test.com",
        password="mgrpass123",  # noqa: S106
        first_name="Mgr",
        last_name="Tester",
        organization=org,
        is_staff=True,
    )


@pytest.fixture
def regular_user(manager):
    org = manager.organization
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"usr-{uuid.uuid4().hex[:8]}@test.com",
        password="usrpass123",  # noqa: S106
        first_name="Reg",
        last_name="User",
        organization=org,
        is_staff=False,
    )


@pytest.fixture
def audit_logs(manager):
    org = manager.organization
    now = timezone.now()
    # Logs de la organización
    AuditLog.objects.create(
        organization=org,
        event_type="USER_CREATED",
        actor=manager,
        ip_address="10.0.0.1",
        timestamp=now - timedelta(days=2),
        entity_type="User",
        entity_id=str(manager.pk),
        payload={"email": "test@test.com"},
    )
    # Log de otra organización (no debe verse)
    other_org = Organization.objects.create(
        name="Other", subdomain=f"other-{uuid.uuid4().hex[:8]}"
    )
    AuditLog.objects.create(
        organization=other_org,
        event_type="ORG_CREATED",
        actor=None,
        ip_address="10.0.0.3",
        timestamp=now,
    )


class TestAuditLogVisibility:
    def test_manager_can_access_own_org_logs(self, manager, audit_logs):
        client = APIClient()
        login_resp = client.post(
            "/api/v1/auth/login/",
            {"email": manager.email, "password": "mgrpass123"},
            format="json",
        )
        token = login_resp.data["access_token"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = client.get("/api/v1/audit-logs/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Debe ver solo los 2 de su organización
        assert data["count"] == 2

    def test_regular_user_is_denied(self, regular_user, audit_logs):
        client = APIClient()
        login_resp = client.post(
            "/api/v1/auth/login/",
            {"email": regular_user.email, "password": "usrpass123"},
            format="json",
        )
        token = login_resp.data["access_token"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.get("/api/v1/audit-logs/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_returns_401(self):
        client = APIClient()
        response = client.get("/api/v1/audit-logs/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestAuditFilters:
    def test_filter_by_event_type(self, manager, audit_logs):
        client = self._login_manager(manager)
        response = client.get("/api/v1/audit-logs/", {"event_type": "USER_CREATED"})
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_filter_by_actor(self, manager, audit_logs):
        client = self._login_manager(manager)
        response = client.get("/api/v1/audit-logs/", {"actor_id": str(manager.pk)})
        assert response.status_code == 200
        assert response.json()["count"] == 2

    def test_filter_by_ip(self, manager, audit_logs):
        client = self._login_manager(manager)
        response = client.get("/api/v1/audit-logs/", {"ip_address": "10.0.0.1"})
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_filter_by_timestamp_range(self, manager, audit_logs):
        client = self._login_manager(manager)
        now = timezone.now()
        response = client.get(
            "/api/v1/audit-logs/",
            {
                "timestamp_from": (now - timedelta(days=1, hours=1)).isoformat(),
                "timestamp_to": now.isoformat(),
            },
        )
        assert response.status_code == 200
        assert response.json()["count"] == 2

    def test_pagination(self, manager, audit_logs):
        client = self._login_manager(manager)
        response = client.get("/api/v1/audit-logs/", {"page_size": 1, "page": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["results"]) == 1
        assert data["total_pages"] == 2

    @staticmethod
    def _login_manager(manager):
        client = APIClient()
        login_resp = client.post(
            "/api/v1/auth/login/",
            {"email": manager.email, "password": "mgrpass123"},
            format="json",
        )
        token = login_resp.data["access_token"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client


class TestAuditImmutability:
    def test_cannot_update_entry(self, manager):
        AuditLog.objects.create(
            organization=manager.organization,
            event_type="TEST_EVENT",
            actor=manager,
        )
        entry = AuditLog.objects.first()
        # Intentar guardar con cambios
        entry.event_type = "CHANGED"
        with pytest.raises(PermissionError, match="immutable"):
            entry.save()

    def test_cannot_force_update(self, manager):
        entry = AuditLog.objects.create(
            organization=manager.organization,
            event_type="TEST_EVENT",
            actor=manager,
        )
        with pytest.raises(PermissionError):
            entry.save(force_update=True)

    def test_cannot_delete_entry(self, manager):
        entry = AuditLog.objects.create(
            organization=manager.organization,
            event_type="TEST_EVENT",
            actor=manager,
        )
        with pytest.raises(PermissionError, match="immutable"):
            entry.delete()
