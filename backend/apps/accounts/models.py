"""
Custom User model for SCDEE.

Uses AbstractBaseUser (instead of AbstractUser) because:
1. Our is_staff field has custom semantics: it means "organization manager",
   not "can access Django admin".
2. We need encrypted DNI (BinaryField) and NIA fields.
3. We don't need Django's built-in groups/permissions (our permissions
   are context-based via SubjectMembership, RF-5).
4. We need per-organization email uniqueness, not global uniqueness.

IMPORTANT: This model is defined in Fase 0 because Django's
AUTH_USER_MODEL must be set before the first `makemigrations`.
Changing it after migrations exist requires a full DB rebuild.

References: RF-2.2, RF-2.5, RF-16.3, RF-16.4
"""

import uuid

from django.contrib.auth.models import AbstractBaseUser
from django.db import models

from apps.accounts.managers import UserManager
from apps.accounts.sso_models import OidcConfig, SamlConfig  # noqa: F401
from apps.core.tenancy.managers import UnfilteredManager


class User(AbstractBaseUser):
    """A user account within the SCDEE system.

    Users belong to exactly one organization. The same person in two
    organizations is two separate User records (RF-2).

    Attributes:
        email: Login identifier. Unique per organization (not globally).
        first_name: User's given name.
        last_name: User's family name(s).
        organization: The organization this user belongs to.
        nia: Institutional student/staff identifier. Unique per org.
        encrypted_dni: AES-256-GCM encrypted national ID document.
        dni_nonce: Unique nonce used for the DNI encryption.
        is_active: False = soft-deleted (cannot login, data preserved).
        is_staff: True = organization manager (can manage users, courses, etc.).
        is_superadmin: True = system-level admin (can create organizations).
        email_notifications_enabled: User preference for email notifications.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    email = models.EmailField(max_length=254, unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        help_text="Null only for system superadmins without an org.",
    )

    # Institutional identifier (Número de Identificación del Alumno).
    nia = models.CharField(max_length=50, blank=True, default="")

    # DNI is stored encrypted with AES-256-GCM (RF-16.3).
    # The encryption key is in ENCRYPTION_MASTER_KEY env var.
    # These fields store raw bytes — decryption happens in the service layer.
    encrypted_dni = models.BinaryField(null=True, blank=True)
    dni_nonce = models.BinaryField(null=True, blank=True)

    # Status and role flags
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive users cannot authenticate (soft delete).",
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Organization manager: can manage users, courses, config.",
    )
    is_superadmin = models.BooleanField(
        default=False,
        help_text="System-level admin: can create/manage organizations.",
    )

    # Notification preferences (RF-2.11)
    email_notifications_enabled = models.BooleanField(default=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # AbstractBaseUser configuration
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()
    unfiltered = UnfilteredManager()  # Bypass tenant filter (auth backend, superadmin ops).

    class Meta:
        constraints = [
            # Email must be unique within an organization, not globally.
            # This allows the same person to have accounts in different orgs.
            models.UniqueConstraint(
                fields=["email", "organization"],
                name="unique_email_per_organization",
            ),
            # NIA must be unique within an organization (if provided).
            models.UniqueConstraint(
                fields=["nia", "organization"],
                name="unique_nia_per_organization",
                condition=~models.Q(nia=""),
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "is_active"],
                name="idx_user_org_active",
            ),
        ]
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.last_name}, {self.first_name} ({self.email})"

    @property
    def full_name(self) -> str:
        """Return the user's full name."""
        return f"{self.first_name} {self.last_name}"
