"""
End-to-end grading flow test (integration).

Walks the full spine of the system at the service layer:

    org → manager → course → subject → exam → model → problem
        → ExamInstance (with student) → Grade → bulk publish
        → Notification + email mirror enqueued.

This is intentionally service-layer (no HTTP, no Celery worker), so it
runs in any environment without MinIO / Redis dependencies. The
HTTP-and-Celery scenario is exercised by the per-app endpoint tests
plus docker-compose smoke runs by the operator.

References: RF-2.1, RF-3.1, RF-4.1, RF-6.1, RF-7.1, RF-7.9, RF-11.1, RF-13.1
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.courses.services.courses import create_course
from apps.exams.models.exams import Exam, ExamModel, Problem
from apps.grading.services.grading import grade_problem
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.instances.services.instance_service import bulk_publish, transition_instance
from apps.notifications.models.notifications import Notification, NotificationType
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import (
    MembershipRole,
    Subject,
    SubjectMembership,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]
VALID_KEY = "a" * 64


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_full_grading_flow_publishes_grades_and_notifies_student(monkeypatch):
    """The publish step must mark the student-owning instance as PUBLISHED
    and create a GRADES_PUBLISHED notification for that student."""

    # Disable Celery dispatch so the email mirror does not try to reach
    # a real broker — the in-app row is what we assert on.
    monkeypatch.setattr(
        "apps.notifications.services.notifications._enqueue_email_mirror",
        lambda notification: None,
    )

    user_model = get_user_model()

    # 1. Organisation and a manager (RF-2.1 / RF-2.2).
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"m-{uuid.uuid4().hex[:8]}@x.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Mgr",
        last_name="U",
        organization=org,
        is_staff=True,
    )

    # 2. First academic course (RF-3.1).
    course, _ = create_course(organization=org, label="2026")
    assert course.is_active is True

    # 3. Subject + coordinator membership (RF-4.1).
    subject = Subject(
        organization=org,
        name="Calculus",
        code=f"S{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=manager,
    )
    subject.save()
    SubjectMembership.objects.create(
        organization=org,
        user=manager,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )

    # 4. Student enrolled in the subject.
    student = user_model.objects.create_user(
        email=f"s-{uuid.uuid4().hex[:8]}@x.com",
        password="StuPass123!",  # noqa: S106
        first_name="Stu",
        last_name="U",
        organization=org,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=student,
        subject=subject,
        role=MembershipRole.STUDENT,
        is_active=True,
    )

    # 5. Exam, model, single problem (RF-6.1, RF-6.5, RF-6.9).
    exam = Exam.objects.create(organization=org, name="Mid-term", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem = Problem.objects.create(
        name="P1", max_score=Decimal("10.00"), exam_model=model, order=1
    )

    # 6. Instance owned by the student in PENDING_GRADING.
    instance = ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model,
        student=student,
        status=InstanceStatus.PENDING_GRADING,
        expected_pages=1,
    )

    # 7. Grade the problem (RF-11.1).
    grade_problem(
        instance=instance,
        problem=problem,
        score=Decimal("8.5"),
        grader=manager,
        old_score=None,
    )
    instance.refresh_from_db()
    assert instance.total_score == Decimal("8.5")

    # 8. Move PENDING_GRADING → GRADED, then bulk-publish (RF-7.5, RF-7.9).
    transition_instance(instance, InstanceStatus.GRADED)
    result = bulk_publish(exam, notify_students=True)
    assert result["published"] == 1
    assert result["skipped"] == 0

    instance.refresh_from_db()
    assert instance.status == InstanceStatus.PUBLISHED

    # 9. The student got a GRADES_PUBLISHED in-app notification (RF-13.1).
    student_notifs = Notification.objects.filter(
        user=student, notification_type=NotificationType.GRADES_PUBLISHED
    )
    assert student_notifs.count() == 1
    assert "Mid-term" in student_notifs.first().message
