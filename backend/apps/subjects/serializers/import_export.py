"""
Serializers for import and export.

References: RF-4
"""

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

# ── Permission serializers ──────────────


class SubjectImportResultSerializer(serializers.Serializer):
    """Report shape returned by the bulk-import endpoint (matches
    ``SubjectImportResult.to_dict``)."""

    created = serializers.IntegerField(read_only=True)
    total_errors = serializers.IntegerField(read_only=True)
    errors = serializers.ListField(child=serializers.DictField(), read_only=True)


class SubjectFileUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)


class SubjectRawDataSerializer(serializers.Serializer):
    data = serializers.ListField(child=serializers.DictField(), required=True)


class SubjectImportExportItemSerializer(serializers.Serializer):
    """Formato de un subject en import/export (compatible ida y vuelta)."""

    name = serializers.CharField()
    code = serializers.CharField()
    semester = serializers.CharField(required=False, allow_blank=True)
    coordinator_email = serializers.EmailField()
    groups = serializers.ListField(child=serializers.CharField(), required=False, default=[])
    teachers = serializers.ListField(child=serializers.EmailField(), required=False, default=[])
    students = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        required=False,
        default=[],
    )


IMPORT_EXPORT_EXAMPLE = OpenApiExample(
    "Ejemplo JSON",
    value=[
        {
            "name": "Matemáticas I",
            "code": "MAT1",
            "semester": "1er cuatrimestre",
            "coordinator_email": "coord@example.com",
            "groups": ["G1", "G2"],
            "teachers": ["prof@example.com"],
            "students": [{"email": "alumno@example.com", "group": "G1"}],
        }
    ],
    response_only=True,
)
