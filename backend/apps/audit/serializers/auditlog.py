# backend/apps/audit/serializers.py
from rest_framework import serializers

from apps.audit.models.auditlog import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "event_type",
            "actor",
            "actor_email",
            "ip_address",
            "timestamp",
            "entity_type",
            "entity_id",
            "payload",
        ]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor else None
