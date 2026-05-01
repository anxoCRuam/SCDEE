"""
OpenAPI schema sanity and contract regression tests.

This file combines two concerns:
1. Schema generation sanity: ensures `manage.py spectacular` runs without
   "unable to guess serializer" warnings (RNF-8).
2. Contract regression: guards against common mistakes like public endpoints
   being marked as authenticated, wrong error response shapes, missing
   security schemes, or missing 429 responses (RNF-6, RNF-8).

References: RNF-8, RNF-6.
"""

from __future__ import annotations

import io
import os
import re

import pytest
import yaml
from django.core.management import call_command

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────
# Part 1: Schema generation sanity (no "unable to guess serializer" warnings)
# ─────────────────────────────────────────────────────────────────────

_BAD = re.compile(r"unable to guess serializer", re.IGNORECASE)


def test_spectacular_has_no_unable_to_guess_warnings(tmp_path) -> None:
    """Fail if drf-spectacular emits 'unable to guess serializer' warnings.

    These warnings occur when an APIView has neither a `serializer_class`
    nor enough info in `@extend_schema`. They poison the OpenAPI document.
    """
    out_file = tmp_path / "schema.yml"
    stderr = io.StringIO()
    stdout = io.StringIO()

    call_command(
        "spectacular",
        f"--file={os.fspath(out_file)}",
        stderr=stderr,
        stdout=stdout,
    )

    captured = stderr.getvalue() + stdout.getvalue()
    assert not _BAD.search(captured), (
        "drf-spectacular emitted at least one 'unable to guess serializer' "
        "warning. Each affected APIView must declare ``serializer_class`` or "
        "an explicit @extend_schema(request=..., responses=...).\n\n"
        f"Output:\n{captured}"
    )

    assert out_file.exists()
    assert out_file.stat().st_size > 0


# ─────────────────────────────────────────────────────────────────────
# Part 2: OpenAPI contract regression tests
# ─────────────────────────────────────────────────────────────────────

# Endpoint groups
PUBLIC_ENDPOINTS: list[tuple[str, str]] = [
    ("/health/", "get"),
    ("/api/v1/auth/login/", "post"),
    ("/api/v1/auth/refresh/", "post"),
    ("/api/v1/auth/logout/", "post"),
]

AUTHED_ENDPOINTS_SAMPLE: list[tuple[str, str]] = [
    ("/api/v1/profile/", "get"),
    ("/api/v1/profile/", "patch"),
    ("/api/v1/users/", "get"),
    ("/api/v1/courses/", "get"),
]


@pytest.fixture(scope="module")
def schema(tmp_path_factory) -> dict:
    """Generate the OpenAPI document once and return it as a parsed dict."""
    out_dir = tmp_path_factory.mktemp("schema")
    out_file = out_dir / "schema.yml"
    stderr = io.StringIO()
    stdout = io.StringIO()
    call_command(
        "spectacular",
        f"--file={os.fspath(out_file)}",
        stderr=stderr,
        stdout=stdout,
    )
    with open(out_file) as f:
        return yaml.safe_load(f)


def _operation(schema: dict, path: str, method: str) -> dict:
    """Look up an operation, fail with a helpful message if missing."""
    paths = schema.get("paths", {})
    if path not in paths:
        pytest.fail(
            f"Path {path!r} not found in OpenAPI document. "
            f"Available paths matching prefix: "
            f"{[p for p in paths if p.startswith(path[:20])][:10]}"
        )
    path_item = paths[path]
    if method not in path_item:
        pytest.fail(
            f"Method {method.upper()} not declared on {path!r}. "
            f"Declared methods: {sorted(path_item.keys())}"
        )
    return path_item[method]


def _security_marks_public(operation: dict) -> bool:
    """Return True if the operation is publicly accessible.

    Public endpoints have `security: [{}]` (an empty requirement).
    """
    security = operation.get("security")
    if not security:
        return False
    return any(not req for req in security)


def _resolve_ref(schema: dict, schema_obj: dict) -> dict:
    """Resolve a single `$ref` if present."""
    if "$ref" in schema_obj:
        ref_name = schema_obj["$ref"].rsplit("/", 1)[-1]
        return schema["components"]["schemas"][ref_name]
    return schema_obj


def _enum_codes(schema: dict, response: dict) -> set[str]:
    """Extract the `error_code` enum values declared on a response."""
    content = response.get("content", {}).get("application/json", {})
    schema_obj = content.get("schema")
    if not schema_obj:
        return set()
    schema_obj = _resolve_ref(schema, schema_obj)

    error_code_prop = schema_obj.get("properties", {}).get("error_code", {})
    if "allOf" in error_code_prop:
        for entry in error_code_prop["allOf"]:
            if "$ref" in entry:
                resolved = _resolve_ref(schema, entry)
                if "enum" in resolved:
                    return set(resolved["enum"])
    if "enum" in error_code_prop:
        return set(error_code_prop["enum"])
    return set()


# -- Public endpoints tests --------------------------------------------


@pytest.mark.parametrize(("path", "method"), PUBLIC_ENDPOINTS)
def test_public_endpoint_does_not_have_generic_401(schema, path, method):
    """Public endpoints must NOT advertise `AUTHENTICATION_REQUIRED`."""
    operation = _operation(schema, path, method)
    response_401 = operation.get("responses", {}).get("401")
    if response_401 is None:
        return
    codes = _enum_codes(schema, response_401)
    assert "AUTHENTICATION_REQUIRED" not in codes, (
        f"{path} {method.upper()} is public but its 401 response carries "
        f"the generic `AUTHENTICATION_REQUIRED` code. Codes: {sorted(codes)}"
    )


@pytest.mark.parametrize(("path", "method"), PUBLIC_ENDPOINTS)
def test_public_endpoint_does_not_have_generic_403(schema, path, method):
    """Public endpoints must NOT advertise `PERMISSION_DENIED`."""
    operation = _operation(schema, path, method)
    response_403 = operation.get("responses", {}).get("403")
    if response_403 is None:
        return
    codes = _enum_codes(schema, response_403)
    assert "PERMISSION_DENIED" not in codes, (
        f"{path} {method.upper()} is public but its 403 response carries "
        f"the generic `PERMISSION_DENIED` code. Codes: {sorted(codes)}"
    )


@pytest.mark.parametrize(("path", "method"), PUBLIC_ENDPOINTS)
def test_public_endpoint_security_marker_is_present(schema, path, method):
    """Public endpoints must have `security: [{}]` to override global auth."""
    operation = _operation(schema, path, method)
    assert _security_marks_public(operation), (
        f"{path} {method.upper()} is public but its security field does not "
        f"signal an empty requirement: {operation.get('security')!r}"
    )


# -- Authenticated endpoints sample tests -----------------------------


@pytest.mark.parametrize(("path", "method"), AUTHED_ENDPOINTS_SAMPLE)
def test_authed_endpoint_has_401_and_403(schema, path, method):
    """Authenticated endpoints must document both 401 and 403 responses."""
    operation = _operation(schema, path, method)
    responses = operation.get("responses", {})
    assert "401" in responses, f"{path} {method.upper()} missing 401"
    assert "403" in responses, f"{path} {method.upper()} missing 403"


# -- Error response shape tests ---------------------------------------


def test_unauthorized_response_uses_error_code_not_detail(schema):
    """401 response must use `error_code` (RNF-8), not DRF's `detail`."""
    operation = _operation(schema, "/api/v1/profile/", "get")
    response_401 = operation["responses"]["401"]
    schema_obj = response_401["content"]["application/json"]["schema"]
    if "$ref" in schema_obj:
        schema_obj = schema["components"]["schemas"][schema_obj["$ref"].rsplit("/", 1)[-1]]
    properties = schema_obj.get("properties", {})
    assert "error_code" in properties
    assert "detail" not in properties


def test_validation_error_response_has_errors_field(schema):
    """400 validation error must contain `errors` map for per-field codes."""
    operation = _operation(schema, "/api/v1/auth/login/", "post")
    response_400 = operation["responses"]["400"]
    schema_obj = response_400["content"]["application/json"]["schema"]
    if "$ref" in schema_obj:
        schema_obj = schema["components"]["schemas"][schema_obj["$ref"].rsplit("/", 1)[-1]]
    properties = schema_obj.get("properties", {})
    assert "error_code" in properties
    assert "errors" in properties


# -- Security scheme tests --------------------------------------------


def test_bearer_auth_security_scheme_is_declared(schema):
    """OpenAPI must declare `BearerAuth` so Swagger's Authorize button works."""
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert "BearerAuth" in schemes, f"Missing BearerAuth. Found: {sorted(schemes.keys())}"
    bearer = schemes["BearerAuth"]
    assert bearer.get("type") == "http"
    assert bearer.get("scheme") == "bearer"
    assert bearer.get("bearerFormat") == "JWT"


# -- Rate limit visibility tests --------------------------------------


def test_every_operation_documents_429(schema):
    """All endpoints are throttled (RNF-6); 429 must appear in every operation."""
    http_methods = {"get", "post", "put", "patch", "delete"}
    missing = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in http_methods:
                continue
            if "429" not in operation.get("responses", {}):
                missing.append(f"{method.upper()} {path}")
    assert not missing, (
        "These operations do not document a 429 response (RNF-6):\n  - " + "\n  - ".join(missing)
    )
