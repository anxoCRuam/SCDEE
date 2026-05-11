"""
Serializers for organization config management, search engine and export.

References: RF-15
"""

from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

ORG_CONFIG_EXAMPLE = OpenApiExample(
    name="ConfigurationExample",
    summary="Example organization configuration",
    description="Example of the organization configuration object returned by GET and accepted by "
    "PATCH /config/.",
    value={
        "default_ocr_engine": "easyocr",
        "recognition_confidence_threshold": 0.7,
        "assembly_timeout_seconds": 600,
        "max_audio_duration_minutes": 60,
        "max_page_size_mb": 50,
        "auto_delete_frequency_days": 365,
        "delete_notice_days": 30,
        "default_language": "es",
        "temp_url_expiration_minutes": 15,
        "role_permission_defaults": {
            "COORDINATOR": {
                "can_create_exams": True,
                "can_create_rubric": True,
                "can_assign_correctors": True,
                "can_resolve_issues": True,
                "can_publish_grades": True,
                "can_manage_reviews": True,
                "can_view_all_instances": True,
                "can_export_grades": True,
                "can_manage_members": True,
                "can_delegate_perms": True,
                "can_manage_groups": True,
            },
            "TEACHER": {
                "can_create_exams": False,
                "can_create_rubric": True,
                "can_assign_correctors": False,
                "can_resolve_issues": False,
                "can_publish_grades": False,
                "can_manage_reviews": False,
                "can_view_all_instances": True,
                "can_export_grades": False,
                "can_manage_members": False,
                "can_delegate_perms": False,
                "can_manage_groups": False,
            },
            "STUDENT": {
                "can_create_exams": False,
                "can_create_rubric": False,
                "can_assign_correctors": False,
                "can_resolve_issues": False,
                "can_publish_grades": False,
                "can_manage_reviews": False,
                "can_view_all_instances": False,
                "can_export_grades": False,
                "can_manage_members": False,
                "can_delegate_perms": False,
                "can_manage_groups": False,
            },
        },
        "grade_export_columns": {
            "student__nia": "NIA",
            "student__last_name": "Student Last Name",
            "group": "Group",
            "total_score": "Final Grade",
        },
    },
)


@extend_schema_serializer(
    examples=[ORG_CONFIG_EXAMPLE],
)
class OrgConfigSerializer(serializers.Serializer):
    """Round-trip shape for ``GET`` and ``PATCH /config/`` (RF-15.1, RF-15.2).

    All fields are optional on input — PATCH only updates what is present —
    and all fields are returned on output.
    """

    default_ocr_engine = serializers.CharField(required=False)
    recognition_confidence_threshold = serializers.FloatField(required=False)
    assembly_timeout_seconds = serializers.IntegerField(required=False)
    max_audio_duration_minutes = serializers.IntegerField(required=False)
    max_page_size_mb = serializers.IntegerField(required=False)
    auto_delete_frequency_days = serializers.IntegerField(required=False)
    delete_notice_days = serializers.IntegerField(required=False)
    default_language = serializers.CharField(required=False)
    temp_url_expiration_minutes = serializers.IntegerField(required=False)
    role_permission_defaults = serializers.JSONField(
        required=False,
        help_text="JSON object with permissions by role. "
        "E.g: {'COORDINATOR': {'can_grade': true} }",
    )
    grade_export_columns = serializers.JSONField(
        required=False,
        help_text="Object with column names for grade export. E.g: "
        " {'nia': 'NIA', 'name': 'Student Name', 'group': 'Group', 'total_score': 'Final Grade' }",
    )
