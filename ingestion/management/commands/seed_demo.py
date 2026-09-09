"""Seed a realistic, self-contained dataset for demoing / reviewing the API.

Uses only public, key-less endpoints (iTunes lookup + the podcasts' own RSS
feeds), then synthesises two days of chart history so rank-movement and the
``date`` parameter can be exercised.

    python manage.py seed_demo
    python manage.py seed_demo --no-episodes         # metadata + charts only (fast)
    python manage.py seed_demo --days 5
"""
from __future__ import annotations

import datetime as dt
import random

from django.core.management.base import BaseCommand
from django.utils import timezone

from charts.services import backfill_rank_movement, upsert_chart_entries
from ingestion.enrichment import enrich_podcast
from ingestion.episodes import sync_podcast_episodes
from podcasts.constants import ChartType, Source
from podcasts.services import resolve_podcast

# (Apple collectionId, fallback title) — stable, well-known shows
DEMO_SHOWS = [
    ("1200361736", "The Daily"),
    ("201671138", "This American Life"),
    ("152249110", "Radiolab"),
    ("354668519", "Freakonomics Radio"),
    ("160904630", "TED Talks Daily"),
    ("290783428", "Planet Money"),
    ("278981407", "Stuff You Should Know"),
    ("1322200189", "Crime Junkie"),
    ("173001861", "Dan Carlin's Hardcore History"),
    ("1028908750", "Up First from NPR"),
    ("1119389968", "The Tim Ferriss Show"),
    ("1212558767", "Wait Wait... Don't Tell Me!"),
]

DEMO_COUNTRY = "us"
DEMO_CATEGORY = "top-podcasts"


class Command(BaseCommand):
    help = "Seed demo podcasts, enrich them, sync episodes and synthesise chart history."

    def add_arguments(self, parser):
        parser.add_argument("--no-episodes", action="store_true", help="skip RSS episode sync")
        parser.add_argument("--days", type=int, default=3, help="days of chart history to synthesise")

    def handle(self, *args, **o):
        podcasts = []
        self.stdout.write(self.style.MIGRATE_HEADING("1) Resolve + enrich demo podcasts"))
        for apple_id, title in DEMO_SHOWS:
            res = resolve_podcast(title=title, apple_id=apple_id, source=Source.APPLE)
            podcast = res.podcast
            try:
                enrich_podcast(podcast, providers=["apple", "podcastindex", "podchaser"])
            except Exception as exc:  # keep going even if one lookup hiccups
                self.stderr.write(f"   enrich {title} failed: {exc}")
            podcast.refresh_from_db()
            podcasts.append(podcast)
            self.stdout.write(
                f"   #{podcast.pk:<4} {podcast.title[:48]:<48} "
                f"rss={'yes' if podcast.rss_url else 'no ':<3} status={podcast.enrichment_status}"
            )

        if not o["no_episodes"]:
            self.stdout.write(self.style.MIGRATE_HEADING("\n2) Sync episodes from RSS"))
            for podcast in podcasts:
                if not podcast.rss_url:
                    continue
                try:
                    r = sync_podcast_episodes(podcast, force=True)
                    self.stdout.write(f"   {podcast.title[:48]:<48} {r['created']} new / {r['total_episodes']} total")
                except Exception as exc:
                    self.stderr.write(f"   episodes {podcast.title} failed: {exc}")

        self.stdout.write(self.style.MIGRATE_HEADING(f"\n3) Synthesise {o['days']} day(s) of chart history"))
        today = timezone.localdate()
        order = list(podcasts)
        for day_offset in range(o["days"] - 1, -1, -1):
            date = today - dt.timedelta(days=day_offset)
            rng = random.Random(date.toordinal())
            shuffled = order[:]
            # small local shuffle so ranks move a little day to day
            for i in range(len(shuffled)):
                j = min(len(shuffled) - 1, max(0, i + rng.choice([-1, 0, 0, 1])))
                shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
            rows = [
                {
                    "date": date,
                    "source": Source.SPOTIFY,
                    "country": DEMO_COUNTRY,
                    "category": DEMO_CATEGORY,
                    "chart_type": ChartType.PODCASTS,
                    "rank": rank,
                    "podcast": p,
                    "title": p.title,
                    "publisher": p.publisher,
                    "image_url": p.image_url,
                    "external_id": p.apple_id or f"demo:{p.pk}",
                    "scraped_at": timezone.now(),
                }
                for rank, p in enumerate(shuffled, start=1)
            ]
            written = upsert_chart_entries(rows)["written"]
            moved = backfill_rank_movement(
                date=date,
                source=Source.SPOTIFY,
                country=DEMO_COUNTRY,
                category=DEMO_CATEGORY,
                chart_type=ChartType.PODCASTS,
            )
            self.stdout.write(f"   {date}: {written} rows, {moved} rank-movement filled")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Try:\n"
                f"  GET /api/v1/charts?source=spotify&country={DEMO_COUNTRY}&category={DEMO_CATEGORY}\n"
                f"  GET /api/v1/podcasts?search=radiolab\n"
                f"  GET /api/v1/podcasts/{podcasts[0].pk}\n"
                f"  GET /api/v1/podcasts/{podcasts[0].pk}/episodes"
            )
        )
