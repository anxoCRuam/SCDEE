"""
Root-level pytest configuration and shared fixtures.

Fixtures defined here are available to ALL test modules without import.
App-specific fixtures should live in each app's conftest.py.
"""

import pytest


@pytest.fixture(autouse=True)
def _enable_db_access_for_all_tests(db: None) -> None:
    """Grant database access to every test by default.

    Django tests need explicit DB access. Rather than marking every test
    with @pytest.mark.django_db, this autouse fixture enables it globally.
    For tests that genuinely don't need the DB, the overhead is negligible
    since pytest-django uses transactions that are rolled back.
    """


@pytest.fixture(autouse=True)
def _reset_runtime_caches() -> None:
    """Clear Redis-backed runtime state between tests.

    DRF throttles, the JWT blacklist and the OrganizationConfig cache
    all live in Django's default cache (Redis). Without this reset, a
    test that hits the login endpoint 10 times pushes every subsequent
    test past the LoginRateThrottle (10/min) and causes a wave of 429s
    that look like unrelated failures. Tests that specifically need to
    exercise these caches can populate them in their own setUp.
    """
    from django.core.cache import cache

    cache.clear()
