"""
Immutable audit log model.

This table records every business event with data impact. It is
designed as append-only:

Application layer:
    - save() raises on updates (only INSERT allowed).
    - delete() always raises.
    - No update/delete methods on the manager.

Database layer:
    - A PostgreSQL trigger (added in migration 0002) prevents
      UPDATE and DELETE at the SQL level, protecting against
      any ORM bypass or raw SQL attempt.

Retention:
    Audit records are NEVER deleted, even during course transitions
    or automatic cleanup. They persist indefinitely (RF-16.1).

References: RF-16.1, RF-16.2, RNF-11
"""

import uuid

from django.conf import settings
from django.db import models


class AuditLogManager(models.Manager):
    """Custom manager that prevents bulk update/delete operations."""

    def update(self, **kwargs):
        raise PermissionError("Audit log entries cannot be updated.")

    def delete(self):
        raise PermissionError("Audit log entries cannot be deleted.")

    def bulk_update(self, objs, fields, **kwargs):
        raise PermissionError("Audit log entries cannot be updated.")

    def bulk_create(self, objs, **kwargs):
        # Permitimos solo si todos son nuevos (no hay updates), pero por seguridad
        # podríamos restringir. Lo dejamos para escritura controlada.
        return super().bulk_create(objs, **kwargs)


class AuditLog(models.Model):
    """Immutable audit log entry.

    Each record captures:
        - WHO performed the action (actor, IP address)
        - WHAT happened (event_type)
        - ON WHAT entity (entity_type, entity_id)
        - WHEN it happened (timestamp)
        - CONTEXT (organization, payload with old/new values)

    Attributes:
        organization: FK to the organization scope. Null for global
            events (superadmin operations).
        event_type: Symbolic identifier for the event category.
            Examples: USER_CREATED, GRADE_MODIFIED, LOGIN_SUCCESS.
        actor: The user who performed the action. Null for system events.
        ip_address: Client IP at the time of the event.
        timestamp: When the event occurred (set once, never modified).
        entity_type: Name of the affected model class.
        entity_id: Primary key of the affected entity (as string).
        payload: JSON with event-specific data (old/new values, etc.).
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",  # No reverse relation needed
        db_index=True,
    )
    event_type = models.CharField(max_length=100, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    entity_type = models.CharField(max_length=100, blank=True, default="")
    entity_id = models.CharField(max_length=255, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)

    objects = AuditLogManager()

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=["organization", "timestamp"],
                name="idx_audit_org_time",
            ),
            models.Index(
                fields=["event_type", "timestamp"],
                name="idx_audit_event_time",
            ),
            models.Index(
                fields=["actor", "timestamp"],
                name="idx_audit_actor_time",
            ),
            models.Index(
                fields=["entity_type", "entity_id"],
                name="idx_audit_entity",
            ),
            models.Index(
                fields=["ip_address"],
                name="idx_audit_ip",
            ),
        ]
        verbose_name = "Audit Log Entry"
        verbose_name_plural = "Audit Log Entries"

    def __str__(self) -> str:
        return f"[{self.timestamp}] {self.event_type} by {self.actor_id}"

    def save(self, *args, **kwargs) -> None:
        """Only allow INSERT operations. Updates are forbidden."""
        if not self._state.adding:
            raise PermissionError("Audit log entries are immutable and cannot be updated.")
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        """Prevent deletion of audit log entries."""
        raise PermissionError("Audit log entries are immutable and cannot be deleted.")
