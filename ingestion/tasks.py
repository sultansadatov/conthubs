"""Celery tasks — the scheduled entry points for the whole pipeline.

Scheduling lives in ``myproject/celery.py``; this module only defines behaviour.
Batch tasks fan out one child task per podcast so a single slow feed cannot
stall the run and failures are isolated & retried individually.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

log = logging.getLogger("ingestion.tasks")

_DEFAULT_RETRY = dict(
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_backoff_max=1800,
    retry_jitter=True,
    max_retries=5,
)


# ---------------------------------------------------------------------------
#  TT §2.1 — daily chart scraping
# ---------------------------------------------------------------------------
@shared_task(name="ingestion.tasks.scrape_spotify_charts", **_DEFAULT_RETRY)
def scrape_spotify_charts(regions: list[str] | None = None, categories: list[str] | None = None) -> dict:
    from .pipeline import run_spotify_scrape

    summary = run_spotify_scrape(regions=regions, categories=categories)
    _fan_out_post_scrape(summary)
    return _trim(summary)


@shared_task(name="ingestion.tasks.scrape_podchaser_charts", **_DEFAULT_RETRY)
def scrape_podchaser_charts(countries: list[str] | None = None, categories: list[str] | None = None) -> dict:
    from .pipeline import run_podchaser_scrape

    summary = run_podchaser_scrape(countries=countries, categories=categories)
    _fan_out_post_scrape(summary)
    return _trim(summary)


def _fan_out_post_scrape(summary: dict) -> None:
    """Queue immediate follow-up work for the shows a scrape just touched:
    enrichment for new/pending ones, and an episode sync only for feeds that are
    actually due (the 6-hourly / nightly beat tasks handle the rest)."""
    from podcasts.models import Podcast

    enrich_cap = settings.INGESTION["ENRICH_BATCH_SIZE"] * 5
    for pid in summary.get("enrich_ids", [])[:enrich_cap]:
        enrich_podcast.delay(pid)

    charting = summary.get("episode_sync_ids", [])
    sync_cap = settings.INGESTION["EPISODE_SYNC_BATCH_SIZE"] * 5
    due = (
        Podcast.objects.filter(pk__in=charting[: sync_cap * 4])
        .needs_episode_sync()
        .values_list("id", flat=True)[:sync_cap]
    )
    for pid in due:
        sync_podcast_episodes.delay(pid)


# ---------------------------------------------------------------------------
#  TT §2.2 — enrichment
# ---------------------------------------------------------------------------
@shared_task(name="ingestion.tasks.enrich_podcast", **_DEFAULT_RETRY)
def enrich_podcast(podcast_id: int, force: bool = False) -> dict:
    from podcasts.models import Podcast

    from .enrichment import enrich_podcast as _enrich

    try:
        podcast = Podcast.objects.get(pk=podcast_id)
    except Podcast.DoesNotExist:
        return {"podcast_id": podcast_id, "status": "missing"}
    return _enrich(podcast, force=force)


@shared_task(name="ingestion.tasks.enrich_pending_podcasts")
def enrich_pending_podcasts(limit: int | None = None) -> dict:
    from podcasts.models import Podcast

    limit = limit or settings.INGESTION["ENRICH_BATCH_SIZE"]
    ids = list(
        Podcast.objects.needs_enrichment()
        .order_by("enriched_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    for pid in ids:
        enrich_podcast.delay(pid)
    log.info("enrich_pending_podcasts queued %d", len(ids))
    return {"queued": len(ids)}


# ---------------------------------------------------------------------------
#  TT §2.3 — episode discovery / sync
# ---------------------------------------------------------------------------
@shared_task(name="ingestion.tasks.sync_podcast_episodes", **_DEFAULT_RETRY)
def sync_podcast_episodes(podcast_id: int, force: bool = False) -> dict:
    from podcasts.models import Podcast

    from .episodes import sync_podcast_episodes as _sync

    try:
        podcast = Podcast.objects.get(pk=podcast_id)
    except Podcast.DoesNotExist:
        return {"podcast_id": podcast_id, "status": "missing"}
    return _sync(podcast, force=force)


@shared_task(name="ingestion.tasks.sync_all_episodes")
def sync_all_episodes(limit: int | None = None) -> dict:
    from podcasts.models import Podcast

    limit = limit or settings.INGESTION["EPISODE_SYNC_BATCH_SIZE"]
    ids = list(
        Podcast.objects.needs_episode_sync()
        .order_by("episodes_synced_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    for pid in ids:
        sync_podcast_episodes.delay(pid)
    log.info("sync_all_episodes queued %d", len(ids))
    return {"queued": len(ids)}


@shared_task(name="ingestion.tasks.sync_episodes_for_recent_charts")
def sync_episodes_for_recent_charts(days: int = 2, limit: int = 500) -> dict:
    """Refresh feeds of podcasts that appeared on a chart in the last ``days``
    days — these change most often, so keep them fresh (TT §2.1 "fresh")."""
    from charts.models import ChartEntry
    from podcasts.models import Podcast

    since = timezone.now().date() - timedelta(days=days)
    ids = list(
        ChartEntry.objects.filter(date__gte=since, podcast__isnull=False)
        .values_list("podcast_id", flat=True)
        .distinct()[:limit]
    )
    # only those actually due, to avoid hammering feeds
    due = set(
        Podcast.objects.filter(pk__in=ids).needs_episode_sync().values_list("id", flat=True)
    )
    for pid in due:
        sync_podcast_episodes.delay(pid)
    return {"charting_podcasts": len(ids), "queued": len(due)}


# ---------------------------------------------------------------------------
#  maintenance
# ---------------------------------------------------------------------------
@shared_task(name="ingestion.tasks.refresh_podcast_aggregates")
def refresh_podcast_aggregates(since_days: int = 3, limit: int = 20000) -> dict:
    """Reconcile denormalised aggregates for podcasts whose episode set may have
    changed recently. Signals keep them fresh incrementally; this is the nightly
    backstop, scoped to recently-synced feeds so it stays cheap as the catalog
    grows."""
    from datetime import timedelta

    from podcasts.models import Podcast
    from podcasts.services import recompute_podcast_aggregates

    cutoff = timezone.now() - timedelta(days=since_days)
    ids = list(
        Podcast.objects.filter(episodes_synced_at__gte=cutoff)
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    )
    touched = 0
    for start in range(0, len(ids), 200):
        for podcast in Podcast.objects.filter(pk__in=ids[start : start + 200]):
            if recompute_podcast_aggregates(podcast)["changed"]:
                touched += 1
    return {"checked": len(ids), "updated": touched}


@shared_task(name="ingestion.tasks.cleanup_task_results")
def cleanup_task_results(days: int = 14) -> dict:
    try:
        from django_celery_results.models import TaskResult
    except ImportError:
        return {"deleted": 0}
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = TaskResult.objects.filter(date_done__lt=cutoff).delete()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def _trim(summary: dict) -> dict:
    """Keep the task result row small — drop the per-podcast id lists."""
    out = dict(summary)
    out["enrich_queued"] = len(out.pop("enrich_ids", []) or [])
    out["episode_sync_queued"] = len(out.pop("episode_sync_ids", []) or [])
    return out
