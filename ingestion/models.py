"""Operational audit trail for ingestion.

One :class:`ScrapeRun` row per scrape/enrichment/episode batch, so operators can
see at a glance that "the system collects data daily" (TT acceptance cri#1) and
diagnose a bad run.
"""
from __future__ import annotations

import contextlib
import traceback

from django.db import models
from django.utils import timezone

from podcasts.constants import Source


class ScrapeRun(models.Model):
    class Kind(models.TextChoices):
        CHARTS = "charts", "Chart scrape"
        EPISODES = "episodes", "Episode sync"
        ENRICHMENT = "enrichment", "Enrichment"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    source = models.CharField(max_length=20, choices=Source.choices)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CHARTS)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING, db_index=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_written = models.PositiveIntegerField(default=0)
    slices_ok = models.PositiveIntegerField(default=0)
    slices_failed = models.PositiveIntegerField(default=0)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["source", "kind", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.source} · {self.started_at:%Y-%m-%d %H:%M} · {self.status}"

    @property
    def duration_seconds(self) -> float | None:
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@contextlib.contextmanager
def record_run(source: str, kind: str = ScrapeRun.Kind.CHARTS):
    """Wrap a scrape; the body sets ``run.stats`` / counters, exceptions are
    captured and re-raised."""
    run = ScrapeRun.objects.create(source=source, kind=kind)
    try:
        yield run
    except Exception as exc:  # noqa: BLE001 - we record then re-raise
        run.status = ScrapeRun.Status.FAILED
        run.error = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[:8000]
        run.finished_at = timezone.now()
        run.save()
        raise
    else:
        if run.status == ScrapeRun.Status.RUNNING:
            if run.slices_failed and not run.slices_ok:
                run.status = ScrapeRun.Status.FAILED
            elif run.slices_failed:
                run.status = ScrapeRun.Status.PARTIAL
            else:
                run.status = ScrapeRun.Status.SUCCESS
        run.finished_at = timezone.now()
        run.save()
