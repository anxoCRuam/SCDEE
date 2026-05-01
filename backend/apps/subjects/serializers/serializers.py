"""
Serializers for subjects, groups, memberships, and permissions.

All in one module because they're tightly coupled — a subject response
includes groups and member counts, memberships include permissions, etc.

References: RF-4.1 through RF-4.10, RF-5.2 through RF-5.6
"""

from rest_framework import serializers

from apps.subjects.models import ALL_PERMISSION_KEYS, MembershipRole

# ── Subject serializers ──────────────────────────────────────


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


# ── Permission serializers ───────────────────────────────────


class UpdatePermissionsSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/memberships/{id}/permissions/ (RF-5.3, RF-5.4).

    Values: true (grant), false (revoke), null (restore default).
    Only recognized permission keys are accepted.
    """

    permissions = serializers.DictField(
        child=serializers.BooleanField(allow_null=True),
        required=True,
        help_text="Dict of permission_name → bool|null.",
    )

    def validate_permissions(self, value: dict) -> dict:
        """Reject unknown permission keys."""
        unknown = set(value.keys()) - set(ALL_PERMISSION_KEYS)
        if unknown:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(sorted(unknown))}")
        return value


class EffectivePermissionsSerializer(serializers.Serializer):
    """Output for GET /api/v1/memberships/{id}/effective-permissions/ (RF-5.5)."""

    permissions = serializers.DictField(
        child=serializers.BooleanField(),
        read_only=True,
    )
    role = serializers.CharField(read_only=True)
    has_overrides = serializers.BooleanField(read_only=True)
    is_staff_bypass = serializers.BooleanField(
        read_only=True,
        help_text="True if the user has manager bypass (all permissions via is_staff).",
    )


# ── My-subjects serializer ──────────────────────────────────


class MySubjectSerializer(serializers.Serializer):
    """Output for GET /api/v1/my-subjects/ (RF-4.8)."""

    id = serializers.UUIDField(source="subject.id", read_only=True)
    name = serializers.CharField(source="subject.name", read_only=True)
    code = serializers.CharField(source="subject.code", read_only=True)
    semester = serializers.CharField(source="subject.semester", read_only=True)
    role = serializers.CharField(read_only=True)
    group_label = serializers.SerializerMethodField()

    def get_group_label(self, obj) -> str:
        return obj.group.label if obj.group else ""
