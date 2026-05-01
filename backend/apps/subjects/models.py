"""
Subject, SubjectGroup, and SubjectMembership models.

The academic hierarchy:
    Organization → Course → Subject → [Groups, Memberships, Exams]

Subject: An academic course unit (e.g. "Matemáticas I").
SubjectGroup: A group within a subject (e.g. "G1", "2121").
SubjectMembership: Links a user to a subject with a role and permissions.

The permission system uses a layered approach (RF-5):
    1. is_staff → full access to all subjects in the org
    2. SubjectMembership.role → base permissions (COORDINATOR > TEACHER > STUDENT)
    3. permission_overrides (JSONField) → per-membership overrides

References: RF-4.1, RF-4.5, RF-4.6, RF-5.2
"""

from django.conf import settings
from django.db import models

from apps.core.models import OrganizationOwnedModel, TimestampedModel


class MembershipRole(models.TextChoices):
    """Role within a subject context."""

    COORDINATOR = "COORDINATOR", "Coordinator"
    TEACHER = "TEACHER", "Teacher"
    STUDENT = "STUDENT", "Student"


# ── Default permissions per role (RF-5.2) ────────────────────
# These are the base permissions before any overrides.
# In Fase 10, OrganizationConfig will allow per-org customization.

DEFAULT_PERMISSIONS: dict[str, dict[str, bool]] = {
    MembershipRole.COORDINATOR: {
        "can_create_exams": True,
        "can_manage_models": True,
        "can_manage_call_list": True,
        "can_assign_correctors": True,
        "can_grade": True,
        "can_annotate": True,
        "can_resolve_issues": True,
        "can_publish_grades": True,
        "can_manage_reviews": True,
        "can_view_all_instances": True,
        "can_export_grades": True,
        "can_manage_members": True,
        "can_delegate_perms": True,
    },
    MembershipRole.TEACHER: {
        "can_create_exams": False,
        "can_manage_models": False,
        "can_manage_call_list": False,
        "can_assign_correctors": False,
        "can_grade": True,
        "can_annotate": True,
        "can_resolve_issues": False,
        "can_publish_grades": False,
        "can_manage_reviews": False,
        "can_view_all_instances": False,
        "can_export_grades": False,
        "can_manage_members": False,
        "can_delegate_perms": False,
    },
    MembershipRole.STUDENT: {
        "can_create_exams": False,
        "can_manage_models": False,
        "can_manage_call_list": False,
        "can_assign_correctors": False,
        "can_grade": False,
        "can_annotate": False,
        "can_resolve_issues": False,
        "can_publish_grades": False,
        "can_manage_reviews": False,
        "can_view_all_instances": False,
        "can_export_grades": False,
        "can_manage_members": False,
        "can_delegate_perms": False,
    },
}

ALL_PERMISSION_KEYS = list(DEFAULT_PERMISSIONS[MembershipRole.COORDINATOR].keys())


def get_default_permissions(role: str, organization=None) -> dict[str, bool]:
    """Get the default permissions for a role.

    Resolution order (RF-15.5):
        1. ``organization.config.role_permission_defaults[role]`` if configured.
        2. The hard-coded ``DEFAULT_PERMISSIONS`` table.

    Per-org overrides are merged on top of the code defaults so that a partial
    override (only one or two keys configured for a role) still yields a
    complete permission dict with the rest filled in by the code defaults.

    Args:
        role: MembershipRole value.
        organization: Optional Organization instance whose config should be
            consulted. Falls back to the code defaults if not provided or if
            the organization has no override for this role.

    Returns:
        Dict of permission_name → bool with all 13 known keys.
    """
    base = dict(DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS[MembershipRole.STUDENT]))

    if organization is None:
        return base

    config = getattr(organization, "config", None)
    if config is None:
        return base

    org_defaults = (config.role_permission_defaults or {}).get(role)
    if not isinstance(org_defaults, dict):
        return base

    for key, value in org_defaults.items():
        if key in base and isinstance(value, bool):
            base[key] = value
    return base


class Subject(OrganizationOwnedModel):
    """An academic subject (asignatura) within a course.

    Each subject belongs to exactly one course and has exactly one
    coordinator. Groups and memberships are managed separately.

    Attributes:
        name: Display name (e.g. "Estructuras de Datos").
        code: Unique code within the course (e.g. "EDA").
        semester: Free-form label (e.g. "1er cuatrimestre", "anual").
        course: FK to the academic course this subject belongs to.
        coordinator: FK to the user who coordinates this subject.
    """

    name = models.CharField(max_length=255)
    code = models.CharField(
        max_length=50,
        help_text="Unique code within the course (e.g. 'EDA').",
    )
    semester = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Free-form period label (e.g. '1er cuatrimestre').",
    )
    course = models.ForeignKey(
        "courses.AcademicCourse",
        on_delete=models.CASCADE,
        related_name="subject_set",
        help_text="The academic course this subject belongs to.",
    )
    coordinator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="coordinated_subjects",
        help_text="User who coordinates this subject.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code", "course"],
                name="unique_subject_code_per_course",
            ),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class SubjectGroup(TimestampedModel):
    """A group within a subject (e.g. "G1", "2121").

    Groups are exclusive to their subject: identifier "G1" in
    Subject A is independent from "G1" in Subject B.

    Attributes:
        label: Alphanumeric group identifier.
        subject: FK to the subject this group belongs to.
    """

    label = models.CharField(
        max_length=50,
        help_text="Alphanumeric group identifier (e.g. 'G1', '2121').",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="groups",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["label", "subject"],
                name="unique_group_label_per_subject",
            ),
        ]
        ordering = ["label"]

    def __str__(self) -> str:
        return f"{self.label} ({self.subject.code})"


class SubjectMembership(OrganizationOwnedModel):
    """Links a user to a subject with a role and permissions.

    This is the central authorization model for subject-level access.
    Each membership defines:
    - Which subject the user belongs to
    - Their role (COORDINATOR, TEACHER, STUDENT)
    - Their group (optional for teachers, required-ish for students)
    - Permission overrides (JSON dict overlaid on role defaults)

    Attributes:
        user: The user who belongs to this subject.
        subject: The subject they belong to.
        role: Their role within the subject context.
        group: Optional group assignment (FK to SubjectGroup).
        permission_overrides: JSON dict of per-user permission overrides.
            Empty dict means "use role defaults". Keys are permission
            names, values are booleans. Null values restore defaults.
        is_active: Soft-delete flag (deactivated during course transition).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subject_memberships",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=MembershipRole.choices,
    )
    group = models.ForeignKey(
        SubjectGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships",
        help_text="Group assignment. Null = no group assigned.",
    )
    permission_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-user permission overrides. Empty = use role defaults.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            # A user can only have one active membership per subject.
            models.UniqueConstraint(
                fields=["user", "subject"],
                condition=models.Q(is_active=True),
                name="unique_active_membership_per_subject",
            ),
        ]
        indexes = [
            models.Index(
                fields=["subject", "role", "is_active"],
                name="idx_membership_subject_role",
            ),
            models.Index(
                fields=["user", "is_active"],
                name="idx_membership_user_active",
            ),
        ]
        ordering = ["user__last_name", "user__first_name"]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.subject.code} ({self.role})"

    def get_effective_permissions(self) -> dict[str, bool]:
        """Calculate effective permissions for this membership.

        Resolution (RF-5.2, RF-15.5):
            1. Start from ``get_default_permissions(role, organization)``,
               which already merges per-org overrides on top of the code
               defaults.
            2. Apply membership-level overrides on top.
            3. ``None`` values in overrides are ignored (restore default).

        Returns:
            Dict of permission_name → bool with all 13 permissions.
        """
        base = get_default_permissions(self.role, organization=self.organization)

        if not self.permission_overrides:
            return base

        for key, value in self.permission_overrides.items():
            if key in base and isinstance(value, bool):
                base[key] = value

        return base
