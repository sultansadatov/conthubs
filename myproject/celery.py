"""Celery application for the podcast aggregation platform.

Everything the platform does on a schedule (daily chart scrapes, metadata
enrichment, episode refresh, chart-partition maintenance) is a Celery task.
The periodic schedule lives here in code so it is versioned and reproducible;
``django_celery_beat``'s ``DatabaseScheduler`` picks these entries up on start
and they remain editable from the Django admin afterwards.
"""
from __future__ import absolute_import, unicode_literals

import logging
import os

from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger("celery")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

app = Celery("myproject")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ---------------------------------------------------------------------------
#  Periodic schedule  (times are in settings.TIME_ZONE = Asia/Baku)
# ---------------------------------------------------------------------------
app.conf.beat_schedule = {
    # --- TT §2.1  daily scraping of the two chart sources -------------------
    "scrape-spotify-charts-daily": {
        "task": "ingestion.tasks.scrape_spotify_charts",
        "schedule": crontab(hour=6, minute=0),
        "options": {"queue": "scraping", "expires": 60 * 60 * 6},
    },
    "scrape-podchaser-charts-daily": {
        "task": "ingestion.tasks.scrape_podchaser_charts",
        "schedule": crontab(hour=6, minute=30),
        "options": {"queue": "scraping", "expires": 60 * 60 * 6},
    },
    # --- TT §2.2  keep metadata enrichment moving through the backlog ------
    "enrich-pending-podcasts": {
        "task": "ingestion.tasks.enrich_pending_podcasts",
        "schedule": crontab(minute="*/20"),
        "options": {"queue": "enrichment"},
    },
    # --- TT §2.3  discover & store newly published episodes ---------------
    "sync-episodes-daily": {
        "task": "ingestion.tasks.sync_all_episodes",
        "schedule": crontab(hour=7, minute=0),
        "options": {"queue": "episodes", "expires": 60 * 60 * 10},
    },
    "sync-episodes-fresh-charting": {
        # charting podcasts change often — refresh their feeds every 6h
        "task": "ingestion.tasks.sync_episodes_for_recent_charts",
        "schedule": crontab(hour="*/6", minute=15),
        "options": {"queue": "episodes"},
    },
    # --- TT §3 scalability  maintain monthly chart partitions -------------
    # runs daily (not monthly) — the check is one cheap query when there is
    # nothing to create, and a missed run must never let inserts fall back to
    # the DEFAULT partition.
    "ensure-chart-partitions": {
        "task": "charts.tasks.ensure_chart_partitions",
        "schedule": crontab(hour=0, minute=10),
        "options": {"queue": "maintenance"},
    },
    # --- keep denormalised podcast aggregates honest ---------------------
    "refresh-podcast-aggregates": {
        "task": "ingestion.tasks.refresh_podcast_aggregates",
        "schedule": crontab(hour=3, minute=30),
        "options": {"queue": "maintenance"},
    },
    # --- housekeeping for stored celery results -------------------------
    "cleanup-expired-task-results": {
        "task": "ingestion.tasks.cleanup_task_results",
        "schedule": crontab(hour=4, minute=0),
        "options": {"queue": "maintenance"},
    },
}

# Route tasks to dedicated queues so a slow feed crawl never blocks the API's
# cache-warming or a chart scrape.
app.conf.task_routes = {
    "ingestion.tasks.scrape_*": {"queue": "scraping"},
    "ingestion.tasks.*charts*": {"queue": "scraping"},
    "ingestion.tasks.enrich_*": {"queue": "enrichment"},
    "ingestion.tasks.sync_*episode*": {"queue": "episodes"},
    "ingestion.tasks.*episode*": {"queue": "episodes"},
    "charts.tasks.*": {"queue": "maintenance"},
}


@app.task(bind=True)
def debug_task(self):  # pragma: no cover - operational helper
    logger.info("Request: %r", self.request)
