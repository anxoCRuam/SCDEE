from django.apps import AppConfig


class ExamsConfig(AppConfig):
    """Exam definitions, models, problems, rubrics, and recognition zones (RF-6)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.exams"
    label = "exams"
    verbose_name = "Exams & Models"
