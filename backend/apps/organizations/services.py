"""
Organization config service with Redis caching.

get_org_config() creates config lazily and caches in Redis.
update_org_config() saves and invalidates cache.

References: RF-15.1, RF-15.2
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from apps.organizations.models import OrganizationConfig

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "org_config"
_CACHE_TTL = 300  # 5 minutes


def get_org_config(organization) -> OrganizationConfig:
    """Get or create organization config with Redis cache."""
    cache_key = f"{_CACHE_PREFIX}:{organization.pk}"

    # Try cache first.
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Get or create from DB.
    config, created = OrganizationConfig.objects.get_or_create(
        organization=organization,
    )

    if created:
        logger.info("Created default config for org %s.", organization.pk)

    cache.set(cache_key, config, timeout=_CACHE_TTL)
    return config


def update_org_config(organization, data: dict) -> dict:
    """Update config fields and invalidate cache. Returns changes dict."""
    config = get_org_config(organization)
    changes = {}

    updatable = [
        "default_ocr_engine",
        "recognition_confidence_threshold",
        "assembly_timeout_seconds",
        "max_audio_duration_minutes",
        "max_page_size_mb",
        "auto_delete_frequency_days",
        "delete_notice_days",
        "default_language",
        "temp_url_expiration_minutes",
        "role_permission_defaults",
        "grade_export_columns",
    ]

    for field in updatable:
        if field in data:
            old_value = getattr(config, field)
            new_value = data[field]
            if old_value != new_value:
                changes[field] = {"old": str(old_value), "new": str(new_value)}
                setattr(config, field, new_value)

    if changes:
        config.save()
        # Invalidate cache.
        cache_key = f"{_CACHE_PREFIX}:{organization.pk}"
        cache.delete(cache_key)
        logger.info("Config updated for org %s: %s", organization.pk, list(changes.keys()))

    return changes
