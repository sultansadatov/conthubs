"""Chart write/read services: the daily UPSERT, rank-movement back-fill and the
cached "latest available date" resolver used by the Chart API.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from podcasts.constants import ChartType

from .models import ChartEntry

log = logging.getLogger("charts.services")

_CONFLICT_KEY = ["date", "source", "country", "category", "chart_type", "rank"]
_UPDATE_FIELDS = [
    "previous_rank",
    "rank_change",
    "podcast",
    "episode",
    "title",
    "publisher",
    "image_url",
    "external_url",
    "external_id",
    "scraped_at",
]

_LATEST_DATE_CACHE = "charts:latest-date:{source}:{country}:{category}:{chart_type}"


def _slice_key(*, source, country, category, chart_type) -> dict:
    return dict(source=source, country=country, category=category, chart_type=chart_type)


@transaction.atomic
def upsert_chart_entries(rows: Iterable[dict]) -> dict:
    """Idempotently UPSERT a day's worth of chart rows.

    Each ``row`` must carry the six key columns plus a snapshot; ``podcast`` /
    ``episode`` (model instances or ids) are optional. Re-running the same scrape
    updates in place instead of duplicating (TT §3).
    """
    rows = list(rows)
    if not rows:
        return {"received": 0, "written": 0}

    objs: list[ChartEntry] = []
    for r in rows:
        objs.append(
            ChartEntry(
                date=r["date"],
                source=r["source"],
                country=r["country"],
                category=r["category"],
                chart_type=r.get("chart_type", ChartType.PODCASTS),
                rank=r["rank"],
                previous_rank=r.get("previous_rank"),
                rank_change=r.get("rank_change"),
                podcast_id=_fk_id(r.get("podcast")),
                episode_id=_fk_id(r.get("episode")),
                title=(r.get("title") or "")[:500],
                publisher=(r.get("publisher") or "")[:300],
                image_url=(r.get("image_url") or "")[:1000],
                external_url=(r.get("external_url") or "")[:1000],
                external_id=(r.get("external_id") or "")[:200],
                scraped_at=r.get("scraped_at") or timezone.now(),
            )
        )

    ChartEntry.objects.bulk_create(
        objs,
        update_conflicts=True,
        unique_fields=_CONFLICT_KEY,
        update_fields=_UPDATE_FIELDS,
        batch_size=1000,
    )

    # invalidate the "latest date" cache for every slice we just touched —
    # only after the write actually commits.
    keys = {
        _LATEST_DATE_CACHE.format(
            source=o.source, country=o.country, category=o.category, chart_type=o.chart_type
        )
        for o in objs
    }
    transaction.on_commit(lambda: cache.delete_many(list(keys)))

    return {"received": len(rows), "written": len(objs)}


def _fk_id(value):
    if value is None:
        return None
    return getattr(value, "pk", value)


def previous_chart_date(*, source, country, category, chart_type, before: dt.date) -> dt.date | None:
    return (
        ChartEntry.objects.filter(
            **_slice_key(source=source, country=country, category=category, chart_type=chart_type),
            date__lt=before,
        )
        .aggregate(d=Max("date"))["d"]
    )


def backfill_rank_movement(*, date, source, country, category, chart_type) -> int:
    """Fill ``previous_rank`` / ``rank_change`` for one freshly-scraped chart
    slice by joining to the previous snapshot of the same slice.

    Entity identity is matched on ``external_id`` (the stable scrape-time id,
    populated for both sources) and, only for rows that lack one, on the
    resolved ``podcast_id`` / ``episode_id``. Each match uses ``MIN(rank)`` of
    the previous snapshot so the result is deterministic even if a scrape ever
    lists the same entity twice.
    """
    prev_date = previous_chart_date(
        source=source, country=country, category=category, chart_type=chart_type, before=date
    )
    if not prev_date:
        return 0

    params = {
        "date": date, "source": source, "country": country,
        "category": category, "chart_type": chart_type, "prev_date": prev_date,
    }

    if connection.vendor == "postgresql":
        base_prev = (
            "SELECT {key} AS k, MIN(rank) AS prev_rank FROM charts_chartentry "
            "WHERE date = %(prev_date)s AND source = %(source)s AND country = %(country)s "
            "AND category = %(category)s AND chart_type = %(chart_type)s AND {non_null} "
            "GROUP BY {key}"
        )
        passes = [
            # 1) by scrape-time external id (the common path)
            (
                base_prev.format(key="external_id", non_null="external_id <> ''"),
                "cur.external_id <> '' AND cur.external_id = sub.k",
            ),
            # 2) fallbacks for rows with no external id, only if still unfilled
            (
                base_prev.format(key="podcast_id", non_null="podcast_id IS NOT NULL"),
                "cur.external_id = '' AND cur.previous_rank IS NULL AND cur.podcast_id = sub.k",
            ),
            (
                base_prev.format(key="episode_id", non_null="episode_id IS NOT NULL"),
                "cur.external_id = '' AND cur.previous_rank IS NULL AND cur.episode_id = sub.k",
            ),
        ]
        touched = 0
        with connection.cursor() as cur:
            for prev_select, match in passes:
                cur.execute(
                    f"""
                    UPDATE charts_chartentry AS cur
                       SET previous_rank = sub.prev_rank,
                           rank_change   = sub.prev_rank - cur.rank
                      FROM ({prev_select}) AS sub
                     WHERE cur.date = %(date)s AND cur.source = %(source)s
                       AND cur.country = %(country)s AND cur.category = %(category)s
                       AND cur.chart_type = %(chart_type)s AND {match}
                    """,
                    params,
                )
                touched += cur.rowcount
        return touched

    # portable (SQLite) fallback
    slice_filter = _slice_key(source=source, country=country, category=category, chart_type=chart_type)

    def _min_map(attr):
        out: dict = {}
        for e in ChartEntry.objects.filter(**slice_filter, date=prev_date):
            k = getattr(e, attr)
            if k not in (None, ""):
                out[k] = min(e.rank, out.get(k, e.rank))
        return out

    prev_by_ext = _min_map("external_id")
    prev_by_pod = _min_map("podcast_id")
    prev_by_epi = _min_map("episode_id")

    updated = []
    for e in ChartEntry.objects.filter(**slice_filter, date=date):
        if e.external_id:
            prev_rank = prev_by_ext.get(e.external_id)
        else:
            prev_rank = prev_by_pod.get(e.podcast_id) or prev_by_epi.get(e.episode_id)
        if prev_rank:
            e.previous_rank = prev_rank
            e.rank_change = prev_rank - e.rank
            updated.append(e)
    if updated:
        ChartEntry.objects.bulk_update(updated, ["previous_rank", "rank_change"], batch_size=500)
    return len(updated)


def latest_chart_date(*, source, country, category, chart_type) -> dt.date | None:
    """Most recent snapshot date for a chart slice, cached briefly so the Chart
    API's default ``date`` resolution stays O(1) (TT §3 low-latency)."""
    key = _LATEST_DATE_CACHE.format(
        source=source, country=country, category=category, chart_type=chart_type
    )
    cached = cache.get(key)
    if cached is not None:
        return dt.date.fromisoformat(cached) if cached else None

    value = (
        ChartEntry.objects.filter(
            **_slice_key(source=source, country=country, category=category, chart_type=chart_type)
        )
        .aggregate(d=Max("date"))["d"]
    )
    cache.set(key, value.isoformat() if value else "", settings.CHARTS["LATEST_DATE_CACHE_TTL"])
    return value
