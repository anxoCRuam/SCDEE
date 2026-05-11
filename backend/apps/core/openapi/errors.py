"""
Shared OpenAPI schema components for SCDEE.

This module is the single source of truth for how the API documents
its error responses. Two principles:

1. The wire format documented here matches what
   ``apps.core.exceptions.api_exception_handler`` actually returns
   (``{"error_code": "..."}``), not the DRF default ``{"detail": "..."}``.

2. Each error code is exposed as an enum value, so the frontend gets a
   precise, machine-readable list of what to expect — not just
   ``string``.

Views compose error responses via :func:`error_response`, which returns
an :class:`OpenApiResponse` ready to be placed in
``responses={...}``::

    @extend_schema(
        responses={
            200: GradeResponseSerializer,
            409: error_response([ErrorCode.CONFLICT], status_code=409),
        },
    )

Generic error responses (400/401/403/404/429) are injected globally by
the postprocessing hook, so views do not need to repeat them.

References: RNF-8.
"""

from __future__ import annotations

from enum import StrEnum

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

# ════════════════════════════════════════════════════════════════════
# Error code catalogue
# ════════════════════════════════════════════════════════════════════
#
# Every ``error_code`` value the API can return appears here. The list
# is split between *generic* codes (one per HTTP status, returned by
# the global exception handler) and *domain-specific* codes (raised by
# service-layer ``*ServiceError`` exceptions or by views directly).
#
# Adding a new code: append the symbol below, raise it from the service
# layer, and reference it in the relevant ``@extend_schema(responses=)``
# via ``error_response(...)``.


class ErrorCode(StrEnum):
    """Symbolic error codes returned by the API.

    Inheriting from :class:`StrEnum` means the value *is* the string,
    so ``ErrorCode.NOT_FOUND == "NOT_FOUND"`` and JSON serialisation is
    transparent.
    """

    # ── Generic (mapped 1:1 from HTTP status) ────────────────────
    VALIDATION_ERROR = "VALIDATION_ERROR"  # 400
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"  # 401
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 403
    NOT_FOUND = "NOT_FOUND"  # 404
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"  # 405
    CONFLICT = "CONFLICT"  # 409 (generic)
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"  # 429
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 500

    # ── Authentication / tokens (RF-1.x) ─────────────────────────
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_TOKEN = "INVALID_TOKEN"  # noqa: S105
    INVALID_TOKEN_TYPE = "INVALID_TOKEN_TYPE"  # noqa: S105
    TOKEN_EXPIRED = "TOKEN_EXPIRED"  # noqa: S105
    TOKEN_REVOKED = "TOKEN_REVOKED"  # noqa: S105
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_INACTIVE = "USER_INACTIVE"
    ORG_INACTIVE = "ORG_INACTIVE"

    # ── Users (RF-2.x) ───────────────────────────────────────────
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    CANNOT_DEACTIVATE_SELF = "CANNOT_DEACTIVATE_SELF"
    CANNOT_DEMOTE_SELF = "CANNOT_DEMOTE_SELF"

    # ── Organizations / courses (RF-2.1, RF-3.x) ────────────────
    SUBDOMAIN_ALREADY_EXISTS = "SUBDOMAIN_ALREADY_EXISTS"
    ORGANIZATION_CREATION_FAILED = "ORGANIZATION_CREATION_FAILED"
    COURSE_ARCHIVED = "COURSE_ARCHIVED"

    # ── Subjects / groups (RF-4.x) ───────────────────────────────
    SUBJECT_CREATION_FAILED = "SUBJECT_CREATION_FAILED"
    CODE_ALREADY_EXISTS = "CODE_ALREADY_EXISTS"
    GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
    GROUP_LABEL_EXISTS = "GROUP_LABEL_EXISTS"

    # ── Exams / models / zones (RF-6.x) ──────────────────────────
    HAS_INSTANCES = "HAS_INSTANCES"
    SOURCE_MODEL_NOT_FOUND = "SOURCE_MODEL_NOT_FOUND"
    INVALID_ZONE_TYPE = "INVALID_ZONE_TYPE"
    INVALID_DIMENSIONS = "INVALID_DIMENSIONS"
    INVALID_COORDINATES = "INVALID_COORDINATES"
    ZONE_EXCEEDS_PAGE_WIDTH = "ZONE_EXCEEDS_PAGE_WIDTH"
    ZONE_EXCEEDS_PAGE_HEIGHT = "ZONE_EXCEEDS_PAGE_HEIGHT"
    NO_INSTRUMENTED_PDF = "NO_INSTRUMENTED_PDF"
    INSTRUMENTED_PDF_INVALID = "INSTRUMENTED_PDF_INVALID"

    # ── Instances / pages / state machine (RF-7.x) ───────────────
    PAGE_NOT_ORPHAN = "PAGE_NOT_ORPHAN"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PROBLEM_NOT_FOUND = "PROBLEM_NOT_FOUND"

    # ── Annotations (RF-10.x) ────────────────────────────────────
    INVALID_TYPE = "INVALID_TYPE"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    NOT_STYLUS = "NOT_STYLUS"

    # ── Reviews (RF-12.x) ────────────────────────────────────────
    NO_REVIEW_WINDOW = "NO_REVIEW_WINDOW"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"

    # ── File / format (multiple RFs) ─────────────────────────────
    NO_FILE_PROVIDED = "NO_FILE_PROVIDED"
    EMPTY_FILE = "EMPTY_FILE"
    INVALID_FILE_FORMAT = "INVALID_FILE_FORMAT"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_JSON = "INVALID_JSON"
    INVALID_FILTER = "INVALID_FILTER"
    NO_DATA_PROVIDED = "NO_DATA_PROVIDED"
    EMPTY_OR_INVALID_DATA = "EMPTY_OR_INVALID_DATA"


# ════════════════════════════════════════════════════════════════════
# Error response serializers
# ════════════════════════════════════════════════════════════════════
#
# These are the two shapes emitted by ``api_exception_handler``. They
# are real DRF serializers so drf-spectacular registers them as named
# components in the OpenAPI document, instead of inlining anonymous
# objects in every operation.


class ErrorResponseSerializer(serializers.Serializer):
    """Standard error response: one symbolic code, no field details.

    Emitted for 401, 403, 404, 405, 409, 429, 500 and most domain
    errors. The frontend maps ``error_code`` to a localised message;
    no human-readable text is sent on the wire.
    """

    error_code = serializers.ChoiceField(
        choices=[(code.value, code.value) for code in ErrorCode],
        help_text=(
            "Symbolic, locale-independent error identifier. "
            "Map to a localised message on the frontend."
        ),
    )


class ValidationErrorResponseSerializer(serializers.Serializer):
    """Validation error response: one global code plus per-field codes.

    Emitted exclusively for HTTP 400. Each entry in ``errors`` is a
    list of symbolic codes (``FIELD_REQUIRED``, ``MIN_LENGTH``, ...)
    so the frontend can attach the right message to the right field.
    """

    error_code = serializers.ChoiceField(
        choices=[(ErrorCode.VALIDATION_ERROR.value, ErrorCode.VALIDATION_ERROR.value)],
        help_text="Always equals ``VALIDATION_ERROR``.",
    )
    errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text=(
            "Map of field name → list of symbolic error codes. "
            "Nested fields are flattened with dot notation "
            "(e.g. ``user.email``). The pseudo-field "
            "``non_field_errors`` carries cross-field errors."
        ),
    )


# ════════════════════════════════════════════════════════════════════
# Helpers used inside @extend_schema(responses=...)
# ════════════════════════════════════════════════════════════════════
#
# These build :class:`OpenApiResponse` objects with a *narrowed*
# error_code enum, so the OpenAPI document tells the frontend exactly
# which codes a given endpoint can return.


_DEFAULT_DESCRIPTIONS: dict[int, str] = {
    400: "Invalid input — see ``errors`` for field-level details.",
    401: "Authentication credentials missing, expired or revoked.",
    403: "Authenticated, but not authorised for this resource.",
    404: "Resource not found in the caller's organisation.",
    405: "HTTP method not allowed on this resource.",
    409: "Conflict with the current state of the resource.",
    422: "Domain rule violation. See ``error_code`` for the reason.",
    429: "Rate limit exceeded for this endpoint.",
    500: "Unexpected server error. The incident has been logged.",
    503: "One or more required infrastructure components are down.",
}


def error_response(
    codes: list[ErrorCode] | list[str],
    *,
    status_code: int,
    description: str | None = None,
) -> OpenApiResponse:
    """Build an :class:`OpenApiResponse` documenting an error response.

    The response schema is a narrowed inline serializer where
    ``error_code`` enumerates only the codes actually emitted by this
    endpoint. The Swagger UI then offers one example per code in a
    dropdown, so the frontend developer can immediately see what each
    failure looks like on the wire.

    Identical ``(codes, status_code)`` invocations from different views
    are de-duplicated: the underlying inline serializer is cached, so
    drf-spectacular sees a single component and registers it once. This
    avoids "two components with identical names" warnings.

    Use as a value inside ``responses={...}``::

        @extend_schema(
            responses={
                200: GradeResponseSerializer,
                409: error_response(
                    [ErrorCode.CONFLICT, ErrorCode.COURSE_ARCHIVED],
                    status_code=409,
                ),
            },
        )

    Args:
        codes: Symbolic error codes the endpoint can emit at this
            status. Use :class:`ErrorCode` members or raw strings if
            the code is not yet in the catalogue.
        status_code: HTTP status this error_response documents. Used
            for the default description and to keep schema component
            names unique.
        description: Optional override for the response description.

    Returns:
        :class:`OpenApiResponse` with the narrowed schema and one
        example per code.
    """
    code_values = [c.value if isinstance(c, ErrorCode) else c for c in codes]
    sorted_codes = tuple(sorted(code_values))

    narrow_serializer = _get_or_build_error_serializer(status_code, sorted_codes)

    examples = [
        OpenApiExample(
            name=code,
            summary=code,
            value={"error_code": code},
            response_only=True,
            status_codes=[str(status_code)],
        )
        for code in code_values
    ]

    return OpenApiResponse(
        response=narrow_serializer,
        description=description or _DEFAULT_DESCRIPTIONS.get(status_code, "Error response."),
        examples=examples,
    )


# Cache of inline serializers keyed by (status_code, sorted_codes_tuple).
# We cache the *class* so drf-spectacular sees one component per
# distinct (status, codes) signature, regardless of how many views ask
# for it.
_ERROR_SERIALIZER_CACHE: dict[tuple[int, tuple[str, ...]], type[serializers.Serializer]] = {}


def _get_or_build_error_serializer(
    status_code: int,
    sorted_codes: tuple[str, ...],
) -> type[serializers.Serializer]:
    """Return a cached inline serializer for the given (status, codes).

    Creating a fresh ``inline_serializer`` on every call would yield
    different classes with the same name, which drf-spectacular
    diagnoses as a component collision. Caching by signature gives a
    stable identity across views.
    """
    cache_key = (status_code, sorted_codes)
    cached = _ERROR_SERIALIZER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Schema component name: human-readable, status-scoped, stable
    # across regenerations.
    component_name = f"ErrorResponse{status_code}_" + "_".join(sorted_codes)[:80]

    serializer = inline_serializer(
        name=component_name,
        fields={
            "error_code": serializers.ChoiceField(
                choices=[(v, v) for v in sorted_codes],
                help_text="Symbolic error code emitted at this status.",
            ),
        },
    )
    _ERROR_SERIALIZER_CACHE[cache_key] = serializer
    return serializer


def validation_error_response(
    field_examples: dict[str, list[str]] | None = None,
    *,
    description: str | None = None,
) -> OpenApiResponse:
    """Build the 400 response with the validation-error shape.

    ``field_examples`` lets the endpoint document realistic per-field
    error codes. For example, the login endpoint can advertise that
    ``email`` may return ``FIELD_REQUIRED`` or ``INVALID``::

        validation_error_response({"email": ["FIELD_REQUIRED", "INVALID"]})

    Args:
        field_examples: Map of field name → list of symbolic codes.
            If omitted, a generic placeholder example is shown.
        description: Optional override for the response description.

    Returns:
        :class:`OpenApiResponse` for HTTP 400.
    """
    example_value: dict[str, object] = {"error_code": ErrorCode.VALIDATION_ERROR.value}
    if field_examples:
        example_value["errors"] = field_examples
    else:
        example_value["errors"] = {"<field_name>": ["<SYMBOLIC_CODE>"]}

    return OpenApiResponse(
        response=ValidationErrorResponseSerializer,
        description=description or _DEFAULT_DESCRIPTIONS[400],
        examples=[
            OpenApiExample(
                name="ValidationError",
                summary="Per-field error codes",
                value=example_value,
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )


def forbidden(description: str = _DEFAULT_DESCRIPTIONS[403]) -> dict:
    return error_response(["PERMISSION_DENIED"], status_code=403, description=description)
