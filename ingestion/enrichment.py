"""Metadata enrichment orchestrator (TT §2.2).

Given a :class:`~podcasts.models.Podcast` we already know by *some* identifier,
walk the configured providers (Apple Podcasts -> PodcastIndex -> Podchaser by
default) and merge what they return:

* descriptive fields (description, image, publisher, language, website, categories,
  frequency hints) are **filled if empty** — the first provider to supply a value
  wins, later providers only backfill gaps;
* ratings (``rating_average`` / ``rating_count``) are **refreshed** every run
  from whichever provider has them (Podchaser);
* every provider's raw payload is kept under ``podcast.raw_metadata[provider]``
  so nothing is lost and re-processing is possible.

Providers with no credentials are skipped silently, so the pipeline still
produces enriched data from Apple alone.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from podcasts.constants import EnrichmentStatus, Source
from podcasts.models import Podcast
from podcasts.services import attach_categories

from .clients.apple import ApplePodcastsClient
from .clients.podcastindex import PodcastIndexClient
from .clients.podchaser import PodchaserClient

log = logging.getLogger("ingestion.enrichment")


class _Merge:
    """Accumulates field changes and reports which ones actually changed."""

    def __init__(self, podcast: Podcast):
        self.p = podcast
        self.changed: set[str] = set()

    def fill(self, field: str, value):
        value = _clean(value)
        if value and not getattr(self.p, field, None):
            setattr(self.p, field, value)
            self.changed.add(field)

    def set(self, field: str, value):
        if value is None:
            return
        if getattr(self.p, field, None) != value:
            setattr(self.p, field, value)
            self.changed.add(field)

    def set_id(self, field: str, value):
        value = _clean(value)
        if value and not getattr(self.p, field, ""):
            setattr(self.p, field, str(value))
            self.changed.add(field)


def enrich_podcast(podcast: Podcast, *, providers: list[str] | None = None, force: bool = False) -> dict:
    # skip the outbound API calls when metadata is already fresh (unless forced)
    if not force and podcast.enrichment_status == EnrichmentStatus.DONE and podcast.enriched_at:
        ttl = timedelta(days=settings.INGESTION["ENRICHMENT_TTL_DAYS"])
        if timezone.now() - podcast.enriched_at < ttl:
            return {
                "podcast_id": podcast.pk,
                "sources": podcast.enrichment_sources,
                "status": podcast.enrichment_status,
                "fields_changed": [],
                "skipped": "fresh",
            }

    order = providers or settings.ENRICHMENT_PROVIDER_ORDER
    merge = _Merge(podcast)
    used: list[str] = []
    categories: list[tuple[str, str]] = []
    raw = dict(podcast.raw_metadata or {})

    handlers = {
        "apple": _enrich_apple,
        "podcastindex": _enrich_podcastindex,
        "podchaser": _enrich_podchaser,
    }

    for name in order:
        handler = handlers.get(name)
        if handler is None:
            continue
        try:
            payload = handler(podcast, merge, categories)
        except Exception:  # one bad provider must not sink the run
            log.exception("enrichment provider %s crashed for podcast %s", name, podcast.pk)
            payload = None
        if payload:
            raw[name] = payload
            used.append(name)

    # bookkeeping
    podcast.raw_metadata = raw
    podcast.enrichment_sources = sorted(set(used))
    podcast.enriched_at = timezone.now()
    podcast.enrichment_status = _status(podcast, used, bool(categories))
    merge.changed.update({"raw_metadata", "enrichment_sources", "enriched_at", "enrichment_status", "updated_at"})

    if not podcast.match_key:
        from podcasts.models import build_match_key

        podcast.match_key = build_match_key(podcast.title, podcast.publisher)
        merge.changed.add("match_key")

    podcast.save(update_fields=list(merge.changed))
    if categories:
        attach_categories(podcast, categories)

    log.info(
        "enriched podcast id=%s '%s' via %s -> %s",
        podcast.pk, podcast.title, used or "nothing", podcast.enrichment_status,
    )
    return {
        "podcast_id": podcast.pk,
        "sources": podcast.enrichment_sources,
        "status": podcast.enrichment_status,
        "fields_changed": sorted(merge.changed),
    }


# ---------------------------------------------------------------------------
#  per-provider handlers
# ---------------------------------------------------------------------------
def _enrich_apple(podcast: Podcast, merge: _Merge, categories: list) -> dict | None:
    client = ApplePodcastsClient()
    try:
        record = None
        if podcast.apple_id:
            record = client.lookup(podcast.apple_id, country=podcast.country or None)
        if record is None:
            record = client.best_match(
                title=podcast.title, author=podcast.author or podcast.publisher, feed_url=podcast.rss_url
            )
        if not record:
            return None

        merge.set_id("apple_id", record.get("collectionId") or record.get("trackId"))
        merge.set_id("rss_url", record.get("feedUrl"))
        merge.fill("publisher", record.get("artistName"))
        merge.fill("author", record.get("artistName"))
        merge.fill("image_url", record.get("artworkUrl600") or record.get("artworkUrl100"))
        # note: iTunes `country` is ISO-3166 alpha-3 ("USA") — wrong shape for our
        # 2-letter field, so we take country/language from PodcastIndex instead.
        if not podcast.title:
            merge.set("title", record.get("collectionName"))
        advisory = (record.get("contentAdvisoryRating") or "").lower()
        if advisory == "explicit":
            merge.set("explicit", True)

        for genre in _as_list(record.get("genres")) or [record.get("primaryGenreName")]:
            if genre and genre.lower() != "podcasts":
                categories.append((Source.APPLE, genre))
        return record
    finally:
        client.close()


def _enrich_podcastindex(podcast: Podcast, merge: _Merge, categories: list) -> dict | None:
    client = PodcastIndexClient()
    if not client.enabled:
        return None
    try:
        feed = None
        if podcast.apple_id:
            feed = client.by_itunes_id(podcast.apple_id)
        if feed is None and podcast.rss_url:
            feed = client.by_feed_url(podcast.rss_url)
        if feed is None and podcast.podcastindex_guid:
            feed = client.by_guid(podcast.podcastindex_guid)
        if feed is None:
            matches = client.search(podcast.title)
            feed = _pick_pi_match(matches, podcast)
        if not feed:
            return None

        merge.set_id("podcastindex_id", feed.get("id"))
        merge.set_id("podcastindex_guid", feed.get("podcastGuid"))
        merge.set_id("rss_url", feed.get("url") or feed.get("originalUrl"))
        merge.set_id("apple_id", feed.get("itunesId"))
        merge.fill("description", feed.get("description"))
        merge.fill("publisher", feed.get("author") or feed.get("ownerName"))
        merge.fill("author", feed.get("author") or feed.get("ownerName"))
        merge.fill("image_url", feed.get("artwork") or feed.get("image"))
        merge.fill("website_url", feed.get("link"))
        merge.fill("language", (feed.get("language") or "")[:16])
        if str(feed.get("explicit")) in ("1", "True", "true"):
            merge.set("explicit", True)
        pi_type = feed.get("type")
        if pi_type in (1, "1"):
            merge.fill("itunes_type", "serial")
        elif pi_type in (0, "0"):
            merge.fill("itunes_type", "episodic")

        for _cid, name in (feed.get("categories") or {}).items():
            categories.append((Source.PODCASTINDEX, name))
        return feed
    finally:
        client.close()


def _enrich_podchaser(podcast: Podcast, merge: _Merge, categories: list) -> dict | None:
    client = PodchaserClient()
    if not client.enabled:
        return None
    try:
        meta = client.podcast_metadata(
            podchaser_id=podcast.podchaser_id or None,
            itunes_id=podcast.apple_id or None,
            feed_url=podcast.rss_url or None,
            title=podcast.title,
        )
        if not meta:
            return None

        merge.set_id("podchaser_id", meta.get("id"))
        merge.set_id("apple_id", meta.get("applePodcastsId"))
        merge.set_id("rss_url", meta.get("rssUrl"))
        merge.fill("description", meta.get("description"))
        merge.fill("website_url", meta.get("webUrl"))
        merge.fill("image_url", meta.get("imageUrl"))
        merge.fill("language", (meta.get("language") or "")[:16])
        author = meta.get("author")
        if isinstance(author, dict):
            merge.fill("publisher", author.get("name"))
            merge.fill("author", author.get("name"))

        # ratings — always refreshed
        avg = meta.get("ratingAverage")
        cnt = meta.get("ratingCount")
        if avg is not None:
            merge.set("rating_average", round(float(avg), 2))
        if cnt is not None:
            merge.set("rating_count", int(cnt))

        for cat in meta.get("categories") or []:
            title = cat.get("title") if isinstance(cat, dict) else cat
            if title:
                categories.append((Source.PODCHASER, title))
        return meta
    finally:
        client.close()


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def _status(podcast: Podcast, used: list[str], has_categories: bool = False) -> str:
    """DONE  -> we have a usable metadata set (identity + cover + publisher +
              some descriptive content: a description or at least categories);
    PARTIAL -> a provider answered but key fields are still missing;
    FAILED  -> no provider returned anything."""
    if not used:
        return EnrichmentStatus.FAILED
    have_identity = bool(podcast.image_url and (podcast.publisher or podcast.author))
    have_descriptive = bool(podcast.description) or has_categories or bool(podcast.categories.exists())
    return EnrichmentStatus.DONE if (have_identity and have_descriptive) else EnrichmentStatus.PARTIAL


def _pick_pi_match(matches: list[dict], podcast: Podcast) -> dict | None:
    if not matches:
        return None
    feed_l = (podcast.rss_url or "").rstrip("/").lower()
    title_l = podcast.title.strip().lower()
    for m in matches:
        if feed_l and (m.get("url") or "").rstrip("/").lower() == feed_l:
            return m
    for m in matches:
        if (m.get("title") or "").strip().lower() == title_l:
            return m
    return matches[0]


def _as_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value
