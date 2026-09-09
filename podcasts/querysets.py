"""Custom querysets / managers for the podcast domain.

These keep the "which podcasts need work" logic in one place so the Celery
beat tasks and management commands stay thin.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .constants import EnrichmentStatus


class PodcastQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def needs_enrichment(self):
        """Podcasts that were never fully enriched, previously failed, or whose
        metadata has gone stale (TT §2.2)."""
        ttl_days = settings.INGESTION["ENRICHMENT_TTL_DAYS"]
        stale_before = timezone.now() - timedelta(days=ttl_days)
        return self.active().filter(
            Q(enrichment_status__in=[EnrichmentStatus.PENDING, EnrichmentStatus.FAILED, EnrichmentStatus.PARTIAL])
            | Q(enriched_at__isnull=True)
            | Q(enriched_at__lt=stale_before)
        )

    def needs_episode_sync(self):
        """Podcasts whose feed has not been polled within the configured
        minimum interval (TT §2.3)."""
        min_interval = settings.INGESTION["EPISODE_REFRESH_MIN_INTERVAL_HOURS"]
        due_before = timezone.now() - timedelta(hours=min_interval)
        return self.active().filter(
            Q(episodes_synced_at__isnull=True) | Q(episodes_synced_at__lt=due_before)
        ).exclude(rss_url="", podcastindex_id="")

    def with_feed(self):
        return self.exclude(rss_url="")

    def search(self, term: str):
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            Q(title__icontains=term)
            | Q(publisher__icontains=term)
            | Q(author__icontains=term)
            | Q(description__icontains=term)
        )
