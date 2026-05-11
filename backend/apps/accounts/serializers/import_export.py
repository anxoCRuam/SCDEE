"""
Serializers for import / export users
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

USER_CSV_EXAMPLE = OpenApiExample(
    name="CSV example",
    summary="CSV file upload",
    description="A CSV file with header and two user rows.",
    value={
        "file": "first_name,last_name,email,dni,nia\n"
        "Ana,García,ana@uam.es,12345678X,NIA001\n"
        "Pedro,López,pedro@uam.es,,NIA002"
    },
    request_only=True,
)

USER_JSON_EXAMPLE = OpenApiExample(
    name="JSON example",
    summary="JSON file or raw body",
    description="A JSON array of user objects.",
    value=[
        {
            "first_name": "Ana",
            "last_name": "García",
            "email": "ana@uam.es",
            "dni": "12345678X",
            "nia": "NIA001",
        },
        {
            "first_name": "Pedro",
            "last_name": "López",
            "email": "pedro@uam.es",
            "dni": "",
            "nia": "NIA002",
        },
    ],
    request_only=True,
)


class UserImportRequestSerializer(serializers.Serializer):
    """Multipart upload — CSV or JSON file with user rows (RF-2.3)."""

    file = serializers.FileField(required=True)


class UserImportResultSerializer(serializers.Serializer):
    """Report shape returned by the bulk-import endpoint (matches
    ``ImportResult.to_dict``)."""

    created = serializers.IntegerField(read_only=True)
    reactivated = serializers.IntegerField(read_only=True)
    updated = serializers.IntegerField(read_only=True)
    total_processed = serializers.IntegerField(read_only=True)
    total_errors = serializers.IntegerField(read_only=True)
    errors = serializers.ListField(child=serializers.DictField(), read_only=True)
