"""
Notification serializers, views, and URLs (RF-13.3, RF-13.4).
All in one file since the API surface is small.
"""

from rest_framework import serializers


class NotificationResponseSerializer(serializers.Serializer):
    """Output for notification list."""

    id = serializers.UUIDField(read_only=True)
    notification_type = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    metadata = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class MarkReadSerializer(serializers.Serializer):
    """Input for marking notifications as read."""

    notification_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        help_text="Specific IDs to mark. Empty = mark all.",
    )


class MarkReadResponseSerializer(serializers.Serializer):
    """Output for the mark-as-read endpoint."""

    marked_read = serializers.IntegerField(read_only=True)


class UnreadCountResponseSerializer(serializers.Serializer):
    """Output for the unread-count endpoint."""

    unread_count = serializers.IntegerField(read_only=True)
