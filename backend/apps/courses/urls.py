"""
URL patterns for academic course endpoints.

Mounted at: /api/v1/courses/

Endpoints:
    POST   /api/v1/courses/         — Create course (RF-3.1)
    GET    /api/v1/courses/         — List courses (RF-3.4)
    GET    /api/v1/courses/{id}/    — Retrieve course
    PATCH  /api/v1/courses/{id}/    — Update course (RF-3.2)

No DELETE: courses are archived via transition, not deleted.
"""

from django.urls import path

from apps.courses.views import CourseViewSet

app_name = "courses"

course_list = CourseViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)

course_detail = CourseViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)

urlpatterns = [
    path("", course_list, name="course-list"),
    path("<uuid:pk>/", course_detail, name="course-detail"),
]
