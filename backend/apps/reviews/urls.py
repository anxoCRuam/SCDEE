"""
URL patterns for review endpoints.

Three groups:
    exam_review_patterns — mounted under /exams/{eid}/
    instance_review_patterns — mounted under /instances/{iid}/
    review_request_patterns — mounted under /review-requests/
"""

from django.urls import path

from apps.reviews.views import (
    ReviewCreateView,
    ReviewRequestResolveView,
    ReviewRequestSubmitView,
)

# Mounted under /exams/{eid}/
exam_review_patterns = [
    path("review/", ReviewCreateView.as_view(), name="review-create"),
]

# Mounted under /instances/{iid}/
instance_review_patterns = [
    path("review-requests/", ReviewRequestSubmitView.as_view(), name="review-request-submit"),
]

# Mounted under /review-requests/
review_request_patterns = [
    path(
        "<uuid:request_pk>/resolve/",
        ReviewRequestResolveView.as_view(),
        name="review-request-resolve",
    ),
]
