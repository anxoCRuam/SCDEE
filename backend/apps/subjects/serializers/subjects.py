"""
Serializers for subjects, groups and memberships.

All in one module because they're tightly coupled — a subject response
includes groups and member counts, etc.

References: RF-4
"""

# ── Subject serializers ──────────────────────────────────────
from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

from apps.subjects.models.subjects import MembershipRole

SUBJECT_DETAILS_ORG_MANAGER_EXAMPLE = OpenApiExample(
    name="CourseDetailsForManager",
    summary="Full course details returned to organisation managers",
    value={
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "Introduction to Python",
        "code": "CS101",
        "semester": "Fall 2024",
        "course_id": "550e8400-e29b-41d4-a716-446655440001",
        "coordinator_id": "550e8400-e29b-41d4-a716-446655440002",
        "coordinator_email": "coordinator@example.com",
        "created_at": "2024-09-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "groups": [
            {"id": "550e8400-e29b-41d4-a716-446655440010", "label": "Group A"},
            {"id": "550e8400-e29b-41d4-a716-446655440011", "label": "Group B"},
        ],
        "member_counts": {
            "coordinators": 1,
            "teachers": 3,
            "students": 25,
        },
    },
    response_only=True,
)

SUBJECT_DETAILS_NON_MANAGER_EXAMPLE = OpenApiExample(
    name="CourseDetailsForNonManager",
    summary="Reduced course details returned to non‑manager users (active course only)",
    value={
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "name": "Introduction to Python",
        "code": "CS101",
        "semester": "Fall 2024",
        "role": "STUDENT",
        "group_label": "Group A",
        "coordinator_email": "coordinator@example.com",
    },
    response_only=True,
)


class CreateSubjectSerializer(serializers.Serializer):
    """Input for POST /api/v1/subjects/ (RF-4.1)."""

    name = serializers.CharField(required=True, max_length=255)
    code = serializers.CharField(required=True, max_length=50)
    semester = serializers.CharField(required=False, default="", max_length=100)
    coordinator_id = serializers.UUIDField(
        required=True,
        help_text="UUID of the user to assign as coordinator.",
    )


class UpdateSubjectSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/subjects/{id}/ (RF-4.3)."""

    name = serializers.CharField(required=False, max_length=255)
    code = serializers.CharField(required=False, max_length=50)
    semester = serializers.CharField(required=False, max_length=100, allow_blank=True)
    coordinator_id = serializers.UUIDField(
        required=False,
        help_text="New coordinator UUID. Old coordinator becomes TEACHER.",
    )


class SubjectResponseSerializer(serializers.Serializer):
    """Output for subject endpoints (RF-4.4, RF-4.9)."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    code = serializers.CharField(read_only=True)
    semester = serializers.CharField(read_only=True)
    course_id = serializers.UUIDField(read_only=True)
    coordinator_id = serializers.UUIDField(read_only=True)
    coordinator_email = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def get_coordinator_email(self, obj) -> str:
        if hasattr(obj, "coordinator") and obj.coordinator:
            return obj.coordinator.email
        return ""


class SubjectDetailSerializer(SubjectResponseSerializer):
    """Extended output with groups and member counts (RF-4.9)."""

    groups = serializers.SerializerMethodField()
    member_counts = serializers.SerializerMethodField()

    def get_groups(self, obj) -> list[dict]:
        return [{"id": str(g.pk), "label": g.label} for g in obj.groups.all()]

    def get_member_counts(self, obj) -> dict:
        memberships = obj.memberships.filter(is_active=True)
        return {
            "coordinators": memberships.filter(role=MembershipRole.COORDINATOR).count(),
            "teachers": memberships.filter(role=MembershipRole.TEACHER).count(),
            "students": memberships.filter(role=MembershipRole.STUDENT).count(),
        }


# ── Group serializers ────────────────────────────────────────


class CreateGroupSerializer(serializers.Serializer):
    """Input for POST /api/v1/subjects/{id}/groups/ (RF-4.5)."""

    label = serializers.CharField(required=True, max_length=50)


class UpdateGroupSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/subjects/{sid}/groups/{gid}/ (RF-4.5)."""

    label = serializers.CharField(required=True, max_length=50)


class GroupResponseSerializer(serializers.Serializer):
    """Output for group endpoints."""

    id = serializers.UUIDField(read_only=True)
    label = serializers.CharField(read_only=True)
    subject_id = serializers.UUIDField(read_only=True)
    member_count = serializers.IntegerField(read_only=True, default=0)


# ── Membership serializers ───────────────────────────────────


class AssignMemberSerializer(serializers.Serializer):
    """Input for POST /api/v1/subjects/{id}/members/ (RF-4.6)."""

    user_id = serializers.UUIDField(required=True)
    role = serializers.ChoiceField(
        choices=[MembershipRole.TEACHER, MembershipRole.STUDENT],
        required=True,
        help_text="TEACHER or STUDENT. COORDINATOR is set via subject creation/update.",
    )
    group_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional group assignment.",
    )


class MembershipResponseSerializer(serializers.Serializer):
    """Output for membership endpoints."""

    id = serializers.UUIDField(read_only=True)
    user_id = serializers.UUIDField(read_only=True)
    user_email = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    subject_id = serializers.UUIDField(read_only=True)
    role = serializers.CharField(read_only=True)
    group_id = serializers.UUIDField(read_only=True, allow_null=True)
    group_label = serializers.SerializerMethodField()
    permission_overrides = serializers.JSONField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    def get_user_email(self, obj) -> str:
        return obj.user.email if hasattr(obj, "user") else ""

    def get_user_name(self, obj) -> str:
        if hasattr(obj, "user"):
            return f"{obj.user.first_name} {obj.user.last_name}"
        return ""

    def get_group_label(self, obj) -> str:
        return obj.group.label if obj.group else ""


# ── My-subjects serializer ──────────────────────────────────


class MySubjectSerializer(serializers.Serializer):
    """Output for GET /api/v1/my-subjects/ (RF-4.8)."""

    id = serializers.UUIDField(source="subject.id", read_only=True)
    name = serializers.CharField(source="subject.name", read_only=True)
    code = serializers.CharField(source="subject.code", read_only=True)
    semester = serializers.CharField(source="subject.semester", read_only=True)
    role = serializers.CharField(read_only=True)
    group_label = serializers.SerializerMethodField()
    coordinator_email = serializers.SerializerMethodField()  # NUEVO

    def get_group_label(self, obj) -> str:
        return obj.group.label if obj.group else ""

    def get_coordinator_email(self, obj) -> str:  # NUEVO
        return obj.subject.coordinator.email if obj.subject.coordinator else ""
