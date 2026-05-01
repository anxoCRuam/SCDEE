"""
Tests for Phases 11-13: maintenance, security, and deployment.

Covers:
- Auto-deletion task logic (RF-15.3)
- Deletion notice check (RF-15.4)
- Watermark service (RF-16.6)
- Anti-cache middleware (RF-16.7)
- Rate limiting configuration (RNF-6)
- SSO placeholder models (RF-1.5)
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.test import RequestFactory, TestCase, override_settings

from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── Anti-cache middleware (RF-16.7) ──────────────────────────


class TestAntiCacheMiddleware:
    def test_adds_no_cache_headers_to_api(self):
        from apps.core.middleware import AntiCacheMiddleware

        def get_response(request):
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = AntiCacheMiddleware(get_response)
        factory = RequestFactory()
        request = factory.get("/api/v1/profile/")

        response = middleware(request)

        assert response["Cache-Control"] == "no-store, no-cache, must-revalidate"
        assert response["Pragma"] == "no-cache"
        assert response["Expires"] == "0"

    def test_skips_non_api_paths(self):
        from apps.core.middleware import AntiCacheMiddleware

        def get_response(request):
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = AntiCacheMiddleware(get_response)
        factory = RequestFactory()
        request = factory.get("/health/")

        response = middleware(request)

        assert "Cache-Control" not in response


# ── Auto-deletion logic (RF-15.3) ────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAutoDeletion(TestCase):
    def test_delete_old_archived_courses(self):
        """Exams from very old archived courses should be deletable."""
        from apps.courses.models import AcademicCourse, CourseStatus
        from apps.organizations.models import OrganizationConfig

        org = Organization.objects.create(name="Del Org", subdomain=f"d-{uuid.uuid4().hex[:8]}")

        # Create config with 30-day retention.
        OrganizationConfig.objects.create(
            organization=org,
            auto_delete_frequency_days=30,
        )

        # Create an archived course older than 30 days.
        course = AcademicCourse(
            organization=org,
            label="Old",
            is_active=False,
            status=CourseStatus.ARCHIVED,
        )
        course.save()
        # Backdate it.
        AcademicCourse.unfiltered.filter(pk=course.pk).update(
            updated_at=datetime.now(tz=UTC) - timedelta(days=60)
        )

        # Verify the course exists.
        assert AcademicCourse.unfiltered.filter(pk=course.pk).exists()

        # The task would delete data from this course.
        # We test the logic without actually running the full task.
        from apps.core.tasks import _delete_course_data

        result = _delete_course_data(course, org)
        # No exams to delete in this test, but the function runs cleanly.
        assert result["exams"] == 0

    def test_audit_log_entries_are_preserved(self):
        """RF-16.1 requires the AuditLog never to be touched by maintenance.

        We seed AuditLog rows scoped to the organisation, run the
        course-data deletion, and verify the audit rows survive.
        """
        from decimal import Decimal

        from apps.audit.models import AuditLog
        from apps.core.tasks import _delete_course_data
        from apps.courses.models import AcademicCourse, CourseStatus
        from apps.exams.models import Exam, ExamModel
        from apps.subjects.models import (
            MembershipRole,
            Subject,
            SubjectMembership,
        )

        org = Organization.objects.create(name="Audit Org", subdomain=f"a-{uuid.uuid4().hex[:8]}")
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.create_user(
            email=f"a-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="A",
            last_name="C",
            organization=org,
            is_staff=True,
        )
        course = AcademicCourse(
            organization=org,
            label="Old",
            is_active=False,
            status=CourseStatus.ARCHIVED,
        )
        course.save()
        subject = Subject(
            organization=org,
            name="S",
            code=f"S{uuid.uuid4().hex[:4]}",
            course=course,
            coordinator=actor,
        )
        subject.save()
        SubjectMembership.objects.create(
            organization=org,
            user=actor,
            subject=subject,
            role=MembershipRole.COORDINATOR,
            is_active=True,
        )
        exam = Exam.objects.create(organization=org, name="ToBeDeleted", subject=subject)
        ExamModel.objects.create(label="A", exam=exam)

        # Seed two audit rows for this org and one for a different org.
        AuditLog.objects.create(
            organization=org,
            event_type="USER_CREATED",
            actor=actor,
            entity_type="User",
            entity_id=str(actor.pk),
            payload={"k": Decimal("1") and 1},
        )
        AuditLog.objects.create(
            organization=org,
            event_type="LOGIN_SUCCESS",
            actor=actor,
            entity_type="User",
            entity_id=str(actor.pk),
        )
        other_org = Organization.objects.create(
            name="Other", subdomain=f"oth-{uuid.uuid4().hex[:8]}"
        )
        AuditLog.objects.create(
            organization=other_org, event_type="LOGIN_SUCCESS", entity_type="User"
        )
        baseline_count = AuditLog.objects.count()

        # Run the maintenance step.
        result = _delete_course_data(course, org)
        assert result["exams"] == 1

        # All audit log rows survived (RF-16.1).
        assert AuditLog.objects.count() == baseline_count
        # And the rows scoped to the deleted course's org are still present.
        assert AuditLog.objects.filter(organization=org, event_type="USER_CREATED").count() == 1
