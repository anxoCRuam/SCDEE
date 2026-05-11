"""
URL patterns for annotation endpoints.

Two groups:
    instance_annotation_patterns — mounted under /instances/{iid}/
    annotation_detail_patterns — mounted under /annotations/
"""

from django.urls import path

from apps.annotations.views.annotations import (
    AnnotationDetailView,
    AnnotationListCreateView,
    OCRGradeView,
    OCRStylusView,
)

# Mounted under /instances/{iid}/
instance_annotation_patterns = [
    path("annotations/", AnnotationListCreateView.as_view(), name="annotation-list"),
]

# Mounted under /annotations/
annotation_detail_patterns = [
    path("<uuid:annotation_pk>/", AnnotationDetailView.as_view(), name="annotation-detail"),
    path("<uuid:annotation_pk>/ocr-grade/", OCRGradeView.as_view(), name="ocr-grade"),
    path("<uuid:annotation_pk>/ocr/", OCRStylusView.as_view(), name="ocr-stylus"),
]
