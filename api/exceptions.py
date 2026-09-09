"""Uniform error envelope for the v1 API.

Every handled error comes back as::

    {"error": {"status": 400, "code": "validation_error", "detail": ...}}
"""
from __future__ import annotations

from django.http import Http404
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class ChartNotAvailable(APIException):
    status_code = 404
    default_detail = "No chart data is available for the requested parameters."
    default_code = "chart_not_available"


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, "default_code", None) or getattr(exc, "code", None)
    if isinstance(exc, Http404):
        code = "not_found"

    detail = response.data
    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        detail = detail["detail"]

    response.data = {
        "error": {
            "status": response.status_code,
            "code": code or "error",
            "detail": detail,
        }
    }
    return response
