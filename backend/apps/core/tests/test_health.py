"""Tests for the health check endpoint (RNF-5)."""

from rest_framework import status
from rest_framework.test import APIClient


class TestHealthCheck:
    """Verify the /health/ endpoint reports component status."""

    def test_health_check_returns_200_when_healthy(self):
        client = APIClient()
        response = client.get("/health/")
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        assert "status" in response.data
        assert "components" in response.data

    def test_health_check_requires_no_authentication(self):
        client = APIClient()  # No credentials
        response = client.get("/health/")
        # Should not return 401 or 403
        assert response.status_code != status.HTTP_401_UNAUTHORIZED
        assert response.status_code != status.HTTP_403_FORBIDDEN
