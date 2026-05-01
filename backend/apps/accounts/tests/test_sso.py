"""
Tests for Phases 11-13: maintenance, security, and deployment.

Covers:
- Auto-deletion task logic (RF-15.3)
- Deletion notice check (RF-15.4)
- Watermark service (RF-16.6)
- Anti-cache middleware (RF-16.7)
- Rate limiting configuration (RNF-6)
- SSO placeholder models (RF-1.5)
"""

import uuid

import pytest
from django.test import TestCase, override_settings

from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── SSO models (RF-1.5) ─────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestSSOModels(TestCase):
    def test_saml_config_creation(self):
        from apps.accounts.sso_models import SamlConfig

        org = Organization.objects.create(name="SSO Org", subdomain=f"sso-{uuid.uuid4().hex[:8]}")

        config = SamlConfig.objects.create(
            organization=org,
            entity_id="https://idp.example.com",
            sso_url="https://idp.example.com/sso",
            is_active=False,
        )

        assert config.pk is not None
        assert config.is_active is False

    def test_oidc_config_creation(self):
        from apps.accounts.sso_models import OidcConfig

        org = Organization.objects.create(
            name="OIDC Org", subdomain=f"oidc-{uuid.uuid4().hex[:8]}"
        )

        config = OidcConfig.objects.create(
            organization=org,
            client_id="my-client",
            discovery_url="https://idp.example.com/.well-known/openid-configuration",
            is_active=False,
        )

        assert config.pk is not None
        assert config.scopes == "openid email profile"
