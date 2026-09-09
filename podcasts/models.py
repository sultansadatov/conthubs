"""Core domain models: Category, Podcast, Episode.

Design notes
------------
* **De-duplication (TT §3 "Dublikatların Qarşısının Alınması").**  A podcast is
  identified by whichever stable external id we have (rss url, Apple id,
  PodcastIndex id/guid, Podchaser id, Spotify id).  Each of those has a partial
  unique constraint.  When no id matches we fall back to a normalised
  ``match_key`` of ``"<title>|<publisher>"``.  See ``podcasts.services.resolve_podcast``.
* **Episodes** are unique per ``(podcast, guid)`` — the RSS ``<guid>`` (or the
  API episode id, or a deterministic hash) — which is exactly the UPSERT key the
  TT asks for.  ``podcasts.services.upsert_episodes`` performs the bulk UPSERT.
* **History is append-only.**  Nothing here is ever hard-deleted by the
  pipeline; ``is_active`` is used to hide dead feeds instead.
"""
from __future__ import annotations

from django.db import models
from django.utils.text import slugify

from .constants import EnrichmentStatus, PublishFrequency, Source
from .querysets import PodcastQuerySet


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Category(TimestampedModel):
    """A taxonomy node.

    Categories are namespaced by ``source`` because Spotify, Podchaser, Apple
    and PodcastIndex all use different taxonomies. Optional ``parent`` supports
    Apple/PodcastIndex sub-genres.
    """

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.INTERNAL)
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160)
    external_id = models.CharField(max_length=100, blank=True, default="")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["source", "name"]
        constraints = [
            # also serves as the (source, slug) lookup index
            models.UniqueConstraint(fields=["source", "slug"], name="uniq_category_source_slug"),
        ]
        indexes = [
            # cross-source slug filter used by the podcast-list `?category=` filter
            models.Index(fields=["slug"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.get_source_display()}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:160] or "category"
        super().save(*args, **kwargs)


class Podcast(TimestampedModel):
    Status = EnrichmentStatus

    # --- stable external identifiers (de-duplication keys) ----------------
    rss_url = models.URLField(max_length=1000, blank=True, default="")
    apple_id = models.CharField(
        "Apple/iTunes collectionId", max_length=32, blank=True, default=""
    )
    podcastindex_id = models.CharField(max_length=32, blank=True, default="")
    podcastindex_guid = models.CharField(max_length=64, blank=True, default="")
    podchaser_id = models.CharField(max_length=32, blank=True, default="")
    spotify_id = models.CharField(max_length=64, blank=True, default="")
    match_key = models.CharField(
        max_length=400,
        blank=True,
        default="",
        db_index=True,
        help_text="normalised '<title>|<publisher>' fallback de-dupe key",
    )

    # --- core metadata (TT §2.2 "Saxlanılacaq əsas məlumatlar") -----------
    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=280, blank=True, default="")
    description = models.TextField(blank=True, default="")
    publisher = models.CharField(max_length=300, blank=True, default="")
    author = models.CharField(max_length=300, blank=True, default="")
    image_url = models.URLField("cover image URL", max_length=1000, blank=True, default="")
    website_url = models.URLField(max_length=1000, blank=True, default="")
    language = models.CharField(max_length=16, blank=True, default="")
    country = models.CharField(max_length=8, blank=True, default="")
    explicit = models.BooleanField(default=False)
    itunes_type = models.CharField(max_length=16, blank=True, default="")  # episodic / serial
    categories = models.ManyToManyField(Category, blank=True, related_name="podcasts")

    # --- ratings / frequency (from enrichment) ---------------------------
    rating_average = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    rating_count = models.PositiveIntegerField(null=True, blank=True)
    publish_frequency = models.CharField(
        max_length=16, choices=PublishFrequency.choices, blank=True, default=""
    )

    # --- denormalised aggregates (kept fresh by signals + a nightly task) -
    total_episodes = models.PositiveIntegerField(default=0)
    last_published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # --- enrichment bookkeeping (TT §2.2) ------------------------------
    # (indexed via the composite ("enrichment_status", "enriched_at") below)
    enrichment_status = models.CharField(
        max_length=12, choices=EnrichmentStatus.choices,
        default=EnrichmentStatus.PENDING,
    )
    enriched_at = models.DateTimeField(null=True, blank=True)
    enrichment_sources = models.JSONField(default=list, blank=True)
    raw_metadata = models.JSONField(default=dict, blank=True)

    # --- episode-sync bookkeeping (TT §2.3) --------------------------
    episodes_synced_at = models.DateTimeField(null=True, blank=True)
    feed_etag = models.CharField(max_length=250, blank=True, default="")
    feed_last_modified = models.CharField(max_length=100, blank=True, default="")

    is_active = models.BooleanField(default=True, db_index=True)
    first_seen_source = models.CharField(max_length=20, choices=Source.choices, blank=True, default="")

    objects = PodcastQuerySet.as_manager()

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["title"]),
            models.Index(fields=["publisher"]),
            models.Index(fields=["language"]),
            models.Index(fields=["enrichment_status", "enriched_at"]),
            models.Index(fields=["episodes_synced_at"]),
            models.Index(fields=["-last_published_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["rss_url"], condition=~models.Q(rss_url=""), name="uniq_podcast_rss_url"
            ),
            models.UniqueConstraint(
                fields=["apple_id"], condition=~models.Q(apple_id=""), name="uniq_podcast_apple_id"
            ),
            models.UniqueConstraint(
                fields=["podcastindex_id"], condition=~models.Q(podcastindex_id=""),
                name="uniq_podcast_pi_id",
            ),
            models.UniqueConstraint(
                fields=["podcastindex_guid"], condition=~models.Q(podcastindex_guid=""),
                name="uniq_podcast_pi_guid",
            ),
            models.UniqueConstraint(
                fields=["podchaser_id"], condition=~models.Q(podchaser_id=""),
                name="uniq_podcast_pc_id",
            ),
            models.UniqueConstraint(
                fields=["spotify_id"], condition=~models.Q(spotify_id=""),
                name="uniq_podcast_spotify_id",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:280]
        if not self.match_key and self.title:
            self.match_key = build_match_key(self.title, self.publisher)
        super().save(*args, **kwargs)

    @property
    def external_ids(self) -> dict:
        return {
            "rss_url": self.rss_url,
            "apple_id": self.apple_id,
            "podcastindex_id": self.podcastindex_id,
            "podcastindex_guid": self.podcastindex_guid,
            "podchaser_id": self.podchaser_id,
            "spotify_id": self.spotify_id,
        }


class EpisodeQuerySet(models.QuerySet):
    def published(self):
        return self.filter(published_at__isnull=False)

    def latest_first(self):
        # undated episodes must not float to the top of "newest first"
        return self.order_by(models.F("published_at").desc(nulls_last=True), "-id")


class Episode(TimestampedModel):
    podcast = models.ForeignKey(Podcast, on_delete=models.CASCADE, related_name="episodes")

    # --- UPSERT key (TT §3): unique per (podcast, guid) ------------------
    guid = models.CharField(max_length=500)

    title = models.CharField(max_length=600)
    description = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)
    season_number = models.PositiveIntegerField(null=True, blank=True)
    episode_type = models.CharField(max_length=16, blank=True, default="")  # full / trailer / bonus
    explicit = models.BooleanField(default=False)
    image_url = models.URLField(max_length=1000, blank=True, default="")

    # enclosure / media
    audio_url = models.URLField(max_length=1500, blank=True, default="")
    audio_length_bytes = models.BigIntegerField(null=True, blank=True)
    audio_type = models.CharField(max_length=60, blank=True, default="")

    # external episode ids (apple / podchaser / podcastindex) + raw payload
    external_ids = models.JSONField(default=dict, blank=True)
    raw_metadata = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.RSS)

    objects = EpisodeQuerySet.as_manager()

    class Meta:
        ordering = [models.F("published_at").desc(nulls_last=True), "-id"]
        constraints = [
            models.UniqueConstraint(fields=["podcast", "guid"], name="uniq_episode_podcast_guid"),
        ]
        indexes = [
            # TT §2.3 / §3 — episode lists are always "this podcast, newest first"
            models.Index(
                models.F("podcast"),
                models.F("published_at").desc(nulls_last=True),
                models.F("id").desc(),
                name="episode_podcast_pubdate_idx",
            ),
            models.Index(fields=["-published_at"], name="episode_pubdate_idx"),
        ]

    def __str__(self) -> str:
        return self.title


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def build_match_key(title: str, publisher: str = "") -> str:
    """Normalised fallback de-dupe key for a podcast."""
    t = slugify(title or "")[:200]
    p = slugify(publisher or "")[:150]
    return f"{t}|{p}" if p else t
