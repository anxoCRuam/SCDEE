"""
API v1 URL configuration.

All business endpoints, mounted at /api/v1/ in the root URL config.
"""

from django.urls import include, path

from apps.accounts.views.profile import ProfileView
from apps.annotations.urls import (
    annotation_detail_patterns,
    instance_annotation_patterns,
)
from apps.exams.urls import (
    exam_patterns as exam_detail_patterns,
)
from apps.exams.urls import (
    model_patterns as model_detail_patterns,
)
from apps.exams.urls import (
    page_profile_patterns as page_profile_detail_patterns,
)
from apps.exams.urls import (
    problem_patterns as problem_detail_patterns,
)
from apps.exams.urls import (
    subject_exam_patterns,
)
from apps.exams.urls import (
    zone_patterns as zone_detail_patterns,
)
from apps.exams.views.exams import MyExamsView
from apps.instances.urls import (
    assignment_rule_patterns,
    exam_instance_patterns,
    instance_patterns,
)
from apps.instances.views.instances import MyTasksView, OrphanPageAttachView, OrphanPageListView
from apps.organizations.views.organization_config import (
    OrgConfigView,
)
from apps.organizations.views.search import (
    HierarchicalSearchView,
)
from apps.reviews.urls import (
    exam_review_patterns,
    instance_review_patterns,
    review_request_patterns,
)
from apps.subjects.views.subjects_components import MembershipPermissionsView, MySubjectsView

urlpatterns: list = [
    # ── Audit ──────────────────────────────────────────────
    path("audit-logs/", include("apps.audit.urls")),
    # ── Organizations ──────────────────────────────────────
    path("organizations/", include("apps.organizations.urls")),
    path("config/", OrgConfigView.as_view(), name="org-config"),
    path("search/", HierarchicalSearchView.as_view(), name="hierarchical-search"),
    # ── Courses ────────────────────────────────────────────
    path("courses/", include("apps.courses.urls")),
    # ── Subjects ───────────────────────────────────────────
    path("subjects/", include("apps.subjects.urls")),
    path("my-subjects/", MySubjectsView.as_view(), name="my-subjects"),
    path(
        "memberships/<uuid:membership_pk>/permissions/",
        MembershipPermissionsView.as_view(),
        name="membership-permissions",
    ),
    # ── Authentication & Users ─────────────────────────────
    path("auth/", include("apps.accounts.urls.auth")),
    path("users/", include("apps.accounts.urls.users")),
    path("profile/", ProfileView.as_view(), name="profile"),
    # ── Notifications ──────────────────────────────────────
    path("notifications/", include("apps.notifications.urls")),
    # ── Exams, Models, PageProfiles, Problems ──────────────
    path("subjects/<uuid:subject_pk>/exams/", include(subject_exam_patterns)),
    path("exams/", include(exam_detail_patterns)),
    path("models/", include(model_detail_patterns)),
    path("page-profiles/", include(page_profile_detail_patterns)),
    path("zones/", include(zone_detail_patterns)),
    path("problems/", include(problem_detail_patterns)),
    path("my-exams/", MyExamsView.as_view(), name="my-exams"),
    # ── Instances, Pages, Grading, Assignments ─────────────
    path("exams/<uuid:exam_pk>/", include(exam_instance_patterns + exam_review_patterns)),
    path("instances/", include(instance_patterns)),
    path(
        "instances/<uuid:instance_pk>/",
        include(instance_annotation_patterns + instance_review_patterns),
    ),
    path("assignment-rules/", include(assignment_rule_patterns)),
    path("my-tasks/", MyTasksView.as_view(), name="my-tasks"),
    path("orphan-pages/", OrphanPageListView.as_view(), name="orphan-page-list"),
    path(
        "orphan-pages/<uuid:page_pk>/attach/",
        OrphanPageAttachView.as_view(),
        name="orphan-page-attach",
    ),
    # ── Ingestion & Recognition ──────────────────────
    path("recognition/", include("apps.ingestion.urls")),
    # ── Annotations ──────────────────────────────────
    path("annotations/", include(annotation_detail_patterns)),
    # ── Reviews ──────────────────────────────────────────────
    path("review-requests/", include(review_request_patterns)),
]
