"""Chart history.

``ChartEntry`` is the high-volume table of the platform: one row per
``(date, source, country, category, chart_type, rank)``.  A full daily scrape of
every configured Spotify region + Podchaser country/category adds thousands of
rows *per day* and nothing is ever deleted (TT acceptance cri#3), so the table
is **range-partitioned by month on ``date``** at the database level.

Key points
----------
* **Natural composite primary key** ``(date, source, country, category,
  chart_type, rank)`` — it is the uniqueness rule we want, it contains the
  partition key (a Postgres requirement) and it doubles as the ``ON CONFLICT``
  target for the daily UPSERT (:func:`charts.services.upsert_chart_entries`).
* **Mandatory index** on ``(source, country, category, date)`` (TT §3
  "country, category və date sütunları mütləq indekslənməlidir").
* The physical partitioned table is created by ``0001_initial`` via raw SQL
  (Django's schema editor cannot express ``PARTITION BY``); on non-PostgreSQL
  backends it degrades to a plain table so the unit-test suite still runs.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone

from podcasts.constants import ChartType, Source


class ChartEntry(models.Model):
    pk = models.CompositePrimaryKey(
        "date", "source", "country", "category", "chart_type", "rank"
    )

    date = models.DateField(help_text="chart snapshot date (partition key)")
    source = models.CharField(max_length=20, choices=Source.choices)
    country = models.CharField(max_length=8, help_text="ISO-3166 alpha-2, lower-case")
    category = models.CharField(
        max_length=120, help_text="category slug, never empty (e.g. 'top-podcasts', 'comedy')"
    )
    chart_type = models.CharField(
        max_length=20, choices=ChartType.choices, default=ChartType.PODCASTS
    )
    rank = models.PositiveSmallIntegerField()

    previous_rank = models.PositiveSmallIntegerField(null=True, blank=True)
    rank_change = models.SmallIntegerField(
        null=True, blank=True, help_text="previous_rank - rank; >0 means moved up"
    )

    podcast = models.ForeignKey(
        "podcasts.Podcast", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="chart_entries",
    )
    episode = models.ForeignKey(
        "podcasts.Episode", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="chart_entries",
    )

    # Snapshot of how the row looked at scrape time — lets the Chart API render
    # even before enrichment has resolved the entity. Deliberately *no* raw JSON
    # blob here: this table grows by thousands of rows/day forever (TT §3), so a
    # near-duplicate payload per show per day is exactly the "lazımsız yük" the
    # spec warns against. Rich provider data lives once on Podcast.raw_metadata.
    title = models.CharField(max_length=500, blank=True, default="")
    publisher = models.CharField(max_length=300, blank=True, default="")
    image_url = models.URLField(max_length=1000, blank=True, default="")
    external_url = models.URLField(max_length=1000, blank=True, default="")
    external_id = models.CharField(max_length=200, blank=True, default="")

    scraped_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "source", "country", "category", "chart_type", "rank"]
        verbose_name = "chart entry"
        verbose_name_plural = "chart entries"
        indexes = [
            # TT §3 — the spec explicitly mandates that country / category / date
            # be indexed. This is that index (source-prefixed for selectivity).
            models.Index(
                fields=["source", "country", "category", "date"],
                name="chartentry_sccd_idx",
            ),
            # the covering index the Chart API's planner actually uses (adds
            # chart_type + rank so the ranked page is an index-only range scan)
            models.Index(
                fields=["source", "country", "category", "chart_type", "date", "rank"],
                name="chartentry_lookup_idx",
            ),
            models.Index(fields=["date"], name="chartentry_date_idx"),
            models.Index(fields=["podcast", "date"], name="chartentry_podcast_date_idx"),
            models.Index(fields=["episode", "date"], name="chartentry_episode_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.source}/{self.country}/{self.category}/{self.chart_type} #{self.rank}"

    @property
    def entity(self):
        return self.episode if self.chart_type == ChartType.EPISODES else self.podcast
