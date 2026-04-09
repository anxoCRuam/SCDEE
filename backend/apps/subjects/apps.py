from django.apps import AppConfig


class SubjectsConfig(AppConfig):
    """Subjects, groups, memberships, and contextual permissions (RF-4, RF-5)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.subjects"
    label = "subjects"
    verbose_name = "Subjects & Groups"
