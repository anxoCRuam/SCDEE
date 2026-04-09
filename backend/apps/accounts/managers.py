"""
Custom user manager for the SCDEE User model.

Required because we use AbstractBaseUser instead of AbstractUser.
AbstractBaseUser mandates a custom manager that implements
create_user() and create_superuser().

References: RF-2.2, RF-2.7
"""

from typing import Any

from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    """Manager for the custom User model.

    Handles email normalization and delegates password hashing
    to Django's built-in mechanisms (Argon2 by default, see settings).
    """

    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ):
        """Create and persist a regular user.

        Args:
            email: User's email address (will be normalized).
            password: Plain-text password (will be hashed).
            **extra_fields: Additional model fields.

        Returns:
            The created User instance.

        Raises:
            ValueError: If email is not provided.
        """
        if not email:
            raise ValueError("Email address is required.")

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ):
        """Create a system superadmin user.

        Superadmins have is_superadmin=True and bypass tenant filtering.
        They can create organizations and manage the entire system.

        Args:
            email: Superadmin's email address.
            password: Plain-text password.
            **extra_fields: Additional model fields.

        Returns:
            The created superadmin User instance.
        """
        extra_fields.setdefault("is_superadmin", True)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_active", True)

        if not extra_fields.get("is_superadmin"):
            raise ValueError("Superuser must have is_superadmin=True.")

        return self.create_user(email, password, **extra_fields)
