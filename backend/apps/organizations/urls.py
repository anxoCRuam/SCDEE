"""
URL patterns for organization endpoints.

Mounted at: /api/v1/organizations/

Endpoints:
    POST /api/v1/organizations/ — Create organization (RF-2.1, superadmin only)
"""

from django.urls import path

from apps.organizations.views.organization import OrganizationCreateView

app_name = "organizations"

urlpatterns = [
    path("", OrganizationCreateView.as_view(), name="organization-create"),
]
