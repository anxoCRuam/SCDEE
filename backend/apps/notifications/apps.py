from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """In-app notifications and email dispatch (RF-13)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    label = "notifications"
    verbose_name = "Notifications"
