"""
Declarative filters for user list endpoints.

Uses django-filter for clean, maintainable filter declarations.
The TenantManager already scopes queries to the manager's org,
so these filters only add within-organization filtering.

Supported filters (RF-2.12):
    - search: partial text match on first_name, last_name, or email
    - is_active: boolean (true/false)
    - is_staff: boolean (true/false)
    - nia: exact match

Example:
    GET /api/v1/users/?search=garcia&is_active=true&page_size=10

References: RF-2.12
"""

from __future__ import annotations

import django_filters
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

User = get_user_model()


class UserFilter(django_filters.FilterSet):
    """Filter set for user listing.

    The `search` filter performs a case-insensitive partial match
    across first_name, last_name, and email. This covers the
    requirement for "búsqueda parcial por texto en nombre y apellidos"
    and extends it to email for convenience.
    """

    search = django_filters.CharFilter(
        method="filter_search",
        help_text="Partial text search on first name, last name, or email.",
    )
    is_active = django_filters.BooleanFilter(
        field_name="is_active",
        help_text="Filter by active status (true/false).",
    )
    is_staff = django_filters.BooleanFilter(
        field_name="is_staff",
        help_text="Filter by manager role (true/false).",
    )
    nia = django_filters.CharFilter(
        field_name="nia",
        lookup_expr="iexact",
        help_text="Exact match on institutional identifier (case-insensitive).",
    )
    email = django_filters.CharFilter(
        field_name="email",
        lookup_expr="icontains",
        help_text="Partial match on email address.",
    )

    class Meta:
        model = User
        fields = ["is_active", "is_staff", "nia", "email"]

    def filter_search(self, queryset: QuerySet, name: str, value: str) -> QuerySet:
        """Search across multiple text fields simultaneously.

        Uses Q objects with OR to match any of the fields.
        icontains provides case-insensitive partial matching.
        """
        if not value:
            return queryset

        return queryset.filter(
            Q(first_name__icontains=value)
            | Q(last_name__icontains=value)
            | Q(email__icontains=value)
        )
