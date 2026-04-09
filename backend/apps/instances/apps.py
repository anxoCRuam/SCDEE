from django.apps import AppConfig


class InstancesConfig(AppConfig):
    """Exam instances, state machine, and lifecycle management (RF-7)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.instances"
    label = "instances"
    verbose_name = "Exam Instances"
