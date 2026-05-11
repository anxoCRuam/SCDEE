"""
Generic serializers shared across apps.

Also register the BearerAuth security scheme so JWT-protected endpoints
appear with ``Authorization: Bearer <token>`` in the OpenAPI
document and drf-spectacular stops emitting "could not resolve
authenticator" warnings.

References: RNF-8
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
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


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Maps JWTAuthentication → OpenAPI HTTP Bearer security scheme."""

    target_class = "apps.accounts.backends.JWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "JWT access token. Obtain via `POST /api/v1/auth/login/`. "
                "Pass as `Authorization: Bearer <token>`. "
                "Access tokens expire after 15 minutes by default; "
                "use `POST /api/v1/auth/refresh/` to renew."
            ),
        }
