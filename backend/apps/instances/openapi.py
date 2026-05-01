"""
Reusable OpenAPI examples for the ``instances`` app.

Imported on demand from each view's ``@extend_schema`` decorator. Keeping
these out of ``views.py`` keeps the views focused on logic.

References: RF-7.x, RF-8.x, RF-11.x.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample

# ════════════════════════════════════════════════════════════════════
# Manual grading (RF-11.1) — optimistic concurrency
# ════════════════════════════════════════════════════════════════════


MANUAL_GRADE_REQUEST_EXAMPLE = OpenApiExample(
    name="ManualGradeRequest",
    summary="Set a problem score with optimistic concurrency",
    description=(
        "``prev_score`` is the instance score the corrector last saw. The "
        "service compares it against the stored value and rejects the "
        "update with HTTP 409 ``CONFLICT`` if another corrector touched "
        "the instance in between."
    ),
    value={"score": "8.50", "prev_score": "7.00"},
    request_only=True,
)

MANUAL_GRADE_RESPONSE_EXAMPLE = OpenApiExample(
    name="ManualGradeSuccess",
    summary="Updated grade + new instance version",
    value={
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "instance_id": "11111111-2222-3333-4444-555555555555",
        "problem_id": "99999999-8888-7777-6666-555555555555",
        "score": "8.50",
        "graded_by": {
            "id": "5e1f8a3a-9b7d-4e1a-9c1d-1234567890ab",
            "first_name": "María",
            "last_name": "García",
        },
        "graded_at": "2026-04-27T10:42:13Z",
    },
    response_only=True,
    status_codes=["200"],
)


# ════════════════════════════════════════════════════════════════════
# State transition (RF-7.5)
# ════════════════════════════════════════════════════════════════════


TRANSITION_REQUEST_EXAMPLE = OpenApiExample(
    name="TransitionRequest",
    summary="Move PENDING_GRADING → GRADED",
    description=(
        "Allowed transitions are checked server-side. Invalid transitions "
        "return HTTP 409 with ``error_code: CONFLICT`` and the offending "
        "target in the audit log."
    ),
    value={"target_status": "GRADED"},
    request_only=True,
)


# ════════════════════════════════════════════════════════════════════
# Bulk publish (RF-7.9)
# ════════════════════════════════════════════════════════════════════


PUBLISH_RESPONSE_EXAMPLE = OpenApiExample(
    name="PublishResult",
    summary="Aggregated publish report",
    description=(
        "Returned even when nothing was published — the counts let the "
        "caller distinguish between ‘nothing to do’ and ‘some skipped "
        "due to issues’."
    ),
    value={
        "published": 287,
        "skipped": 13,
        "skipped_reasons": {
            "HAS_ISSUES": 9,
            "NOT_GRADED": 4,
        },
    },
    response_only=True,
    status_codes=["200"],
)
