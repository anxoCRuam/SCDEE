from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Immutable audit log for business events.

    Records every data-impacting operation (create, update, delete,
    access to sensitive data) as append-only entries in PostgreSQL.
    Neither the application nor any user role can modify or delete
    audit records.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"
    verbose_name = "Audit Log"
