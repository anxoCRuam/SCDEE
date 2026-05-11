"""
URL patterns for academic course endpoints.

Mounted at: /api/v1/courses/

Endpoints:
    POST   /api/v1/courses/         — Create course (RF-3.1)
    GET    /api/v1/courses/{id}/    — Retrieve course
    GET    /api/v1/courses/current/    — Retrieve current course details
    PATCH  /api/v1/courses/{id}/    — Update course (RF-3.2)

No DELETE: courses are archived via transition, not deleted.
"""

from django.urls import path

from apps.courses.views.courses import CourseViewSet

app_name = "courses"

course_create = CourseViewSet.as_view(
    {
        "post": "create",
    }
)

current_course = CourseViewSet.as_view({"get": "current"})
course_detail = CourseViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)

urlpatterns = [
    path("", course_create, name="course-create"),
    path("current/", current_course, name="course-current"),
    path("<uuid:pk>/", course_detail, name="course-detail"),
]
