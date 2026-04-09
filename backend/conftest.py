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
