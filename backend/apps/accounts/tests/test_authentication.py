"""
Tests for the authentication plugin system (RF-1.1).

These tests verify:
1. The plugin registry loads the correct class from settings.
2. The singleton pattern works (same instance on repeated calls).
3. Misconfiguration is caught with clear error messages.
4. The abstract interface cannot be instantiated directly.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.accounts.authentication import (
    AuthenticationError,
    BaseAuthPlugin,
    TokenPair,
    TokenPayload,
    get_auth_plugin,
    reset_auth_plugin,
)


class _StubPlugin(BaseAuthPlugin):
    """Minimal concrete plugin for testing the registry."""

    def authenticate(self, email: str, password: str) -> TokenPair:
        return TokenPair(access_token="stub_access", refresh_token="stub_refresh")  # noqa: S106

    def refresh(self, refresh_token: str) -> TokenPair:
        return TokenPair(access_token="new_access", refresh_token="new_refresh")  # noqa: S106

    def revoke(self, refresh_token: str) -> None:
        pass

    def validate_access_token(self, token: str) -> TokenPayload:
        from uuid import uuid4

        return TokenPayload(
            user_id=uuid4(),
            organization_id=None,
            is_staff=False,
            is_superadmin=False,
            jti="test-jti",
            token_type="access",  # noqa: S106
        )


class TestPluginRegistry:
    """Verify the get_auth_plugin() registry behavior."""

    def setup_method(self) -> None:
        """Clear the singleton before each test."""
        reset_auth_plugin()

    def teardown_method(self) -> None:
        """Clear the singleton after each test."""
        reset_auth_plugin()

    @override_settings(AUTH_PLUGIN_CLASS="apps.accounts.tests.test_authentication._StubPlugin")
    def test_loads_configured_plugin(self) -> None:
        """Registry should instantiate the class from AUTH_PLUGIN_CLASS."""
        plugin = get_auth_plugin()
        assert isinstance(plugin, _StubPlugin)

    @override_settings(AUTH_PLUGIN_CLASS="apps.accounts.tests.test_authentication._StubPlugin")
    def test_singleton_returns_same_instance(self) -> None:
        """Repeated calls should return the same object."""
        plugin1 = get_auth_plugin()
        plugin2 = get_auth_plugin()
        assert plugin1 is plugin2

    def test_missing_setting_raises(self) -> None:
        """Missing AUTH_PLUGIN_CLASS must raise ImproperlyConfigured."""
        # Default Django settings don't have AUTH_PLUGIN_CLASS
        with override_settings():
            # Remove the attribute entirely if it exists
            from django.conf import settings

            if hasattr(settings, "AUTH_PLUGIN_CLASS"):
                delattr(settings, "AUTH_PLUGIN_CLASS")
            with pytest.raises(ImproperlyConfigured):
                get_auth_plugin()

    @override_settings(AUTH_PLUGIN_CLASS="nonexistent.module.FakePlugin")
    def test_invalid_path_raises(self) -> None:
        """Non-existent class path must raise an import error."""
        with pytest.raises(Exception):  # noqa: B017
            get_auth_plugin()

    @override_settings(AUTH_PLUGIN_CLASS="apps.accounts.models.users.User")
    def test_non_plugin_class_raises(self) -> None:
        """A class that isn't a BaseAuthPlugin subclass must be rejected."""
        with pytest.raises(TypeError, match="not a subclass"):
            get_auth_plugin()

    @override_settings(AUTH_PLUGIN_CLASS="apps.accounts.tests.test_authentication._StubPlugin")
    def test_reset_clears_singleton(self) -> None:
        """reset_auth_plugin() should force re-instantiation."""
        plugin1 = get_auth_plugin()
        reset_auth_plugin()
        plugin2 = get_auth_plugin()
        assert plugin1 is not plugin2


class TestBaseAuthPluginIsAbstract:
    """Verify that BaseAuthPlugin cannot be instantiated."""

    def test_cannot_instantiate_directly(self) -> None:
        """Abstract methods must prevent direct instantiation."""
        with pytest.raises(TypeError):
            BaseAuthPlugin()  # type: ignore[abstract]


class TestDataClasses:
    """Verify data class behavior for TokenPair and TokenPayload."""

    def test_token_pair_is_immutable(self) -> None:
        """TokenPair should be frozen (no accidental mutation)."""
        pair = TokenPair(access_token="a", refresh_token="r")  # noqa: S106
        with pytest.raises(AttributeError):
            pair.access_token = "modified"  # type: ignore[misc]  # noqa: S105

    def test_token_payload_is_immutable(self) -> None:
        """TokenPayload should be frozen."""
        from uuid import uuid4

        payload = TokenPayload(
            user_id=uuid4(),
            organization_id=uuid4(),
            is_staff=True,
            is_superadmin=False,
            jti="jti-123",
            token_type="access",  # noqa: S106
        )
        with pytest.raises(AttributeError):
            payload.is_staff = False  # type: ignore[misc]

    def test_authentication_error_has_code(self) -> None:
        """AuthenticationError must carry a symbolic error code."""
        err = AuthenticationError(code="INVALID_CREDENTIALS", detail="Bad password")
        assert err.code == "INVALID_CREDENTIALS"
        assert "Bad password" in str(err)
