"""
drf-spectacular postprocessing hooks.

Inject the standard error responses (400 / 401 / 403 / 404 / 429)
into every applicable operation, using the realistic
``{"error_code": "..."}`` schema (not DRF's default
``{"detail": "..."}``).

Public endpoints — login, refresh, logout, health — opt out of
authentication via ``permission_classes = [AllowAny]`` and
``authentication_classes = []``. drf-spectacular emits ``security:
[{}]`` for those (an empty security requirement, meaning "no auth
needed"). The previous implementation treated that list as truthy and
incorrectly attached 401/403 responses to public endpoints, including
``/health/``. The fix is in :func:`_endpoint_requires_auth`: we look
for at least one *non-empty* security requirement, since OpenAPI's
"no auth" representation is precisely the empty dict.

Import is triggered from ``AccountsConfig.ready()`` so the extension
registers before the schema is generated.

References: RNF-8.
"""

from __future__ import annotations

from typing import Any

# ════════════════════════════════════════════════════════════════════
# Postprocessing hook: inject standard error responses
# ════════════════════════════════════════════════════════════════════


_HTTP_METHODS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete"})


def _endpoint_requires_auth(operation: dict[str, Any]) -> bool:
    """Return ``True`` iff the operation **requires** authentication.

    OpenAPI's ``security`` field is a list of *alternative* requirements:
    a request is authorised if it satisfies any one of them. The empty
    requirement ``{}`` represents "no authentication needed". Therefore:

    - ``[]`` or missing → no security at all (drf-spectacular treats this
      as "inherit global"; on this project the global is JWT-required).
    - ``[{}]`` → purely public.
    - ``[{'BearerAuth': []}]`` → bearer required, no anonymous alternative.
    - ``[{'BearerAuth': []}, {}]`` → bearer *optional*: an anonymous request
      is also allowed. This applies to ``/auth/logout/`` (RF-1.4),
      where the view does ``permission_classes = [AllowAny]`` but is
      annotated as authenticated by the view's ``authentication_classes``
      so that, *if* a token is sent, the actor is identified for the
      audit log.

    The endpoint **requires** auth iff every alternative names a scheme
    — equivalently, no alternative is the empty dict. The semantics of
    ``all()`` on an empty list is ``True``, but that case is handled by
    the early-return on ``not security`` above.
    """
    security = operation.get("security")
    if not security:  # missing or []
        return False
    return all(bool(req) for req in security)


def _path_has_param(path: str) -> bool:
    """Return ``True`` if the URL template contains a path parameter."""
    return "{" in path


# ── Schema fragments (single source of truth — kept here, not in the
#    schema_components arg, because adding components from a hook is
#    cleaner than duplicating the YAML in every endpoint). ─────────


def _generic_error_schema(code_value: str) -> dict[str, Any]:
    """Build the ``content`` block for a generic single-code error.

    Keeps the response inline (no ``$ref``) so this hook does not
    have to register schema components — the explicit schemas are
    already provided by the views that call ``error_response()``.
    """
    return {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["error_code"],
                "properties": {
                    "error_code": {
                        "type": "string",
                        "enum": [code_value],
                        "description": (
                            "Symbolic, locale-independent error identifier. "
                            "Map to a localised message on the frontend."
                        ),
                    },
                },
            },
            "examples": {
                code_value: {
                    "summary": code_value,
                    "value": {"error_code": code_value},
                },
            },
        },
    }


def _validation_error_schema() -> dict[str, Any]:
    """Build the ``content`` block for the validation-error shape (400)."""
    return {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["error_code", "errors"],
                "properties": {
                    "error_code": {
                        "type": "string",
                        "enum": ["VALIDATION_ERROR"],
                    },
                    "errors": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "description": (
                            "Map of field name → list of symbolic error codes. "
                            "Nested fields are flattened with dot notation."
                        ),
                    },
                },
            },
            "examples": {
                "ValidationError": {
                    "summary": "Per-field error codes",
                    "value": {
                        "error_code": "VALIDATION_ERROR",
                        "errors": {"<field_name>": ["<SYMBOLIC_CODE>"]},
                    },
                },
            },
        },
    }


# Templates for each generic error response, keyed by status code.
# The hook installs them only when the view has not already declared
# a custom response at that status (``setdefault`` semantics).

_GENERIC_RESPONSES: dict[str, dict[str, Any]] = {
    "400": {
        "description": "Invalid input — see ``errors`` for field-level details.",
        "content": _validation_error_schema(),
    },
    "401": {
        "description": "Authentication credentials missing, expired or revoked.",
        "content": _generic_error_schema("AUTHENTICATION_REQUIRED"),
    },
    "403": {
        "description": "Authenticated, but not authorised for this resource.",
        "content": _generic_error_schema("PERMISSION_DENIED"),
    },
    "404": {
        "description": "Resource not found in the caller's organisation.",
        "content": _generic_error_schema("NOT_FOUND"),
    },
    "429": {
        "description": "Rate limit exceeded for this endpoint.",
        "content": _generic_error_schema("RATE_LIMIT_EXCEEDED"),
    },
}


def add_global_error_responses(
    result: dict[str, Any],
    generator: object,
    request: object,
    public: bool,
) -> dict[str, Any]:
    """Postprocessing hook: inject standard error responses.

    Rules — all gated by ``setdefault``, never overwriting an explicit
    response set by ``@extend_schema``:

    - **401 + 403**: only on operations that actually require auth.
      Public endpoints (``security: [{}]`` or none) are skipped.
    - **404**: only on operations whose path template includes a path
      parameter (``/users/{id}/``), since those are the ones that can
      legitimately fail to find a resource.
    - **400**: only on write operations (POST/PUT/PATCH). Uses the
      validation-error schema.
    - **429**: on every operation. Throttling is configured globally
      in ``REST_FRAMEWORK.DEFAULT_THROTTLE_CLASSES`` so any endpoint
      can return 429.

    Args:
        result: The OpenAPI document (modified in-place).
        generator: drf-spectacular generator (unused).
        request: HTTP request used to render the schema (unused).
        public: Whether the schema is being rendered publicly (unused).

    Returns:
        The modified OpenAPI document.
    """
    for path, path_item in result.get("paths", {}).items():
        has_path_param = _path_has_param(path)

        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue

            responses = operation.setdefault("responses", {})
            requires_auth = _endpoint_requires_auth(operation)

            # 401 / 403 — only on protected endpoints.
            if requires_auth:
                responses.setdefault("401", _GENERIC_RESPONSES["401"])
                responses.setdefault("403", _GENERIC_RESPONSES["403"])

            # 404 — only on detail endpoints.
            if has_path_param:
                responses.setdefault("404", _GENERIC_RESPONSES["404"])

            # 400 — only on write operations.
            if method in {"post", "put", "patch"}:
                responses.setdefault("400", _GENERIC_RESPONSES["400"])

            # 429 — universal: throttling applies to every endpoint.
            responses.setdefault("429", _GENERIC_RESPONSES["429"])

    return result
