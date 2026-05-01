# ============================================================
# drf-spectacular — OpenAPI schema (RNF-8)
# ============================================================
# The OpenAPI document is the contract between backend and frontend.
# It MUST be sufficient on its own — a frontend developer should be
# able to integrate against the API without reading any backend code.
#
# To that end, the description below acts as the document's preface.
# It explains the cross-cutting conventions (auth, multi-tenancy,
# pagination, errors) that would otherwise have to be repeated on
# every endpoint.
SPECTACULAR_SETTINGS = {
    "TITLE": "SCDEE API",
    "VERSION": "1.0.0",
    "DESCRIPTION": (
        "Backend API for the **Digital Exam Correction System (SCDEE/B)** "
        "— a multi-tenant platform that ingests scanned written exams, "
        "drives them through OCR / QR recognition, lets graders annotate "
        "and grade them on screen, and publishes the results to students.\n\n"
        "This document is intended to be sufficient on its own — every "
        "cross-cutting convention is described below; every endpoint "
        "documents its specific request/response shape and error codes.\n\n"
        "---\n\n"
        "## Authentication\n\n"
        "All ``/api/v1/*`` endpoints require a JWT bearer token unless "
        "noted otherwise. Obtain a token pair from "
        "``POST /api/v1/auth/login/`` and pass the access token as "
        "``Authorization: Bearer <token>``. Tokens are short-lived "
        "(15 minutes by default); rotate via ``POST /api/v1/auth/refresh/`` "
        "before they expire.\n\n"
        "Public endpoints (no auth required): ``/health/``, "
        "``/api/v1/auth/login/``, ``/api/v1/auth/refresh/``, "
        "``/api/v1/auth/logout/``.\n\n"
        "The authenticator is plugin-based (RF-1.1). v1.0 ships only the "
        "JWT-by-credentials plugin; SSO plugins are designed for but not "
        "implemented in v1.0.\n\n"
        "---\n\n"
        "## Multi-tenancy\n\n"
        "Every business resource is scoped to one **organisation** — a "
        "university, an institute, or a SaaS tenant. The organisation is "
        "encoded in the JWT payload (``organization_id``) and resolved "
        "automatically by middleware on every request. The frontend never "
        "needs to send the organisation id explicitly: the token is "
        "enough. Listings, lookups, and writes only see resources of the "
        "caller's organisation; cross-tenant access returns ``404 NOT_FOUND``.\n\n"
        "---\n\n"
        "## Error format\n\n"
        "All errors follow a uniform JSON shape (RNF-8). The frontend "
        "maps ``error_code`` to a localised user-facing message — the "
        "API never sends human-readable text on the wire.\n\n"
        "Single-code errors (most cases):\n\n"
        "```json\n"
        '{"error_code": "AUTHENTICATION_REQUIRED"}\n'
        "```\n\n"
        "Validation errors (HTTP 400 only) carry per-field details:\n\n"
        "```json\n"
        "{\n"
        '  "error_code": "VALIDATION_ERROR",\n'
        '  "errors": {\n'
        '    "email": ["FIELD_REQUIRED"],\n'
        '    "password": ["MIN_LENGTH"]\n'
        "  }\n"
        "}\n"
        "```\n\n"
        "The most common error codes:\n\n"
        "| Status | ``error_code``              | Meaning                                                 |\n"  # noqa: E501
        "|-------:|------------------------------|---------------------------------------------------------|\n"  # noqa: E501
        "|    400 | ``VALIDATION_ERROR``         | Request body / query string failed serializer validation. |\n"  # noqa: E501
        "|    401 | ``AUTHENTICATION_REQUIRED``  | Missing, expired or revoked access token.               |\n"  # noqa: E501
        "|    403 | ``PERMISSION_DENIED``        | Authenticated, but role does not allow this action.      |\n"  # noqa: E501
        "|    404 | ``NOT_FOUND``                | Resource does not exist in this organisation.           |\n"  # noqa: E501
        "|    409 | ``CONFLICT``                 | Concurrent modification or invalid state transition.    |\n"  # noqa: E501
        "|    429 | ``RATE_LIMIT_EXCEEDED``      | Throttle exceeded — back off and retry.                 |\n"  # noqa: E501
        "|    500 | ``INTERNAL_ERROR``           | Unexpected server error (logged with a trace ID).       |\n\n"  # noqa: E501
        "Endpoint-specific codes (e.g. ``COURSE_ARCHIVED``, "
        "``EMAIL_ALREADY_EXISTS``, ``PROBLEM_NOT_FOUND``) are documented "
        "on each operation's response section.\n\n"
        "---\n\n"
        "## Pagination\n\n"
        "List endpoints use **offset-based pagination** (RNF-15). Query "
        "parameters: ``limit`` (default 25, max 100) and ``offset``. "
        "Every list returns the same envelope:\n\n"
        "```json\n"
        "{\n"
        '  "count": 247,\n'
        '  "next":  "https://.../?limit=25&offset=25",\n'
        '  "previous": null,\n'
        '  "results": [ ... ]\n'
        "}\n"
        "```\n\n"
        "---\n\n"
        "## Rate limiting\n\n"
        "All endpoints are throttled (RNF-6). Defaults: anonymous 30/min, "
        "regular users 120/min, managers 300/min, login 10/min/IP. "
        "Exceeded → ``429 RATE_LIMIT_EXCEEDED``."
    ),
    "CONTACT": {
        "name": "SCDEE Backend",
        "url": "https://github.com/anxoCR/scdee",
    },
    "LICENSE": {
        "name": "Proprietary — Anxo Canay Reguera",
    },
    "SERVERS": [
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
    # Tags grouped logically. The order here drives the rendering order
    # in Swagger UI / ReDoc, so we put authentication first and system
    # endpoints last.
    "TAGS": [
        {"name": "Authentication", "description": "JWT login, refresh y logout (públicos)."},
        {"name": "Profile", "description": "Perfil del usuario autenticado."},
        {"name": "Users", "description": "Gestión de usuarios por el manager (RF-2.x)."},
        {"name": "Organizations", "description": "Superadmin: creación de tenants."},
        {"name": "Configuration", "description": "Parámetros de la organización (RF-15)."},
        {"name": "Courses", "description": "Cursos académicos (RF-3.x)."},
        {"name": "Subjects", "description": "Asignaturas (RF-4.x)."},
        {"name": "Groups", "description": "Grupos dentro de asignaturas."},
        {"name": "Members", "description": "Miembros de asignaturas."},
        {"name": "Permissions", "description": "Permisos de membresías (RF-5.x)."},
        {
            "name": "Exams",
            "description": "Exámenes, modelos, perfiles de página, zonas, problemas, rúbricas, "
            "convocatoria (RF-6.x).",
        },
        {
            "name": "Instances",
            "description": "Instancias de examen, páginas, transiciones de estado, publicación, "
            "descarga PDF (RF-7.x).",
        },
        {
            "name": "Assignments",
            "description": "Reglas de asignación de correctores y cobertura (RF-8.x).",
        },
        {
            "name": "Grading",
            "description": "Corrección manual y por rúbrica, tareas del corrector.",
        },
        {
            "name": "Annotations",
            "description": "Anotaciones en instancias (texto, stylus, audio) y OCR (RF-10.x).",
        },
        {
            "name": "Recognition",
            "description": "Ingestión manual de páginas y listado de motores OCR (RF-9.x).",
        },
        {
            "name": "Reviews",
            "description": "Ventana de revisión y solicitudes de revisión de notas (RF-12.x).",
        },
        {"name": "Notifications", "description": "Notificaciones del usuario (RF-13.x)."},
        {"name": "Search", "description": "Búsqueda jerárquica (RF-14.3)."},
        {"name": "Export", "description": "Exportación de notas y datos (CSV/JSON)."},
        {"name": "System", "description": "Health check (público)."},
    ],
    "SERVE_INCLUDE_SCHEMA": False,
    # COMPONENT_SPLIT_REQUEST=True separates request and response
    # schemas when the same serializer is used for both, so a write-only
    # ``password`` field on a User serializer doesn't pollute the
    # response component.
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {},
    # Swagger UI tweaks for a less noisy default view.
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": True,
        "filter": True,
        "tagsSorter": "alpha",
        "operationsSorter": "method",
    },
    "POSTPROCESSING_HOOKS": [
        # Built-in: collapses enums to $ref components.
        "drf_spectacular.hooks.postprocess_schema_enums",
        # Custom: injects 400/401/403/404/429 into every relevant operation.
        # Crucially, this hook detects public endpoints (security: [{}])
        # and skips 401/403 on them — see apps.accounts.spectacular for
        # the full rule set.
        "apps.accounts.spectacular.add_global_error_responses",
        # Custom: backfills thin descriptions from view class docstrings,
        # so endpoints that did not bother with description=... in their
        # @extend_schema still get a useful resource-level explanation.
        "apps.accounts.spectacular.enrich_thin_descriptions",
    ],
}
