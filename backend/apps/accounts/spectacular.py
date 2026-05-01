"""
drf-spectacular extensions and postprocessing hooks.

Two responsibilities:

1. Register the BearerAuth security scheme so JWT-protected endpoints
   appear with ``Authorization: Bearer <token>`` in the OpenAPI
   document and drf-spectacular stops emitting "could not resolve
   authenticator" warnings.

2. Inject the standard error responses (400 / 401 / 403 / 404 / 429)
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

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Maps JWTAuthentication → OpenAPI HTTP Bearer security scheme."""

    target_class = "apps.accounts.backends.JWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "JWT access token. Obtain via `POST /api/v1/auth/login/`. "
                "Pass as `Authorization: Bearer <token>`. "
                "Access tokens expire after 15 minutes by default; "
                "use `POST /api/v1/auth/refresh/` to renew."
            ),
        }


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


# ════════════════════════════════════════════════════════════════════
# Postprocessing hook: enrich thin descriptions from the view docstring
# ════════════════════════════════════════════════════════════════════
#
# drf-spectacular falls back to the *method* docstring when no
# ``description=`` is given to ``@extend_schema``. Method docstrings in
# this codebase are typically short one-liners ("Update an annotation
# (RF-10.x)."), so the resulting OpenAPI descriptions are thin.
#
# This hook complements them with the corresponding *class* docstring,
# which tends to explain the resource's purpose, the auth model, and
# the cross-cutting rules — exactly what a frontend developer needs.


def _is_thin_description(operation: dict[str, Any]) -> bool:
    """Heuristic: true when the operation's description is too short to be useful.

    ``too short`` means ``None``, missing, equal to the summary, or under
    160 characters. The threshold is conservative: anything richer than
    a method one-liner survives untouched.
    """
    description = operation.get("description") or ""
    summary = operation.get("summary") or ""
    if not description:
        return True
    if description.strip() == summary.strip():
        return True
    return len(description) < 160


def _resolve_view_class(operation_id: str, schema_generator: Any) -> Any | None:
    """Return the view class for the given ``operationId``, if findable.

    drf-spectacular exposes the resolved endpoints on the generator,
    which lets us walk the operation_id back to its callable. We rely
    on the generator's internal ``endpoints`` attribute; if its shape
    changes in a future drf-spectacular release the hook degrades to
    a no-op (we just don't enrich), it does not break.
    """
    if schema_generator is None:
        return None
    endpoints = getattr(schema_generator, "endpoints", None)
    if not endpoints:
        return None
    for _path, _path_regex, _method, callback in endpoints:
        view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if view_cls is None:
            continue
        # The operation_id is set on @extend_schema; it lives on the
        # callable's spectacular metadata. Compare against the schema's
        # actual operationId via the generated path/method instead.
        # Simpler: return the view_class for the path/method match.
        # (This loop is consumed by the caller via path/method, not here.)
        del view_cls  # unused — see _enrich_descriptions
    return None


def _build_enriched_description(
    summary: str,
    method_description: str,
    class_doc: str,
) -> str:
    """Combine the existing ``description`` with the class docstring.

    Returns the class docstring on its own when:
    - the existing description is missing, or
    - the existing description is the same prose as the class docstring
      (a common case when the method docstring is just the class
      summary repeated).

    Otherwise composes: existing description, then the class docstring
    under a horizontal rule.
    """
    method_clean = (method_description or "").strip()
    class_clean = (class_doc or "").strip()

    # No existing description, or existing description is essentially
    # the summary: replace with the class docstring.
    if not method_clean or method_clean == summary.strip():
        return class_clean

    # Existing description is a *prefix* (or substring) of the class
    # docstring: the class doc supersedes it, return only the class doc.
    if method_clean in class_clean or class_clean.startswith(method_clean):
        return class_clean

    # Existing description is richer than the class doc: leave as is.
    if class_clean in method_clean:
        return method_clean

    # Both contribute distinct content: compose them.
    return method_clean + "\n\n---\n\n**About this resource**\n\n" + class_clean


def enrich_thin_descriptions(
    result: dict[str, Any],
    generator: Any,
    request: object,
    public: bool,
) -> dict[str, Any]:
    """Postprocessing hook: backfill thin descriptions from class docstrings.

    For every operation whose ``description`` is empty or shorter than
    a useful threshold, this hook resolves the view class via the
    schema generator's ``endpoints`` mapping and uses the class
    docstring (cleaned with :func:`inspect.cleandoc`) as a richer
    description.

    Operations that already declare a substantial ``description=`` in
    their ``@extend_schema`` are left untouched — author intent wins.

    Args:
        result: The OpenAPI document (modified in-place).
        generator: drf-spectacular generator with the ``endpoints``
            iterable.
        request: HTTP request used to render the schema (unused).
        public: Whether the schema is being rendered publicly (unused).

    Returns:
        The modified OpenAPI document.
    """
    import inspect

    # Build a (path, method) -> view_class map from the generator.
    endpoint_map: dict[tuple[str, str], Any] = {}
    endpoints = getattr(generator, "endpoints", None) or []
    for _path, _path_regex, method, callback in endpoints:
        view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if view_cls is None:
            continue
        endpoint_map[(_path, method.lower())] = view_cls

    for path, path_item in result.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            if not _is_thin_description(operation):
                continue

            view_cls = endpoint_map.get((path, method))
            class_doc = inspect.getdoc(view_cls) if view_cls is not None else None
            if not class_doc:
                continue

            operation["description"] = _build_enriched_description(
                summary=operation.get("summary", ""),
                method_description=operation.get("description", "") or "",
                class_doc=class_doc,
            )

    return result
