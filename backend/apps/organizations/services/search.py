"""
Search engine

References: RF-14
"""

from __future__ import annotations

import logging

import django_filters

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam
from apps.instances.models.instances import ExamInstance
from apps.subjects.models.subjects import Subject

logger = logging.getLogger(__name__)


class CourseFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = AcademicCourse
        fields = []

    def filter_search(self, queryset, name, value):
        return queryset.filter(label__icontains=value)


class SubjectFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Subject
        fields = []

    def filter_search(self, queryset, name, value):
        return queryset.filter(name__icontains=value) | queryset.filter(code__icontains=value)


class ExamFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Exam
        fields = []

    def filter_search(self, queryset, name, value):
        return queryset.filter(name__icontains=value)


class InstanceFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(field_name="status")
    has_issues = django_filters.BooleanFilter(field_name="has_issues")
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = ExamInstance
        fields = ["status", "has_issues"]

    def filter_search(self, queryset, name, value):
        # Buscar por email de estudiante o etiqueta de modelo
        return queryset.filter(student__email__icontains=value) | queryset.filter(
            model__label__icontains=value
        )
