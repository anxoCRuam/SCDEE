from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """User accounts, authentication, and profile management.

    Uses a custom User model (AbstractBaseUser) with organization FK,
    encrypted DNI, NIA, and custom is_staff semantics (org manager).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "User Accounts"
