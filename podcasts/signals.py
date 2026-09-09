"""Signal handlers that keep a podcast's denormalised aggregates consistent when
a single :class:`~podcasts.models.Episode` is changed **outside** the ingestion
pipeline (e.g. a hand edit in the Django admin).

The pipeline's bulk path (``bulk_create``/``bulk_update``) does not emit these
signals by design — it calls ``recompute_podcast_aggregates`` itself, and the
nightly ``ingestion.tasks.refresh_podcast_aggregates`` is the reconciliation
backstop.
"""
from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Episode


@receiver(post_save, sender=Episode)
def _episode_saved(sender, instance: Episode, created, raw, **kwargs):
    if raw:  # loading fixtures
        return
    podcast = instance.podcast
    changed = False
    if instance.published_at and (
        podcast.last_published_at is None or instance.published_at > podcast.last_published_at
    ):
        podcast.last_published_at = instance.published_at
        changed = True
    if created:
        podcast.total_episodes = podcast.episodes.count()
        changed = True
    if changed:
        podcast.save(update_fields=["last_published_at", "total_episodes", "updated_at"])


@receiver(post_delete, sender=Episode)
def _episode_deleted(sender, instance: Episode, **kwargs):
    try:
        podcast = instance.podcast
    except Exception:  # podcast already gone (cascade)
        return
    from .services import recompute_podcast_aggregates

    recompute_podcast_aggregates(podcast)
