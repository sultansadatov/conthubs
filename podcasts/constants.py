"""Shared enumerations for the podcast domain."""
from __future__ import annotations

from django.db import models


class Source(models.TextChoices):
    """Where a piece of data originated. Used by charts, categories and
    enrichment bookkeeping alike."""

    SPOTIFY = "spotify", "Spotify"
    PODCHASER = "podchaser", "Podchaser"
    APPLE = "apple", "Apple Podcasts"
    PODCASTINDEX = "podcastindex", "PodcastIndex"
    RSS = "rss", "RSS feed"
    INTERNAL = "internal", "Internal"


class ChartType(models.TextChoices):
    PODCASTS = "podcasts", "Podcasts"
    EPISODES = "episodes", "Episodes"


class EnrichmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PARTIAL = "partial", "Partially enriched"
    DONE = "done", "Enriched"
    FAILED = "failed", "Failed"


class PublishFrequency(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    BIWEEKLY = "biweekly", "Every two weeks"
    MONTHLY = "monthly", "Monthly"
    SPORADIC = "sporadic", "Sporadic"
    UNKNOWN = "", "Unknown"
