"""Health check URL (RNF-5). Mounted at /health/ outside the API namespace."""

from django.urls import path

from apps.core.views.health import HealthCheckView

urlpatterns = [
    path("", HealthCheckView.as_view(), name="health-check"),
]
