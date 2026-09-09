"""Run a chart scrape now, synchronously (what the daily Celery beat task does).

    python manage.py scrape_charts                      # both sources, configured slices
    python manage.py scrape_charts --source spotify --regions us,gb --categories top-podcasts
    python manage.py scrape_charts --source podchaser --countries us --categories comedy,news
    python manage.py scrape_charts --enqueue-followups  # also queue enrichment/episode tasks
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Scrape Spotify / Podchaser podcast charts and UPSERT them into chart history."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=["spotify", "podchaser", "all"], default="all")
        parser.add_argument("--regions", help="comma list of Spotify regions (overrides settings)")
        parser.add_argument("--countries", help="comma list of Podchaser countries")
        parser.add_argument(
            "--categories",
            help="comma list of category slugs (applies to whichever source runs)",
        )
        parser.add_argument(
            "--enqueue-followups",
            action="store_true",
            help="dispatch enrich_podcast / sync_podcast_episodes tasks for affected podcasts",
        )

    def handle(self, *args, **o):
        from ingestion.pipeline import run_podchaser_scrape, run_spotify_scrape

        csv = lambda v: [x.strip() for x in v.split(",") if x.strip()] if v else None  # noqa: E731
        results = []

        if o["source"] in ("spotify", "all"):
            self.stdout.write("→ Spotify charts …")
            results.append(run_spotify_scrape(regions=csv(o["regions"]), categories=csv(o["categories"])))
        if o["source"] in ("podchaser", "all"):
            self.stdout.write("→ Podchaser charts …")
            results.append(
                run_podchaser_scrape(
                    countries=csv(o["countries"]), categories=csv(o["categories"])
                )
            )

        for r in results:
            written = sum(s["written"] for s in r["slices"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {r['source']}: {len(r['slices'])} slice(s), {written} chart row(s), "
                    f"{len(r['enrich_ids'])} podcast(s) need enrichment"
                )
            )
            if o["enqueue_followups"]:
                from ingestion.tasks import enrich_podcast, sync_podcast_episodes

                for pid in r["enrich_ids"]:
                    enrich_podcast.delay(pid)
                for pid in r["episode_sync_ids"]:
                    sync_podcast_episodes.delay(pid)
                self.stdout.write("  queued follow-up tasks")

        self.stdout.write(json.dumps([_slim(r) for r in results], indent=2, default=str))


def _slim(r: dict) -> dict:
    return {
        "source": r["source"],
        "scrape_run_id": r.get("scrape_run_id"),
        "slices": len(r["slices"]),
        "rows_written": sum(s["written"] for s in r["slices"]),
        "new_podcasts": sum(s["new_podcasts"] for s in r["slices"]),
        "enrich_queued": len(r["enrich_ids"]),
        "episode_sync_queued": len(r["episode_sync_ids"]),
    }
