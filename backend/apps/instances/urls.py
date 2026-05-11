"""
URL patterns for instances, pages, grading, and assignments.

Mounted at multiple prefixes in api_v1.py:
    /exams/{eid}/instances/                  — Instance list
    /instances/{iid}/                        — Instance detail/update/delete
    /instances/{iid}/download/               — PDF download

    /instances/{iid}/pages/                  — Page management

    /instances/{iid}/transition/             — State transition
    /exams/{eid}/publish/                    — Bulk publish

    /exams/{eid}/coverage/                   — Coverage check
    /exams/{eid}/assignment-rules/           — Assignment rules (list and create)
    /assignment-rules/{rid}/                 — Delete rule
    /instances/{iid}/problems/{pid}/grade/   — Manual grading
    /instances/{iid}/problems/{pid}/rubric/  — Rubric grading
    /my-tasks/                               — Corrector tasks
"""

from django.urls import path

from apps.instances.views.instances import (
    AssignmentRuleDeleteView,
    AssignmentRuleListCreateView,
    CoverageView,
    InstanceCreateView,
    InstanceDetailView,
    InstanceDownloadView,
    InstanceListView,
    ManualGradeView,
    PageAcceptExtraView,
    PageAttachView,
    PageDeleteView,
    PageListView,
    PageMoveView,
    PageReorderView,
    PublishView,
    RubricGradeView,
    TransitionView,
)

# Mounted under /exams/{eid}/
exam_instance_patterns = [
    path("instances/", InstanceListView.as_view(), name="instance-list"),
    path("assignment-rules/", AssignmentRuleListCreateView.as_view(), name="rule-list"),
    path("coverage/", CoverageView.as_view(), name="coverage"),
    path("publish/", PublishView.as_view(), name="publish"),
    path("instances/create/", InstanceCreateView.as_view(), name="instance-create"),
]

# Mounted under /instances/
instance_patterns = [
    path("<uuid:instance_pk>/", InstanceDetailView.as_view(), name="instance-detail"),
    path("<uuid:instance_pk>/transition/", TransitionView.as_view(), name="transition"),
    path("<uuid:instance_pk>/download/", InstanceDownloadView.as_view(), name="download"),
    # Pages.
    path("<uuid:instance_pk>/pages/", PageListView.as_view(), name="page-list"),
    path("<uuid:instance_pk>/pages/reorder/", PageReorderView.as_view(), name="page-reorder"),
    path("<uuid:instance_pk>/pages/attach/", PageAttachView.as_view(), name="page-attach"),
    path("<uuid:instance_pk>/pages/<uuid:page_pk>/", PageDeleteView.as_view(), name="page-delete"),
    path(
        "<uuid:instance_pk>/pages/<uuid:page_pk>/move/", PageMoveView.as_view(), name="page-move"
    ),
    path(
        "<uuid:instance_pk>/pages/<uuid:page_pk>/accept/",
        PageAcceptExtraView.as_view(),
        name="page-accept",
    ),
    # Grading.
    path(
        "<uuid:instance_pk>/problems/<uuid:problem_pk>/grade/",
        ManualGradeView.as_view(),
        name="manual-grade",
    ),
    path(
        "<uuid:instance_pk>/problems/<uuid:problem_pk>/rubric/",
        RubricGradeView.as_view(),
        name="rubric-grade",
    ),
]

# Mounted under /assignment-rules/
assignment_rule_patterns = [
    path("<uuid:rule_pk>/", AssignmentRuleDeleteView.as_view(), name="rule-delete"),
]
