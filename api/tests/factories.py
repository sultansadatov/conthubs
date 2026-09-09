"""Tiny seed helpers for API tests."""
from __future__ import annotations

import datetime as dt

from django.utils import timezone

from charts.services import backfill_rank_movement, upsert_chart_entries
from podcasts.services import recompute_podcast_aggregates, resolve_podcast, upsert_episodes

SLICE = dict(source="spotify", country="us", category="top-podcasts", chart_type="podcasts")


def make_podcast(title, *, apple_id="", episodes=0, **fields):
    podcast = resolve_podcast(title=title, publisher=fields.pop("publisher", "Pub"), apple_id=apple_id).podcast
    for key, value in fields.items():
        setattr(podcast, key, value)
    if fields:
        podcast.save()
    if episodes:
        now = timezone.now()
        upsert_episodes(
            podcast,
            [
                {"guid": f"{title}-{i}", "title": f"{title} ep {i}",
                 "published_at": now - dt.timedelta(days=i), "audio_url": f"https://m/{title}{i}.mp3"}
                for i in range(episodes)
            ],
        )
        recompute_podcast_aggregates(podcast)
    return podcast


def make_chart(day, ranking, **slice_over):
    """``ranking`` = list of (rank, podcast)."""
    sl = {**SLICE, **slice_over}
    rows = [
        {
            **sl, "date": day, "rank": rank, "podcast": p, "title": p.title,
            "publisher": p.publisher, "image_url": p.image_url,
            "external_id": p.apple_id or f"x{p.pk}", "scraped_at": timezone.now(),
        }
        for rank, p in ranking
    ]
    upsert_chart_entries(rows)
    backfill_rank_movement(date=day, **sl)
