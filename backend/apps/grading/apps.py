from django.apps import AppConfig


class GradingConfig(AppConfig):
    """Grades, assignment rules, and correction workflow (RF-8, RF-11)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.grading"
    label = "grading"
    verbose_name = "Grading & Assignment"
