"""
URL patterns for exam-related endpoints.

Mounted at: /api/v1/ (multiple prefixes)

Endpoint structure:
    /subjects/{sid}/exams/                         — List/Create exams
    /exams/{eid}/                                  — Detail/Update/Delete exam
    /exams/{eid}/models/                           — Create model
    /exams/{eid}/convocation/                      — Set convocation
    /models/{mid}/blank-pdf/                       — Upload blank PDF
    /models/{mid}/page-profiles/                   — Create/List page profiles
    /models/{mid}/problems/                        — Create/List problems
    /models/{mid}/generate-instrumented-pdf/       — Generate PDF
    /models/{mid}/instrumented-pdf/                — Download PDF
    /page-profiles/{pid}/zones/                    — Create zones
    /zones/{zid}/                                  — Delete zone
    /problems/{pid}/rubric/                        — Set rubric
"""

from django.urls import path

from apps.exams.views import (
    BlankPDFUploadView,
    ConvocationView,
    DownloadInstrumentedPDFView,
    ExamDetailView,
    ExamListCreateView,
    GenerateInstrumentedPDFView,
    GradeExportCSVView,
    GradeExportJSONView,
    ModelCreateView,
    PageProfileCreateView,
    ProblemCreateView,
    RubricSetView,
    ZoneCreateView,
    ZoneDeleteView,
)

app_name = "exams"

# Routes are split across api_v1.py prefixes:
# - subject_exam_patterns: mounted under /subjects/{sid}/exams/
# - exam_patterns: mounted under /exams/
# - model_patterns: mounted under /models/
# - profile_patterns: mounted under /page-profiles/
# - zone_patterns: mounted under /zones/
# - problem_patterns: mounted under /problems/

subject_exam_patterns = [
    path("", ExamListCreateView.as_view(), name="exam-list-create"),
]

exam_patterns = [
    path("<uuid:exam_pk>/", ExamDetailView.as_view(), name="exam-detail"),
    path("<uuid:exam_pk>/models/", ModelCreateView.as_view(), name="model-create"),
    path("<uuid:exam_pk>/convocation/", ConvocationView.as_view(), name="convocation"),
    path(
        "<uuid:exam_pk>/export-grades/",
        GradeExportCSVView.as_view(),
        name="export-grades-csv",
    ),
    path("<uuid:exam_pk>/grades/", GradeExportJSONView.as_view(), name="export-grades-json"),
]

model_patterns = [
    path("<uuid:model_pk>/blank-pdf/", BlankPDFUploadView.as_view(), name="blank-pdf"),
    path("<uuid:model_pk>/page-profiles/", PageProfileCreateView.as_view(), name="page-profiles"),
    path("<uuid:model_pk>/problems/", ProblemCreateView.as_view(), name="problems"),
    path(
        "<uuid:model_pk>/generate-instrumented-pdf/",
        GenerateInstrumentedPDFView.as_view(),
        name="generate-instrumented-pdf",
    ),
    path(
        "<uuid:model_pk>/instrumented-pdf/",
        DownloadInstrumentedPDFView.as_view(),
        name="instrumented-pdf",
    ),
]

page_profile_patterns = [
    path("<uuid:profile_pk>/zones/", ZoneCreateView.as_view(), name="zone-create"),
]

zone_patterns = [
    path("<uuid:zone_pk>/", ZoneDeleteView.as_view(), name="zone-delete"),
]

problem_patterns = [
    path("<uuid:problem_pk>/rubric/", RubricSetView.as_view(), name="rubric-set"),
]
