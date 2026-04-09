from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    """Exam review windows, student requests, and re-grading (RF-12)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reviews"
    label = "reviews"
    verbose_name = "Exam Reviews"
