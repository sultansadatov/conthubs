"""Maintenance Celery tasks for the charts app."""
from __future__ import annotations

import logging

from celery import shared_task

from .partitions import ensure_partitions

log = logging.getLogger("charts.tasks")


@shared_task(name="charts.tasks.ensure_chart_partitions")
def ensure_chart_partitions(months_ahead: int | None = None) -> dict:
    """Pre-create the coming months' chart partitions (TT §3 Table Partitioning).

    Safe to run anytime; it only ever *adds* partitions.
    """
    created = ensure_partitions(months_ahead=months_ahead)
    if created:
        log.info("ensure_chart_partitions created: %s", ", ".join(created))
    return {"created": created, "count": len(created)}
