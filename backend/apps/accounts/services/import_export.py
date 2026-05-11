"""
User import/export business logic.

Import (RF-2.3):
    Processes a list of user records (from CSV or JSON). For each row:
    - If user doesn't exist in org → create with generated password.
    - If user exists but is inactive → reactivate and update data.
    - If user exists and is active → update data.
    Errors on individual rows don't abort the whole import; they're
    collected and returned in the report.

Export (RF-2.13):
    Generates a list of user dicts with decrypted DNIs, suitable for
    CSV or JSON serialization. Filtered by the same criteria as the
    list endpoint.

CSV format (fixed columns, RF-2.3):
    first_name,last_name,email,dni,nia

References: RF-2.3, RF-2.13
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from apps.accounts.services.encryption import encrypt_dni
from apps.accounts.services.password import generate_secure_password
from apps.accounts.services.user_service import get_decrypted_dni

logger = logging.getLogger(__name__)

User = get_user_model()

# Fixed CSV column names.
CSV_COLUMNS = ["first_name", "last_name", "email", "dni", "nia"]


@dataclass
class ImportResult:
    """Report of a bulk import operation.

    Tracks counts and per-row errors for the import response.

    Attributes:
        created: Number of new users created.
        reactivated: Number of inactive users reactivated.
        updated: Number of active users updated.
        errors: List of per-row error dicts with row number and details.
        passwords: Dict of email → raw_password for welcome emails.
    """

    created: int = 0
    reactivated: int = 0
    updated: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    passwords: dict[str, str] = field(default_factory=dict)

    @property
    def total_processed(self) -> int:
        return self.created + self.reactivated + self.updated

    @property
    def total_errors(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict:
        """Serialize for the API response (passwords excluded)."""
        return {
            "created": self.created,
            "reactivated": self.reactivated,
            "updated": self.updated,
            "total_processed": self.total_processed,
            "total_errors": self.total_errors,
            "errors": self.errors,
        }


def parse_csv_file(file_content: str) -> list[dict[str, str]]:
    """Parse CSV content into a list of row dicts.

    Expects the fixed column format: first_name,last_name,email,dni,nia.
    The first row must be a header. Column names are case-insensitive
    and stripped of whitespace.

    Args:
        file_content: Raw CSV string.

    Returns:
        List of dicts, one per data row.

    Raises:
        ValueError: If headers don't match the expected columns.
    """
    reader = csv.DictReader(io.StringIO(file_content))

    if reader.fieldnames is None:
        raise ValueError("CSV file is empty or has no header row.")

    # Normalize header names.
    normalized_headers = [h.strip().lower() for h in reader.fieldnames]

    # Verify required columns are present.
    missing = set(CSV_COLUMNS) - set(normalized_headers)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {', '.join(sorted(missing))}. "
            f"Expected: {', '.join(CSV_COLUMNS)}"
        )

    rows = []
    for row in reader:
        # Normalize keys to match CSV_COLUMNS.
        normalized_row = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items()}
        rows.append(normalized_row)

    return rows


def parse_json_file(file_content: str) -> list[dict[str, str]]:
    """Parse JSON content into a list of row dicts.

    Expects a JSON array of objects, each with the same fields
    as the CSV format.

    Args:
        file_content: Raw JSON string.

    Returns:
        List of dicts.

    Raises:
        ValueError: If JSON is invalid or not an array of objects.
    """
    try:
        data = json.loads(file_content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("JSON must be an array of user objects.")

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Row {i + 1}: expected an object, got {type(item).__name__}.")

    return data


def import_users(
    organization,
    rows: list[dict[str, str]],
) -> ImportResult:
    """Process a list of user records for bulk import.

    For each row:
    1. Validate required fields (email, first_name, last_name).
    2. Check if user exists in the organization by email.
    3. Create / reactivate / update accordingly.
    4. Generate password for new and reactivated users.

    Errors on individual rows are collected, not raised.
    This allows partial imports to succeed.

    Args:
        organization: The organization to import into.
        rows: List of user data dicts.

    Returns:
        ImportResult with counts and per-row errors.
    """
    result = ImportResult()

    for row_num, row in enumerate(rows, start=1):
        try:
            _process_import_row(organization, row, row_num, result)
        except Exception as exc:
            logger.warning("Import row %d failed unexpectedly: %s", row_num, exc)
            result.errors.append(
                {
                    "row": row_num,
                    "error": "UNEXPECTED_ERROR",
                    "detail": str(exc),
                }
            )

    return result


def _process_import_row(
    organization,
    row: dict[str, str],
    row_num: int,
    result: ImportResult,
) -> None:
    """Process a single import row.

    Separated from the loop for clarity and testability.
    """
    # Validate required fields.
    email = row.get("email", "").strip()
    first_name = row.get("first_name", "").strip()
    last_name = row.get("last_name", "").strip()
    dni = row.get("dni", "").strip()
    nia = row.get("nia", "").strip()

    validation_errors = []
    if not email:
        validation_errors.append("email is required")
    if not first_name:
        validation_errors.append("first_name is required")
    if not last_name:
        validation_errors.append("last_name is required")

    if validation_errors:
        result.errors.append(
            {
                "row": row_num,
                "error": "VALIDATION_ERROR",
                "detail": "; ".join(validation_errors),
            }
        )
        return

    # Check if user already exists in this organization.
    try:
        existing_user = User.objects.get(email=email, organization=organization)
    except User.DoesNotExist:
        existing_user = None

    if existing_user is None:
        # Create new user.
        _create_import_user(organization, email, first_name, last_name, dni, nia, row_num, result)
    elif not existing_user.is_active:
        # Reactivate inactive user.
        _reactivate_import_user(existing_user, first_name, last_name, dni, nia, row_num, result)
    else:
        # Update existing active user.
        _update_import_user(existing_user, first_name, last_name, dni, nia, row_num, result)


def _create_import_user(organization, email, first_name, last_name, dni, nia, row_num, result):
    """Create a new user during import."""
    raw_password = generate_secure_password()

    encrypted_dni = None
    dni_nonce = None
    if dni:
        encrypted_dni, dni_nonce = encrypt_dni(dni)

    try:
        User.objects.create_user(
            email=email,
            password=raw_password,
            first_name=first_name,
            last_name=last_name,
            organization=organization,
            nia=nia,
            encrypted_dni=encrypted_dni,
            dni_nonce=dni_nonce,
            is_active=True,
        )
        result.created += 1
        result.passwords[email] = raw_password
    except IntegrityError as exc:
        result.errors.append(
            {
                "row": row_num,
                "error": "INTEGRITY_ERROR",
                "detail": str(exc),
                "email": email,
            }
        )


def _reactivate_import_user(user, first_name, last_name, dni, nia, row_num, result):
    """Reactivate an inactive user and update their data."""
    raw_password = generate_secure_password()

    user.first_name = first_name
    user.last_name = last_name
    user.nia = nia
    user.is_active = True
    user.set_password(raw_password)

    if dni:
        encrypted_dni, dni_nonce = encrypt_dni(dni)
        user.encrypted_dni = encrypted_dni
        user.dni_nonce = dni_nonce

    try:
        user.save()
        result.reactivated += 1
        result.passwords[user.email] = raw_password
    except IntegrityError as exc:
        result.errors.append(
            {
                "row": row_num,
                "error": "INTEGRITY_ERROR",
                "detail": str(exc),
                "email": user.email,
            }
        )


def _update_import_user(user, first_name, last_name, dni, nia, row_num, result):
    """Update data for an existing active user."""
    user.first_name = first_name
    user.last_name = last_name
    user.nia = nia

    if dni:
        encrypted_dni, dni_nonce = encrypt_dni(dni)
        user.encrypted_dni = encrypted_dni
        user.dni_nonce = dni_nonce

    try:
        user.save()
        result.updated += 1
    except IntegrityError as exc:
        result.errors.append(
            {
                "row": row_num,
                "error": "INTEGRITY_ERROR",
                "detail": str(exc),
                "email": user.email,
            }
        )


def export_users(
    queryset,
    file_format: str = "csv",
) -> str:
    """Export users to CSV or JSON string.

    Includes decrypted DNI as required by RF-2.13.

    Args:
        queryset: Filtered User queryset to export.
        file_format: "csv" or "json".

    Returns:
        String content of the exported file.
    """
    users_data = []
    for user in queryset.iterator():
        users_data.append(
            {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "dni": get_decrypted_dni(user),
                "nia": user.nia,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
            }
        )

    if file_format == "json":
        return json.dumps(users_data, ensure_ascii=False, indent=2)

    # CSV format (default).
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["first_name", "last_name", "email", "dni", "nia", "is_active", "is_staff"],
    )
    writer.writeheader()
    writer.writerows(users_data)
    return output.getvalue()
