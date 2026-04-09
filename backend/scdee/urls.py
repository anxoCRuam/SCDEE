"""
URL configuration for scdee project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    # ── Health check (RNF-5) ─────────────────────────────────
    # Outside /api/v1/ because it's an infrastructure endpoint,
    # not a business resource. No authentication required.
    path("health/", include("apps.core.urls.health")),
    # ── API v1 ───────────────────────────────────────────────
    # All business endpoints live under this versioned prefix.
    # When v2 is needed, a new /api/v2/ include can coexist.
    path("api/v1/", include("apps.core.urls.api_v1")),
    # ── OpenAPI documentation (RNF-8) ────────────────────────
    # Generated automatically from code via drf-spectacular.
    # The schema endpoint returns the raw OpenAPI JSON spec.
    path(
        "api/schema/",
        SpectacularAPIView.as_view(),
        name="schema",
    ),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/docs/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]
