"""Pagination styles for the v1 API.

* :class:`DefaultPageNumberPagination` — page/size, used by the podcast list
  (TT §3.2 "səhifələnmiş (pagination) formatda siyahı").
* :class:`EpisodeCursorPagination` — cursor pagination keyed on
  ``-published_at`` for the potentially huge per-podcast episode list
  (TT §3.2 "cursor-based və ya offset-based pagination"). Default.
* :class:`EpisodeLimitOffsetPagination` — offset alternative for the same list,
  selected with ``?paginate=offset``.

(The Chart API paginates itself — its result set is bounded to ≤200 rows.)
"""
from __future__ import annotations

from collections import OrderedDict

from rest_framework.pagination import (
    CursorPagination,
    LimitOffsetPagination,
    PageNumberPagination,
)
from rest_framework.response import Response


class DefaultPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("num_pages", self.page.paginator.num_pages),
                    ("page", self.page.number),
                    ("page_size", self.get_page_size(self.request)),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )


class EpisodeCursorPagination(CursorPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("-published_at", "-id")
    cursor_query_param = "cursor"


class EpisodeLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100
    limit_query_param = "limit"
    offset_query_param = "offset"
