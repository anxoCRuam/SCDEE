from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Core application providing shared infrastructure.

    Contains base models, middleware, pagination, error handling,
    and the health check endpoint. No business logic lives here.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Core Infrastructure"
