"""
URL patterns for subjects, groups, members, and permissions.

Mounted at: /api/v1/subjects/ (and /api/v1/my-subjects/, /api/v1/memberships/)

Endpoints:
    POST   /api/v1/subjects/                              — Create subject (RF-4.1)
    GET    /api/v1/subjects/                              — List subjects (RF-4.8/4.9)
    GET    /api/v1/subjects/{id}/                         — Subject detail (RF-4.9)
    PATCH  /api/v1/subjects/{id}/                         — Update subject (RF-4.3)
    DELETE /api/v1/subjects/{id}/                         — Delete subject (RF-4.4)

    GET    /api/v1/subjects/{id}/groups/                  — List groups (RF-4.5)
    POST   /api/v1/subjects/{id}/groups/                  — Create group (RF-4.5)
    PATCH  /api/v1/subjects/{id}/groups/{gid}/            — Rename group (RF-4.5)
    DELETE /api/v1/subjects/{id}/groups/{gid}/            — Delete group (RF-4.5)

    GET    /api/v1/subjects/{id}/members/                 — List members (RF-4.6)
    POST   /api/v1/subjects/{id}/members/                 — Assign member (RF-4.6)
    DELETE /api/v1/subjects/{id}/members/{mid}/           — Remove member (RF-4.7)

    POST   /api/v1/subjects/import/                       — Import subjects (RF-4.2)
    GET    /api/v1/subjects/export/                       — Export subjects (RF-4.10)

Separate URL files handle:
    GET/PATCH /api/v1/memberships/{id}/permissions/       — Permissions (RF-5.3, RF-5.5)
    GET       /api/v1/my-subjects/                        — Own subjects (RF-4.8)
"""

from django.urls import path

from apps.subjects.views.import_export import SubjectExportView, SubjectImportView
from apps.subjects.views.subjects import SubjectViewSet
from apps.subjects.views.views import (
    GroupDetailView,
    GroupListCreateView,
    MemberDetailView,
    MemberListCreateView,
)

app_name = "subjects"

subject_list = SubjectViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)

subject_detail = SubjectViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

urlpatterns = [
    # Subject CRUD.
    path("", subject_list, name="subject-list"),
    path("import/", SubjectImportView.as_view(), name="subject-import"),
    path("export/", SubjectExportView.as_view(), name="subject-export"),
    path("<uuid:pk>/", subject_detail, name="subject-detail"),
    # Nested: groups.
    path(
        "<uuid:subject_pk>/groups/",
        GroupListCreateView.as_view(),
        name="group-list",
    ),
    path(
        "<uuid:subject_pk>/groups/<uuid:group_pk>/",
        GroupDetailView.as_view(),
        name="group-detail",
    ),
    # Nested: members.
    path(
        "<uuid:subject_pk>/members/",
        MemberListCreateView.as_view(),
        name="member-list",
    ),
    path(
        "<uuid:subject_pk>/members/<uuid:membership_pk>/",
        MemberDetailView.as_view(),
        name="member-detail",
    ),
]
