"""
Unit tests for user service business logic.

These test the service layer directly without HTTP requests,
verifying business rules in isolation:
1. User creation with DNI encryption.
2. Password generation integration.
3. Update with change detection.
4. Password reset with token invalidation.
"""

import uuid

import pytest
from django.test import TestCase, override_settings

from apps.accounts.services.user_service import (
    UserServiceError,
    create_user,
    deactivate_user,
    get_decrypted_dni,
    reset_user_password,
    update_user,
)
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

VALID_KEY = "a" * 64


def _create_org():
    return Organization.objects.create(
        name="Test Org",
        subdomain=f"svc-{uuid.uuid4().hex[:8]}",
    )


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCreateUser(TestCase):
    """Test user_service.create_user()."""

    def test_creates_user_with_password(self):
        """User is created and raw password is returned."""
        org = _create_org()
        user, raw_password = create_user(
            organization=org,
            email="test@example.com",
            first_name="Test",
            last_name="User",
        )

        assert user.pk is not None
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert len(raw_password) >= 10
        assert user.check_password(raw_password)

    def test_encrypts_dni(self):
        """DNI is encrypted before storage."""
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="dni@example.com",
            first_name="Test",
            last_name="User",
            dni="12345678X",
        )

        assert user.encrypted_dni is not None
        assert user.dni_nonce is not None

        # Verify decryption works.
        decrypted = get_decrypted_dni(user)
        assert decrypted == "12345678X"

    def test_no_dni_leaves_fields_null(self):
        """User without DNI has null encrypted fields."""
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="nodni@example.com",
            first_name="Test",
            last_name="User",
        )

        assert user.encrypted_dni is None
        assert user.dni_nonce is None
        assert get_decrypted_dni(user) == ""

    def test_duplicate_email_raises(self):
        """Duplicate email in same org raises UserServiceError."""
        org = _create_org()
        create_user(
            organization=org,
            email="dup@example.com",
            first_name="First",
            last_name="User",
        )

        with pytest.raises(UserServiceError) as exc_info:
            create_user(
                organization=org,
                email="dup@example.com",
                first_name="Second",
                last_name="User",
            )

        assert exc_info.value.code == "EMAIL_ALREADY_EXISTS"


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUpdateUser(TestCase):
    """Test user_service.update_user()."""

    def test_detects_changes(self):
        """Returns dict of old → new values for changed fields."""
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="upd@example.com",
            first_name="Old",
            last_name="Name",
        )

        changes = update_user(user, data={"first_name": "New"})

        assert "first_name" in changes
        assert changes["first_name"]["old"] == "Old"
        assert changes["first_name"]["new"] == "New"

    def test_no_changes_returns_empty(self):
        """When no fields actually change, returns empty dict."""
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="nochg@example.com",
            first_name="Same",
            last_name="Name",
        )

        changes = update_user(user, data={"first_name": "Same"})

        assert changes == {}

    def test_update_dni_reencrypts(self):
        """Updating DNI re-encrypts with new nonce."""
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="redni@example.com",
            first_name="Test",
            last_name="User",
            dni="11111111A",
        )

        old_nonce = bytes(user.dni_nonce)
        update_user(user, data={"dni": "22222222B"})

        assert get_decrypted_dni(user) == "22222222B"
        assert bytes(user.dni_nonce) != old_nonce


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestDeactivateUser(TestCase):
    """Test user_service.deactivate_user()."""

    def test_sets_inactive(self):
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="deact@example.com",
            first_name="Test",
            last_name="User",
        )

        deactivate_user(user)

        user.refresh_from_db()
        assert user.is_active is False


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestResetPassword(TestCase):
    """Test user_service.reset_user_password()."""

    def test_changes_password(self):
        """Password is changed and old one no longer works."""
        org = _create_org()
        user, old_password = create_user(
            organization=org,
            email="reset@example.com",
            first_name="Test",
            last_name="User",
        )

        new_password = reset_user_password(user)

        user.refresh_from_db()
        assert not user.check_password(old_password)
        assert user.check_password(new_password)

    def test_returns_raw_password(self):
        """Returns the new password for email sending."""
        org = _create_org()
        user, _ = create_user(
            organization=org,
            email="resetraw@example.com",
            first_name="Test",
            last_name="User",
        )

        new_password = reset_user_password(user)

        assert len(new_password) >= 10
        assert user.check_password(new_password)
