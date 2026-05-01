"""
Generic serializers shared across apps.

References: RNF-8
"""

from __future__ import annotations

from rest_framework import serializers


class BinaryFileResponseSerializer(serializers.Serializer):
    """Marker serializer for endpoints that return raw binary content.

    Exists only to silence drf-spectacular's *unable to guess serializer*
    warning on APIViews whose response is a downloadable file (PDF, CSV,
    JSON file). The actual response shape is described in each view via
    ``@extend_schema(responses=...)`` with explicit content-type entries
    (e.g. ``application/pdf``).
    """


class DetailMessageSerializer(serializers.Serializer):
    """Generic ``{"detail": "<message>"}`` payload."""

    detail = serializers.CharField(read_only=True)
