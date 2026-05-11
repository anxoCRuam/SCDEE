"""
URL patterns for authentication endpoints.

Mounted at: /api/v1/auth/

Endpoints:
    POST /api/v1/auth/login/    — Authenticate (RF-1.2)
    POST /api/v1/auth/refresh/  — Renew tokens (RF-1.3)
    POST /api/v1/auth/logout/   — Revoke tokens (RF-1.4)
"""

from django.urls import path

from apps.accounts.views.auth import LoginView, LogoutView, RefreshView

app_name = "auth"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
