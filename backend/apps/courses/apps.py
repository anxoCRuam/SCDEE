from django.apps import AppConfig


class CoursesConfig(AppConfig):
    """Academic course management and transitions (RF-3)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.courses"
    label = "courses"
    verbose_name = "Academic Courses"
