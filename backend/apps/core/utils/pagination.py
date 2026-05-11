"""
Standard pagination for all list endpoints.

Uses offset-based pagination (page number + page size) as required
by RNF-15. Every paginated response includes:
    - count:       Total number of items matching the query.
    - page:        Current page number (1-based).
    - page_size:   Number of items per page.
    - total_pages: Total number of pages.
    - results:     Array of items for the current page.

Example response:
    {
        "count": 142,
        "page": 3,
        "page_size": 25,
        "total_pages": 6,
        "next": "http://api/v1/users/?page=4",
        "previous": "http://api/v1/users/?page=2",
        "results": [...]
    }

Why offset pagination instead of cursor?
    The requirements explicitly ask for total count and page numbers
    (RNF-15). Cursor pagination is more performant for large datasets
    but cannot provide total count without a separate COUNT query.
"""

import math
from collections import OrderedDict
from typing import Any

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """Offset-based pagination with configurable page size.

    Query parameters:
        page:      Page number (default: 1)
        page_size: Items per page (default: 25, max: 100)
    """

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data: list[Any]) -> Response:
        """Override to include total_pages and current page metadata."""
        total = self.page.paginator.count
        page_size = self.get_page_size(self.request)

        return Response(
            OrderedDict(
                [
                    ("count", total),
                    ("page", self.page.number),
                    ("page_size", page_size),
                    ("total_pages", math.ceil(total / page_size) if page_size else 0),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["count", "page", "page_size", "total_pages", "results"],
            "properties": {
                "count": {"type": "integer", "example": 142},
                "page": {"type": "integer", "example": 1},
                "page_size": {"type": "integer", "example": 25},
                "total_pages": {"type": "integer", "example": 6},
                "next": {
                    "type": "string",
                    "nullable": True,
                    "format": "uri",
                    "example": "http://api.example.com/.../?page=2",
                },
                "previous": {
                    "type": "string",
                    "nullable": True,
                    "format": "uri",
                    "example": None,
                },
                "results": schema,
            },
        }
