"""
URL patterns for user management endpoints.

Mounted at: /api/v1/users/

Manual URL wiring instead of DRF's DefaultRouter because:
1. We don't want DELETE (users are deactivated, not deleted).
2. We don't want PUT (only PATCH for partial updates).
3. We want explicit control over action URLs.

Endpoints:
    POST   /api/v1/users/                     — Create user (RF-2.2)
    GET    /api/v1/users/                     — List users (RF-2.12)
    GET    /api/v1/users/{id}/                — Retrieve user
    PATCH  /api/v1/users/{id}/                — Update user (RF-2.4)
    POST   /api/v1/users/{id}/reset-password/ — Reset password (RF-2.8)
    POST   /api/v1/users/import/              — Bulk import (RF-2.3)
    GET    /api/v1/users/export/              — Export users (RF-2.13)
"""

from django.urls import path

from apps.accounts.views.import_export import UserExportView, UserImportView
from apps.accounts.views.users import UserViewSet

app_name = "users"

# ViewSet actions wired to explicit URL patterns.
user_list = UserViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)

user_detail = UserViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)

user_reset_password = UserViewSet.as_view(
    {
        "post": "reset_password",
    }
)

urlpatterns = [
    path("", user_list, name="user-list"),
    path("import/", UserImportView.as_view(), name="user-import"),
    path("export/", UserExportView.as_view(), name="user-export"),
    path("<uuid:pk>/", user_detail, name="user-detail"),
    path("<uuid:pk>/reset-password/", user_reset_password, name="user-reset-password"),
]
