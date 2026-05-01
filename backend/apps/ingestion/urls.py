"""
URL patterns for ingestion/recognition endpoints.

Mounted at: /api/v1/recognition/
"""

from django.urls import path

from apps.ingestion.views import ManualIngestView, OCREngineListView

app_name = "ingestion"

urlpatterns = [
    path("engines/", OCREngineListView.as_view(), name="engine-list"),
    path("ingest/", ManualIngestView.as_view(), name="manual-ingest"),
]
