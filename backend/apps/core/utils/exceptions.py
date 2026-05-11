"""
Custom exception handler for Django REST Framework.

Transforms all API errors into a uniform format with symbolic
error codes (never human-readable text), satisfying RNF-8:

Response format for single errors:
    {
        "error_code": "AUTHENTICATION_REQUIRED",
    }

Response format for validation errors (field-level):
    {
        "error_code": "VALIDATION_ERROR",
        "errors": {
            "email": ["FIELD_REQUIRED"],
            "password": ["MIN_LENGTH"]
        }
    }

The frontend maps these codes to localized user-facing messages.
"""

import logging
from typing import Any

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

# ── Mapping from DRF default codes to our symbolic codes ─────

_STATUS_CODE_MAP: dict[int, str] = {
    400: "VALIDATION_ERROR",
    401: "AUTHENTICATION_REQUIRED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    # Convertir excepciones Django a DRF primero
    if isinstance(exc, Http404):
        exc = APIException(detail="Not found", code="not_found")
        exc.status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, PermissionDenied):
        exc = APIException(detail="Permission denied", code="permission_denied")
        exc.status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, DjangoValidationError):
        exc = ValidationError(
            detail=exc.message_dict if hasattr(exc, "message_dict") else exc.messages
        )

    # Dejar que DRF construya la respuesta inicial
    response = drf_exception_handler(exc, context)

    if response is None:
        # Excepción no manejada → 500 genérico
        logger.exception("Unhandled exception in %s", context.get("view", "unknown"))
        return Response(
            {"error_code": "INTERNAL_ERROR"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Extraer el código de error semántico si está disponible
    if isinstance(exc, APIException):
        # exc.detail puede ser un string o un dict (ValidationError)
        if isinstance(exc.detail, str):
            possible_code = exc.detail
        else:
            possible_code = getattr(exc, "code", "") or ""
        # Si tenemos un código significativo y no es el genérico "error", lo usamos.
        # Lo normalizamos a mayúsculas y guiones bajos.
        if possible_code and possible_code.lower() not in ("error", "invalid"):
            error_code = possible_code.upper().replace(" ", "_")
        else:
            error_code = _STATUS_CODE_MAP.get(
                response.status_code, f"ERROR_{response.status_code}"
            )
    else:
        error_code = _STATUS_CODE_MAP.get(response.status_code, f"ERROR_{response.status_code}")

    # Formatear la respuesta según el tipo de error
    if isinstance(response.data, dict) and response.status_code == 400:
        # Error de validación con campos
        errors = _normalize_validation_errors(response.data)
        response.data = {
            "error_code": "VALIDATION_ERROR",
            "errors": errors,
        }
    elif isinstance(response.data, list):
        # Errores no asociados a un campo (lista de mensajes)
        response.data = {
            "error_code": error_code,
            "errors": {"non_field_errors": response.data},
        }
    else:
        # Error simple con código semántico
        response.data = {"error_code": error_code}

    return response


def _normalize_validation_errors(data: dict) -> dict[str, list[str]]:
    """Convert DRF's validation error detail into symbolic codes.

    DRF returns errors like:
        {"email": [ErrorDetail(string="This field is required.", code="required")]}

    We convert to:
        {"email": ["FIELD_REQUIRED"]}

    The error codes are uppercased versions of DRF's internal codes.
    """
    errors: dict[str, list[str]] = {}

    for field, field_errors in data.items():
        if isinstance(field_errors, list):
            codes = []
            for error in field_errors:
                if hasattr(error, "code"):
                    codes.append(str(error.code).upper())
                else:
                    codes.append("INVALID")
            errors[field] = codes
        elif isinstance(field_errors, dict):
            # Nested serializer errors — flatten with dot notation.
            nested = _normalize_validation_errors(field_errors)
            for nested_field, nested_codes in nested.items():
                errors[f"{field}.{nested_field}"] = nested_codes
        else:
            errors[field] = ["INVALID"]

    return errors
