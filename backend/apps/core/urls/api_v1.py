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
from apps.instances.urls import (
    assignment_rule_patterns,
    exam_instance_patterns,
    instance_patterns,
)
from apps.instances.views import MyTasksView
from apps.organizations.views import (
    HierarchicalSearchView,
    OrgConfigView,
)
from apps.reviews.urls import (
    exam_review_patterns,
    instance_review_patterns,
    review_request_patterns,
)
from apps.subjects.views.views import MembershipPermissionsView, MySubjectsView

urlpatterns: list = [
    # ── Fase 1: Authentication & Users ───────────────────────
    path("auth/", include("apps.accounts.urls.auth")),
    path("organizations/", include("apps.organizations.urls")),
    path("users/", include("apps.accounts.urls.users")),
    path("profile/", ProfileView.as_view(), name="profile"),
    # ── Fase 2: Courses ──────────────────────────────────────
    path("courses/", include("apps.courses.urls")),
    # ── Fase 3: Subjects, Groups, Members, Permissions ───────
    path("subjects/", include("apps.subjects.urls")),
    path("my-subjects/", MySubjectsView.as_view(), name="my-subjects"),
    path(
        "memberships/<uuid:membership_pk>/permissions/",
        MembershipPermissionsView.as_view(),
        name="membership-permissions",
    ),
    # ── Fase 4: Exams, Models, PageProfiles, Problems ────────
    path("subjects/<uuid:subject_pk>/exams/", include(subject_exam_patterns)),
    path("exams/", include(exam_detail_patterns)),
    path("models/", include(model_detail_patterns)),
    path("page-profiles/", include(page_profile_detail_patterns)),
    path("zones/", include(zone_detail_patterns)),
    path("problems/", include(problem_detail_patterns)),
    # ── Fase 5: Instances, Pages, Grading, Assignments ───────
    path("exams/<uuid:exam_pk>/", include(exam_instance_patterns + exam_review_patterns)),
    path("instances/", include(instance_patterns)),
    path(
        "instances/<uuid:instance_pk>/",
        include(instance_annotation_patterns + instance_review_patterns),
    ),
    path("assignment-rules/", include(assignment_rule_patterns)),
    path("my-tasks/", MyTasksView.as_view(), name="my-tasks"),
    # ── Fase 6: Ingestion & Recognition ──────────────────────
    path("recognition/", include("apps.ingestion.urls")),
    # ── Fase 7: Annotations ─────────────────────────────────
    path("annotations/", include(annotation_detail_patterns)),
    # ── Fase 8: Reviews ─────────────────────────────────────
    path("review-requests/", include(review_request_patterns)),
    # ── Fase 9: Notifications ───────────────────────────────
    path("notifications/", include("apps.notifications.urls")),
    # ── Fase 10: Export, Search, Config ──────────────────────
    path("config/", OrgConfigView.as_view(), name="org-config"),
    path("search/", HierarchicalSearchView.as_view(), name="hierarchical-search"),
]
