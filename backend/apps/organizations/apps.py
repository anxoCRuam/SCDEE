from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    """Organization model and per-org configuration.

    An Organization is the top-level tenant container. All data
    isolation in the system is scoped to an organization.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizations"
    label = "organizations"
    verbose_name = "Organizations"
