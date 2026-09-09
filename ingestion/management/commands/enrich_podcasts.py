"""Enrich podcasts synchronously (TT §2.2).

    python manage.py enrich_podcasts --limit 50
    python manage.py enrich_podcasts --ids 1,2,3 --force
    python manage.py enrich_podcasts --all --providers apple,podcastindex
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ingestion.enrichment import enrich_podcast
from podcasts.models import Podcast


class Command(BaseCommand):
    help = "Run metadata enrichment (Apple / PodcastIndex / Podchaser) on podcasts."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--ids", help="comma list of podcast ids")
        parser.add_argument("--all", action="store_true", help="every podcast that needs it")
        parser.add_argument("--force", action="store_true", help="re-enrich even if fresh")
        parser.add_argument("--providers", help="comma list, overrides settings order")

    def handle(self, *args, **o):
        providers = [p.strip() for p in o["providers"].split(",")] if o["providers"] else None

        if o["ids"]:
            qs = Podcast.objects.filter(pk__in=[int(x) for x in o["ids"].split(",") if x.strip()])
        elif o["all"]:
            qs = Podcast.objects.needs_enrichment()
        else:
            qs = Podcast.objects.needs_enrichment().order_by("enriched_at", "id")[: o["limit"]]

        total = qs.count() if o["all"] or o["ids"] else min(o["limit"], qs.count())
        self.stdout.write(f"Enriching {total} podcast(s) …")
        done = {"done": 0, "partial": 0, "failed": 0, "missing": 0}
        for podcast in qs.iterator(chunk_size=50):
            res = enrich_podcast(podcast, providers=providers, force=o["force"])
            done[res["status"]] = done.get(res["status"], 0) + 1
            self.stdout.write(
                f"  #{podcast.pk} {podcast.title[:60]:<60} {res['status']:<8} "
                f"({', '.join(res['sources']) or 'no sources'})"
            )
        self.stdout.write(self.style.SUCCESS(f"Finished: {done}"))
