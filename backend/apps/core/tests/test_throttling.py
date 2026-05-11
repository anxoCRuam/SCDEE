"""
End-to-end tests for the rate-limit configuration (RNF-6, fix C.5).

The throttle classes in ``apps.core.security`` (``LoginRateThrottle``,
``StandardUserThrottle``, ``ManagerThrottle``) only protect the API
when they are wired into ``DEFAULT_THROTTLE_CLASSES`` and applied to
the relevant views. These tests blow past each rate limit and confirm
that DRF responds with 429 — proving the wiring is real, not just
declarative.

The autouse cache-clear fixture in conftest.py prevents bleed across
tests, so each test starts from a clean throttle state.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.organizations.models.organization import Organization

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestLoginRateThrottle(TestCase):
    """LoginView is annotated with throttle_classes=[LoginRateThrottle]
    (10/minute). The 11th login attempt within the window must be 429."""

    def test_eleventh_login_attempt_is_throttled(self):
        reset_auth_plugin()
        client = APIClient()

        # Wrong creds on purpose so login always fails fast.
        creds = {"email": "nobody@example.com", "password": "x"}

        statuses: list[int] = []
        for _ in range(11):
            r = client.post("/api/v1/auth/login/", creds, format="json")
            statuses.append(r.status_code)

        # First 10 are 401 (or whatever the auth plugin returns for bad creds),
        # the 11th must be 429.
        assert statuses[-1] == status.HTTP_429_TOO_MANY_REQUESTS, statuses
        assert all(s != status.HTTP_429_TOO_MANY_REQUESTS for s in statuses[:10])


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestStandardUserThrottle(TestCase):
    """A regular authenticated user is capped at 120 requests/minute on
    the global default throttle. Hitting 121 within the window yields a 429."""

    def test_121st_authenticated_request_is_throttled(self):
        reset_auth_plugin()
        user_model = get_user_model()

        org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
        user = user_model.objects.create_user(
            email=f"u-{uuid.uuid4().hex[:8]}@x.com",
            password="UserPass123!",  # noqa: S106
            first_name="U",
            last_name="X",
            organization=org,
        )

        login_client = APIClient()
        login_resp = login_client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "UserPass123!"},
            format="json",
        )
        token = login_resp.data["access_token"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # /api/v1/profile/ is a cheap authenticated read.
        last_status = status.HTTP_200_OK
        seen_429 = False
        # 130 is enough to surpass the 120/minute cap.
        for _ in range(130):
            r = client.get("/api/v1/profile/")
            last_status = r.status_code
            if last_status == status.HTTP_429_TOO_MANY_REQUESTS:
                seen_429 = True
                break

        assert seen_429, f"Did not get throttled after 130 requests; last={last_status}"
