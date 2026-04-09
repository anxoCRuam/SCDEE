"""
API v1 URL configuration.

All business endpoints are included here, mounted at /api/v1/
in the root URL config. Each phase adds its own app URLs.

URL conventions:
    - Plural nouns for collections: /users/, /subjects/, /exams/
    - Nested resources where logical: /subjects/{id}/exams/
    - Actions as sub-paths: /exams/{id}/publish/
"""

from django.urls import path  # noqa: F401

urlpatterns: list = [
    # Fase 1: path("auth/", include("apps.accounts.urls")),
    # Fase 1: path("organizations/", include("apps.organizations.urls")),
    # Fase 1: path("users/", include("apps.accounts.urls.users")),
    # Fase 2: path("courses/", include("apps.courses.urls")),
    # Fase 3: path("subjects/", include("apps.subjects.urls")),
    # ... etc.
]
