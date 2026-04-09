from django.apps import AppConfig


class AnnotationsConfig(AppConfig):
    """Text, stylus, and audio annotations on exam instances (RF-10)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.annotations"
    label = "annotations"
    verbose_name = "Annotations"
