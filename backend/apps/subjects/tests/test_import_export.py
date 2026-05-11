"""
Tests for import and export subjects
"""

import json
import uuid

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models.auditlog import AuditLog
from apps.courses.models.courses import AcademicCourse
from apps.organizations.models.organization import Organization
from apps.subjects.models.permissions import MembershipRole
from apps.subjects.models.subjects import (
    Subject,
    SubjectMembership,
)

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── Test helpers ─────────────────────────────────────────────


def _setup_org_with_course():
    """Create org + manager + active course. Returns (org, manager, course)."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Test Org", subdomain=f"org-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"mgr-{uuid.uuid4().hex[:8]}@example.com",
        password="MgrPass123!",  # noqa S106
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2025-2026", is_active=True)
    course.save()
    return org, manager, course


def _create_user(org, **kwargs):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = kwargs.pop("email", f"u-{uuid.uuid4().hex[:8]}@example.com")
    return user_model.objects.create_user(
        email=email,
        password=kwargs.pop("password", "Pass123!"),
        first_name=kwargs.pop("first_name", "Test"),
        last_name=kwargs.pop("last_name", "User"),
        organization=org,
        **kwargs,
    )


def _auth_client(user, password="MgrPass123!"):  # noqa S106
    reset_auth_plugin()
    client = APIClient()
    resp = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")
    return client


# ── Import/Export (RF-4.2, RF-4.10) ──────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestSubjectImportExport(TestCase):
    def _create_coord(self, org):
        return _create_user(
            org,
            email=f"coord-{uuid.uuid4().hex[:6]}@example.com",
            password="Pass123!",  # noqa S106
        )

    def _import(self, client, data):
        return client.post("/api/v1/subjects/import/", data, format="json")

    def _export(self, client, fmt="json", course_id=None):
        url = f"/api/v1/subjects/export/?export_format={fmt}"
        if course_id:
            url += f"&course_id={course_id}"
        return client.get(url)

    # ── JSON import ────────────────────────────────────────
    def test_import_json_creates_subject_and_groups(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        client = _auth_client(mgr)

        data = [
            {
                "name": "Física I",
                "code": "FIS1",
                "coordinator_email": coord.email,
                "groups": ["A", "B"],
                "teachers": [],
                "students": [],
            }
        ]
        resp = self._import(client, data)
        assert resp.status_code == 200
        assert resp.data["created"] == 1
        assert resp.data["total_errors"] == 0

        subject = Subject.unfiltered.get(code="FIS1")
        assert subject.groups.count() == 2

    def test_import_json_assigns_members(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        teacher = _create_user(org, email="profe@example.com", password="Pass123!")  # noqa S106
        student = _create_user(org, email="alumno@example.com", password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        data = [
            {
                "name": "Química",
                "code": "QUI1",
                "coordinator_email": coord.email,
                "groups": ["G1"],
                "teachers": [teacher.email],
                "students": [{"email": student.email, "group": "G1"}],
            }
        ]
        resp = self._import(client, data)
        assert resp.status_code == 200

        # Verificar membresías
        subject = Subject.unfiltered.get(code="QUI1")
        self.assertTrue(
            SubjectMembership.unfiltered.filter(
                user=coord, subject=subject, role=MembershipRole.COORDINATOR, is_active=True
            ).exists()
        )
        self.assertTrue(
            SubjectMembership.unfiltered.filter(
                user=teacher, subject=subject, role=MembershipRole.TEACHER, is_active=True
            ).exists()
        )
        self.assertTrue(
            SubjectMembership.unfiltered.filter(
                user=student, subject=subject, role=MembershipRole.STUDENT, is_active=True
            ).exists()
        )

    # ── CSV import ─────────────────────────────────────────
    def test_import_csv_file(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        client = _auth_client(mgr)

        csv_content = (
            "name,code,semester,coordinator_email,groups,teachers,students\n"
            f"Bio, BIO1,1er cuatri,{coord.email},G1;G2,,\n"
        )

        from django.core.files.uploadedfile import SimpleUploadedFile

        uploaded_file = SimpleUploadedFile(
            name="subjects.csv",
            content=csv_content.encode("utf-8-sig"),
            content_type="text/csv",
        )
        resp = client.post(
            "/api/v1/subjects/import/",
            {"file": uploaded_file},
            format="multipart",
        )
        assert resp.status_code == 200
        assert resp.data["created"] == 1
        assert Subject.unfiltered.filter(code="BIO1").exists()

    # ── Export JSON ────────────────────────────────────────
    def test_export_json_matches_import_format(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        client = _auth_client(mgr)

        # Crear subject con miembros
        subject_data = {
            "name": "Exportable",
            "code": "EXP1",
            "coordinator_id": str(coord.pk),
        }
        create_resp = client.post("/api/v1/subjects/", subject_data, format="json")
        sid = create_resp.data["id"]
        # Añadir un grupo y un profesor
        client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "GX"}, format="json")
        teacher = _create_user(org, email="tchr@example.com", password="Pass123!")  # noqa S106
        client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )

        # Exportar JSON
        export_resp = self._export(client, fmt="json")
        assert export_resp.status_code == 200
        data = export_resp.data
        assert isinstance(data, list)
        assert len(data) >= 1
        exported = next(s for s in data if s["code"] == "EXP1")
        assert exported["name"] == "Exportable"
        assert "GX" in exported["groups"]
        assert teacher.email in exported["teachers"]
        # No debería tener member_counts (formato de importación)
        assert "member_counts" not in exported

    # ── Export CSV ─────────────────────────────────────────
    def test_export_csv_download(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        client = _auth_client(mgr)
        client.post(
            "/api/v1/subjects/",
            {"name": "CSV", "code": "CSV1", "coordinator_id": str(coord.pk)},
            format="json",
        )

        resp = self._export(client, fmt="csv")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv"
        # Verificar cabeceras
        content = resp.content.decode("utf-8")
        self.assertIn(
            "name,code,semester,coordinator_email,groups,teachers,students", content.split("\n")[0]
        )

    # ── Round‑trip JSON export → import ──────────────────
    def test_export_import_roundtrip_json(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        client = _auth_client(mgr)

        # Crear asignaturas de partida
        for i in range(2):
            client.post(
                "/api/v1/subjects/",
                {"name": f"Round{i}", "code": f"R{i}", "coordinator_id": str(coord.pk)},
                format="json",
            )

        # Exportar
        exported = client.get("/api/v1/subjects/export/?format=json").json()
        # Importar los mismos datos
        for item in exported:
            item["code"] += "_2"
        resp = client.post("/api/v1/subjects/import/", exported, format="json")
        assert resp.status_code == 200
        assert resp.data["created"] == 2

    # ── Errores esperados ─────────────────────────────────
    def test_import_missing_fields_errors(self):
        org, mgr, course = _setup_org_with_course()
        client = _auth_client(mgr)
        data = [{"name": "Sin código", "coordinator_email": "none@example.com"}]
        resp = self._import(client, data)
        assert resp.data["total_errors"] == 1
        assert resp.data["errors"][0]["error"] == "VALIDATION_ERROR"

    def test_import_coordinator_not_found(self):
        org, mgr, course = _setup_org_with_course()
        client = _auth_client(mgr)
        data = [{"name": "X", "code": "X1", "coordinator_email": "fake@example.com"}]
        resp = self._import(client, data)
        assert resp.data["total_errors"] == 1
        assert resp.data["errors"][0]["error"] == "COORDINATOR_NOT_FOUND"

    def test_import_tolerates_duplicate_memberships(self):
        org, mgr, course = _setup_org_with_course()
        coord = self._create_coord(org)
        teacher = _create_user(org, email="dupteacher@example.com", password="Pass123!")  # noqa S106
        client = _auth_client(mgr)
        # Importar una vez con profesor
        data = [
            {
                "name": "Tol",
                "code": "TOL",
                "coordinator_email": coord.email,
                "teachers": [teacher.email],
                "students": [],
            }
        ]
        self._import(client, data)
        # Importar de nuevo con el mismo profesor (no debería dar error)
        resp = self._import(client, data)
        assert resp.data["created"] == 1
        assert resp.data["total_errors"] == 0

    def test_import_subjects_json(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, email="coord-imp@example.com", password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        import_data = [
            {
                "name": "Imported Subject",
                "code": "IMP1",
                "semester": "1er cuatrimestre",
                "coordinator_email": coord.email,
                "groups": ["G1", "G2"],
                "teachers": [],
                "students": [],
            }
        ]

        resp = client.post("/api/v1/subjects/import/", import_data, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["created"] == 1

        # Verify subject exists.
        assert Subject.unfiltered.filter(code="IMP1").exists()

        # Verify groups created.
        subject = Subject.unfiltered.get(code="IMP1")
        assert subject.groups.count() == 2

    def test_export_subjects_json(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        client.post(
            "/api/v1/subjects/",
            {"name": "Export Me", "code": "EXP", "coordinator_id": str(coord.pk)},
            format="json",
        )

        resp = client.get("/api/v1/subjects/export/?format=json")
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content)
        assert len(data) >= 1
        assert any(s["code"] == "EXP" for s in data)

    def test_audit_logs_created(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        client.post(
            "/api/v1/subjects/",
            {"name": "Audit", "code": "AUD", "coordinator_id": str(coord.pk)},
            format="json",
        )

        assert AuditLog.objects.filter(event_type="SUBJECT_CREATED").exists()
