"""Sync episodes synchronously (TT §2.3).

    python manage.py sync_episodes --limit 100
    python manage.py sync_episodes --ids 1,2,3 --force
    python manage.py sync_episodes --all
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ingestion.episodes import sync_podcast_episodes
from podcasts.models import Podcast


class Command(BaseCommand):
    help = "Fetch new episodes from RSS / PodcastIndex and UPSERT them."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--ids", help="comma list of podcast ids")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--force", action="store_true", help="ignore conditional-GET / min interval")

    def handle(self, *args, **o):
        if o["ids"]:
            qs = Podcast.objects.filter(pk__in=[int(x) for x in o["ids"].split(",") if x.strip()])
        elif o["all"]:
            qs = Podcast.objects.needs_episode_sync()
        else:
            qs = Podcast.objects.needs_episode_sync().order_by("episodes_synced_at", "id")[: o["limit"]]

        created = updated = 0
        n = 0
        for podcast in qs.iterator(chunk_size=50):
            res = sync_podcast_episodes(podcast, force=o["force"])
            created += res.get("created", 0)
            updated += res.get("updated", 0)
            n += 1
            self.stdout.write(
                f"  #{podcast.pk} {podcast.title[:55]:<55} +{res.get('created', 0)} new / "
                f"{res.get('updated', 0)} upd  (total {res.get('total_episodes', '?')})"
            )
        self.stdout.write(
            self.style.SUCCESS(f"Synced {n} podcast(s): {created} new, {updated} updated episode(s).")
        )
