"""
Service-level tests for the ExamInstance state machine (RF-7.5).

The endpoint-level test_instances.py already covers a happy path and a
single rejection. Here we exhaustively walk every legal transition and
sample the rejections — and we test ``transition_instance`` directly so
we exercise the ``select_for_update`` lock fix from D.2 without going
through the HTTP layer.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models import AcademicCourse
from apps.exams.models import Exam, ExamModel
from apps.instances.models import (
    VALID_TRANSITIONS,
    ExamInstance,
    InstanceIssueType,
    InstanceStatus,
    is_valid_transition,
)
from apps.instances.services.instance_service import (
    InstanceServiceError,
    transition_instance,
)
from apps.organizations.models import Organization
from apps.subjects.models import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def instance():
    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    user = user_model.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="X",
        last_name="Y",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2026", is_active=True, status="ACTIVE")
    course.save()
    subject = Subject(
        organization=org,
        name="S",
        code=f"S{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=user,
    )
    subject.save()
    SubjectMembership.objects.create(
        organization=org,
        user=user,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="E", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    return ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model,
        status=InstanceStatus.ASSEMBLING,
        expected_pages=1,
    )


# ── Pure transition table ────────────────────────────────────


def _legal_pairs() -> list[tuple[str, str]]:
    return [(src, dst) for src, dsts in VALID_TRANSITIONS.items() for dst in dsts]


def _illegal_pairs() -> list[tuple[str, str]]:
    all_states = list(VALID_TRANSITIONS.keys())
    legal = set(_legal_pairs())
    return [
        (src, dst)
        for src in all_states
        for dst in all_states
        if src != dst and (src, dst) not in legal
    ]


@pytest.mark.parametrize(("src", "dst"), _legal_pairs())
def test_legal_transition_predicate(src: str, dst: str):
    assert is_valid_transition(src, dst)


@pytest.mark.parametrize(("src", "dst"), _illegal_pairs()[:25])  # sample, not exhaustive
def test_illegal_transition_predicate(src: str, dst: str):
    assert not is_valid_transition(src, dst)


def test_archived_is_terminal():
    assert VALID_TRANSITIONS[InstanceStatus.ARCHIVED] == set()


# ── Service-level transition (RF-7.5, fix D.2) ──────────────


class TestTransitionService:
    def test_legal_transition_persists(self, instance):
        instance.status = InstanceStatus.QUEUED
        instance.save()

        transition_instance(instance, InstanceStatus.PENDING_GRADING)

        instance.refresh_from_db()
        assert instance.status == InstanceStatus.PENDING_GRADING

    def test_illegal_transition_raises(self, instance):
        # ASSEMBLING → GRADED is not in VALID_TRANSITIONS.
        with pytest.raises(InstanceServiceError) as exc_info:
            transition_instance(instance, InstanceStatus.GRADED)
        assert exc_info.value.code == "INVALID_TRANSITION"

    def test_published_requires_no_issues(self, instance):
        instance.status = InstanceStatus.GRADED
        instance.add_issue(InstanceIssueType.STUDENT_NOT_IDENTIFIED)
        instance.save()

        with pytest.raises(InstanceServiceError) as exc_info:
            transition_instance(instance, InstanceStatus.PUBLISHED)
        assert exc_info.value.code == "HAS_UNRESOLVED_ISSUES"

        instance.refresh_from_db()
        assert instance.status == InstanceStatus.GRADED  # unchanged

    def test_retroceso_graded_to_pending_grading(self, instance):
        instance.status = InstanceStatus.GRADED
        instance.save()

        transition_instance(instance, InstanceStatus.PENDING_GRADING)

        instance.refresh_from_db()
        assert instance.status == InstanceStatus.PENDING_GRADING

    def test_retroceso_published_to_graded(self, instance):
        instance.status = InstanceStatus.PUBLISHED
        instance.save()

        transition_instance(instance, InstanceStatus.GRADED)

        instance.refresh_from_db()
        assert instance.status == InstanceStatus.GRADED

    def test_transition_after_concurrent_status_drift_is_rejected(self, instance):
        """Validates the select_for_update guard from D.2.

        We simulate a concurrent peer mutation by writing a *different*
        status to the row between the caller's read and the transition
        call. ``transition_instance`` reloads under SELECT FOR UPDATE,
        sees the new status, and rejects the requested transition based
        on the *current* state — not on what the caller had in memory.
        """
        # Caller observed status=GRADED on its in-memory copy.
        instance.status = InstanceStatus.GRADED

        # Peer mutates the row in the DB to ARCHIVED (terminal).
        ExamInstance.unfiltered.filter(pk=instance.pk).update(status=InstanceStatus.ARCHIVED)

        # Caller still believes the row is GRADED and asks for PUBLISHED.
        # The service must use the locked, on-disk status (ARCHIVED) and
        # reject the transition.
        with pytest.raises(InstanceServiceError) as exc_info:
            transition_instance(instance, InstanceStatus.PUBLISHED)

        assert exc_info.value.code == "INVALID_TRANSITION"
