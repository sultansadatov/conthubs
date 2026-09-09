"""High-level ingestion orchestration.

``scrape -> resolve entities -> UPSERT chart rows -> back-fill rank movement``,
returning the set of podcast ids that now need enrichment / an episode sync (the
Celery layer fans those out; keeping it out of here avoids an import cycle).
"""
from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from charts.services import backfill_rank_movement, upsert_chart_entries
from podcasts.constants import ChartType, EnrichmentStatus, Source
from podcasts.models import Podcast
from podcasts.services import resolve_podcast, upsert_episodes

from .models import ScrapeRun, record_run
from .scrapers.base import ChartRow, ChartScrapeResult
from .scrapers.podchaser import PodchaserChartScraper
from .scrapers.spotify import SpotifyChartScraper

log = logging.getLogger("ingestion.pipeline")

# scrape-time identifiers we can batch-resolve a slice on (podcastindex_* only
# ever come from enrichment, so they are not useful here)
_SCRAPE_ID_FIELDS = ("rss_url", "apple_id", "podchaser_id", "spotify_id")


class IngestSummary(dict):
    """Plain dict used to accumulate a source-wide run summary."""


def _chart_date(result: ChartScrapeResult) -> dt.date:
    return timezone.localtime(result.fetched_at).date()


def ingest_chart_result(result: ChartScrapeResult) -> dict:
    """Persist one scraped chart slice. Idempotent."""
    date = _chart_date(result)
    resolved = _bulk_resolve_shows(result.rows, result.source)

    rows_out: list[dict] = []
    enrich_ids: set[int] = set()
    episode_sync_ids: set[int] = set()
    created_podcasts = 0

    for row in result.rows:
        podcast, was_created = resolved.get(id(row), (None, False))
        episode = None

        if podcast is not None:
            if was_created:
                created_podcasts += 1
            if was_created or podcast.enrichment_status in (
                EnrichmentStatus.PENDING, EnrichmentStatus.FAILED
            ):
                enrich_ids.add(podcast.pk)
            episode_sync_ids.add(podcast.pk)

            if result.chart_type == ChartType.EPISODES:
                episode = _resolve_episode(podcast, row)

        rows_out.append(
            {
                "date": date,
                "source": result.source,
                "country": result.country,
                "category": result.category,
                "chart_type": result.chart_type,
                "rank": row.rank,
                "previous_rank": row.previous_rank,
                "podcast": podcast,
                "episode": episode,
                "title": row.title or row.episode_title,
                "publisher": row.publisher,
                "image_url": row.image_url,
                "external_url": row.external_url,
                "external_id": row.external_id,
                "scraped_at": result.fetched_at,
            }
        )

    write = upsert_chart_entries(rows_out)
    moved = backfill_rank_movement(
        date=date,
        source=result.source,
        country=result.country,
        category=result.category,
        chart_type=result.chart_type,
    )

    summary = {
        "source": result.source,
        "country": result.country,
        "category": result.category,
        "chart_type": result.chart_type,
        "date": date.isoformat(),
        "rows": len(result.rows),
        "written": write["written"],
        "new_podcasts": created_podcasts,
        "rank_movement_filled": moved,
        "enrich_ids": sorted(enrich_ids),
        "episode_sync_ids": sorted(episode_sync_ids),
    }
    log.info("ingested chart %s/%s/%s %s: %s rows", result.source, result.country,
             result.category, date, write["written"])
    return summary


def _bulk_resolve_shows(rows: list[ChartRow], source: str) -> dict[int, tuple[Podcast, bool]]:
    """Resolve every row of a slice to a :class:`Podcast` with as few queries as
    possible: one bulk SELECT for the shows we already have, then a single
    ``resolve_podcast`` call per genuinely-new show (which also covers the
    title/publisher fallback and the concurrent-insert race).
    """
    # 1) collect the scrape-time ids present in this slice
    wanted: dict[str, set[str]] = {f: set() for f in _SCRAPE_ID_FIELDS}
    for row in rows:
        for f in _SCRAPE_ID_FIELDS:
            v = (getattr(row, f, "") or "").strip()
            if v:
                wanted[f].add(v)

    # 2) one query to fetch every podcast matching any of those ids
    maps: dict[str, dict[str, Podcast]] = {f: {} for f in _SCRAPE_ID_FIELDS}
    q = Q()
    for f, values in wanted.items():
        if values:
            q |= Q(**{f"{f}__in": values})
    if q:
        for p in Podcast.objects.filter(q):
            for f in _SCRAPE_ID_FIELDS:
                val = getattr(p, f)
                if val:
                    maps[f].setdefault(val, p)

    out: dict[int, tuple[Podcast, bool]] = {}
    to_backfill: dict[int, Podcast] = {}

    for row in rows:
        title = row.title or row.episode_title
        if not title:
            continue

        podcast = None
        for f in _SCRAPE_ID_FIELDS:
            v = (getattr(row, f, "") or "").strip()
            if v and v in maps[f]:
                podcast = maps[f][v]
                break

        if podcast is not None:
            # only touch safe, non-unique fields in the fast path; cross-source
            # id linking is done carefully by the enrichment orchestrator.
            if _backfill_cosmetic(podcast, row):
                to_backfill[podcast.pk] = podcast
            out[id(row)] = (podcast, False)
            continue

        # unknown by id -> the single-item path (match_key fallback + create)
        res = resolve_podcast(
            title=title,
            publisher=row.publisher,
            source=source,
            rss_url=row.rss_url,
            apple_id=row.apple_id,
            spotify_id=row.spotify_id,
            podchaser_id=row.podchaser_id,
            defaults={"image_url": row.image_url, "description": row.description},
        )
        out[id(row)] = (res.podcast, res.created)
        for f in _SCRAPE_ID_FIELDS:  # so a later row with the same id reuses it
            val = getattr(res.podcast, f)
            if val:
                maps[f].setdefault(val, res.podcast)

    if to_backfill:
        Podcast.objects.bulk_update(
            list(to_backfill.values()),
            ["image_url", "description", "updated_at"],
            batch_size=200,
        )
    return out


def _backfill_cosmetic(podcast: Podcast, row: ChartRow) -> bool:
    """Fill blank ``image_url`` / ``description`` on an already-known podcast
    from a fresh chart row. Returns True if anything changed."""
    changed = False
    if row.image_url and not podcast.image_url:
        podcast.image_url = row.image_url[:1000]
        changed = True
    if row.description and not podcast.description:
        podcast.description = row.description
        changed = True
    if changed:
        podcast.updated_at = timezone.now()
    return changed


def _resolve_episode(podcast: Podcast, row: ChartRow):
    entry = {
        "guid": row.episode_guid or row.external_id,
        "title": row.episode_title or row.title,
        "audio_url": row.episode_audio_url,
        "published_at": row.episode_published_at,
        "image_url": row.image_url,
        "external_ids": {"spotify": row.external_id} if row.external_id else {},
        "source": Source.SPOTIFY,
    }
    upsert_episodes(podcast, [entry])
    return (
        podcast.episodes.filter(guid=entry["guid"]).first()
        if entry["guid"]
        else podcast.episodes.order_by("-id").first()
    )


# ---------------------------------------------------------------------------
#  source runners
# ---------------------------------------------------------------------------
def _run_scrape(*, source: str, scraper, slice_iter) -> dict:
    """Shared driver for both sources. ``slice_iter`` yields ``(label, kwargs)``
    pairs; ``scraper.scrape(**kwargs)`` returns a :class:`ChartScrapeResult`."""
    out = IngestSummary(source=source, slices=[], enrich_ids=set(), episode_sync_ids=set())
    with record_run(source, ScrapeRun.Kind.CHARTS) as run:
        try:
            for label, kwargs in slice_iter:
                try:
                    result = scraper.scrape(**kwargs)
                    if not result.rows:
                        log.warning("%s %s returned 0 rows", source, label)
                        run.slices_failed += 1
                        continue
                    s = ingest_chart_result(result)
                    out["slices"].append(s)
                    out["enrich_ids"].update(s["enrich_ids"])
                    out["episode_sync_ids"].update(s["episode_sync_ids"])
                    run.rows_written += s["written"]
                    run.slices_ok += 1
                except Exception:
                    log.exception("%s scrape failed for %s", source, label)
                    run.slices_failed += 1
        finally:
            scraper.close()
        out["enrich_ids"] = sorted(out["enrich_ids"])
        out["episode_sync_ids"] = sorted(out["episode_sync_ids"])
        run.stats = {
            "slices": len(out["slices"]),
            "enrich_queued": len(out["enrich_ids"]),
            "episode_sync_queued": len(out["episode_sync_ids"]),
        }
        out["scrape_run_id"] = run.pk
    return out


def run_spotify_scrape(
    *, regions: list[str] | None = None, categories: list[str] | None = None
) -> dict:
    regions = regions or settings.SPOTIFY_CHART_REGIONS
    categories = categories or settings.SPOTIFY_CHART_CATEGORIES
    slices = [
        (f"{category}/{region}", {"region": region, "category": category})
        for region in regions
        for category in categories
    ]
    return _run_scrape(source="spotify", scraper=SpotifyChartScraper(), slice_iter=slices)


def run_podchaser_scrape(*, countries: list[str] | None = None, categories: list[str] | None = None) -> dict:
    countries = countries or settings.PODCHASER_CHART_COUNTRIES
    categories = categories or settings.PODCHASER_CHART_CATEGORIES
    slices = [
        (f"{category}/{country}", {"country": country, "category": category})
        for country in countries
        for category in categories
    ]
    return _run_scrape(source="podchaser", scraper=PodchaserChartScraper(), slice_iter=slices)
