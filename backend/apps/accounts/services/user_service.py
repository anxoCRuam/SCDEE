"""
User management business logic service.

All user mutations flow through this module. Views and serializers
validate input shape; this service applies business rules:

- Encrypt DNI before storage (RF-16.3)
- Generate secure passwords for new users (RF-2.7)
- Enforce uniqueness constraints within organization scope
- Detect field changes for audit logging (RF-16.1)
- Enqueue welcome/reset emails via Celery (RF-13.6)
- Invalidate tokens on password reset (RF-2.8)

The service never accesses request objects directly — it receives
clean data from the view layer and returns results. This makes it
testable without HTTP infrastructure.


Declarative filters for user list endpoints.

Uses django-filter for clean, maintainable filter declarations.
The TenantManager already scopes queries to the manager's org,
so these filters only add within-organization filtering.

Supported filters (RF-2.12):
    - search: partial text match on first_name, last_name, or email
    - is_active: boolean (true/false)
    - is_staff: boolean (true/false)
    - nia: exact match

Example:
    GET /api/v1/users/?search=garcia&is_active=true&page_size=10


References: RF-2.2, RF-2.4, RF-2.5, RF-2.7, RF-2.8, RF-2.12
"""

from __future__ import annotations

import logging
from typing import Any

import django_filters
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import Q, QuerySet

from apps.accounts.services.encryption import decrypt_dni, encrypt_dni
from apps.accounts.services.password import generate_secure_password

logger = logging.getLogger(__name__)

User = get_user_model()


class UserServiceError(Exception):
    """Raised when a user operation violates business rules.

    Attributes:
        code: Symbolic error code for the API response.
        field: Optional field name for validation errors.
    """

    def __init__(self, code: str, detail: str = "", field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(detail or code)


def create_user(
    *,
    organization,
    email: str,
    first_name: str,
    last_name: str,
    dni: str = "",
    nia: str = "",
    is_staff: bool = False,
) -> tuple[Any, str]:
    """Create a new user within an organization.

    This is the single entry point for user creation. It handles:
    1. DNI encryption (if provided)
    2. Secure password generation
    3. User record creation
    4. Returns the raw password for email sending

    The caller (view) is responsible for:
    - Audit logging
    - Enqueuing the welcome email with the raw password

    Args:
        organization: Organization instance the user belongs to.
        email: User's email address.
        first_name: User's given name.
        last_name: User's family name(s).
        dni: National ID document (will be encrypted). Empty string if not provided.
        nia: Institutional identifier. Empty string if not provided.
        is_staff: Whether the user is an organization manager.

    Returns:
        Tuple of (created_user, raw_password).

    Raises:
        UserServiceError: If email/NIA/DNI uniqueness is violated.
    """
    raw_password = generate_secure_password()

    # Encrypt DNI if provided.
    encrypted_dni = None
    dni_nonce = None
    if dni:
        encrypted_dni, dni_nonce = encrypt_dni(dni)

    try:
        user = User.objects.create_user(
            email=email,
            password=raw_password,
            first_name=first_name,
            last_name=last_name,
            organization=organization,
            nia=nia,
            encrypted_dni=encrypted_dni,
            dni_nonce=dni_nonce,
            is_staff=is_staff,
            is_active=True,
        )
    except IntegrityError as exc:
        error_msg = str(exc).lower()
        if "unique_email_per_organization" in error_msg or "email" in error_msg:
            raise UserServiceError(
                code="EMAIL_ALREADY_EXISTS",
                detail=f"A user with email {email} already exists in this organization.",
                field="email",
            ) from exc
        if "unique_nia_per_organization" in error_msg:
            raise UserServiceError(
                code="NIA_ALREADY_EXISTS",
                detail=f"A user with NIA {nia} already exists in this organization.",
                field="nia",
            ) from exc
        raise UserServiceError(
            code="USER_CREATION_FAILED",
            detail=str(exc),
        ) from exc

    return user, raw_password


def update_user(
    user,
    *,
    data: dict,
) -> dict:
    """Update a user's mutable fields.

    Detects which fields actually changed and returns the diff
    for audit logging. Only provided fields are updated (PATCH semantics).

    Updatable fields: first_name, last_name, email, nia, is_staff, is_active.
    DNI is updatable but requires re-encryption.

    Args:
        user: The User instance to update.
        data: Dict of field names → new values. Only present keys are updated.

    Returns:
        Dict with old and new values for changed fields:
        {"field_name": {"old": "...", "new": "..."}, ...}
        Empty dict if nothing changed.

    Raises:
        UserServiceError: If uniqueness constraints are violated.
    """
    changes: dict[str, dict[str, Any]] = {}

    # Simple text fields.
    for field in ("first_name", "last_name", "email", "nia"):
        if field in data:
            old_value = getattr(user, field)
            new_value = data[field]
            if old_value != new_value:
                changes[field] = {"old": old_value, "new": new_value}
                setattr(user, field, new_value)

    # Boolean fields.
    for field in ("is_staff", "is_active"):
        if field in data:
            old_value = getattr(user, field)
            new_value = data[field]
            if old_value != new_value:
                changes[field] = {"old": old_value, "new": new_value}
                setattr(user, field, new_value)

    # DNI requires re-encryption.
    if "dni" in data:
        new_dni = data["dni"]
        if new_dni:
            encrypted_dni, dni_nonce = encrypt_dni(new_dni)
            user.encrypted_dni = encrypted_dni
            user.dni_nonce = dni_nonce
            # Don't log the actual DNI value in changes (sensitive).
            changes["dni"] = {"old": "[encrypted]", "new": "[encrypted]"}
        else:
            # Clear DNI.
            if user.encrypted_dni is not None:
                user.encrypted_dni = None
                user.dni_nonce = None
                changes["dni"] = {"old": "[encrypted]", "new": ""}

    if not changes:
        return changes

    try:
        user.save()
    except IntegrityError as exc:
        error_msg = str(exc).lower()
        if "unique_email_per_organization" in error_msg or "email" in error_msg:
            raise UserServiceError(
                code="EMAIL_ALREADY_EXISTS",
                field="email",
            ) from exc
        if "unique_nia_per_organization" in error_msg:
            raise UserServiceError(
                code="NIA_ALREADY_EXISTS",
                field="nia",
            ) from exc
        raise

    return changes


def deactivate_user(user) -> None:
    """Soft-delete a user by setting is_active=False.

    The user's data and historical relationships are preserved.
    They can no longer authenticate or access any resource.

    Args:
        user: The User instance to deactivate.
    """
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])


def reset_user_password(user) -> str:
    """Generate a new password and invalidate all existing tokens.

    Called when a manager forces a password reset (RF-2.8).
    The new raw password is returned so it can be sent by email.

    Args:
        user: The User instance whose password to reset.

    Returns:
        The new raw password (for email sending).
    """
    from datetime import timedelta

    from apps.accounts.blacklist import blacklist_all_user_tokens

    raw_password = generate_secure_password()
    user.set_password(raw_password)
    user.save(update_fields=["password", "updated_at"])

    # Invalidate all existing tokens for this user.
    # Use the maximum token lifetime (refresh = 7 days by default)
    # to ensure all tokens are covered.
    from django.conf import settings

    max_lifetime_days = getattr(settings, "JWT_REFRESH_TOKEN_LIFETIME_DAYS", 7)
    blacklist_all_user_tokens(str(user.pk), timedelta(days=max_lifetime_days))

    return raw_password


def get_decrypted_dni(user) -> str:
    """Decrypt and return a user's DNI.

    Should only be called when the requesting user has permission
    to see the DNI (the user themselves, or a manager).

    Args:
        user: The User instance.

    Returns:
        Decrypted DNI string, or empty string if not set.
    """
    if user.encrypted_dni and user.dni_nonce:
        return decrypt_dni(bytes(user.encrypted_dni), bytes(user.dni_nonce))
    return ""


class UserFilter(django_filters.FilterSet):
    """Filter set for user listing.

    The `search` filter performs a case-insensitive partial match
    across first_name, last_name, and email. This covers the
    requirement for "búsqueda parcial por texto en nombre y apellidos"
    and extends it to email for convenience.
    """

    search = django_filters.CharFilter(
        method="filter_search",
        help_text="Partial text search on first name, last name, or email.",
    )
    is_active = django_filters.BooleanFilter(
        field_name="is_active",
        help_text="Filter by active status (true/false).",
    )
    is_staff = django_filters.BooleanFilter(
        field_name="is_staff",
        help_text="Filter by manager role (true/false).",
    )
    nia = django_filters.CharFilter(
        field_name="nia",
        lookup_expr="iexact",
        help_text="Exact match on institutional identifier (case-insensitive).",
    )
    email = django_filters.CharFilter(
        field_name="email",
        lookup_expr="icontains",
        help_text="Partial match on email address.",
    )

    class Meta:
        model = User
        fields = ["is_active", "is_staff", "nia", "email"]

    def filter_search(self, queryset: QuerySet, name: str, value: str) -> QuerySet:
        """Search across multiple text fields simultaneously.

        Uses Q objects with OR to match any of the fields.
        icontains provides case-insensitive partial matching.
        """
        if not value:
            return queryset

        return queryset.filter(
            Q(first_name__icontains=value)
            | Q(last_name__icontains=value)
            | Q(email__icontains=value)
        )
