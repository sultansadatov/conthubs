import datetime as dt

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from charts.models import ChartEntry
from charts.services import (
    backfill_rank_movement,
    latest_chart_date,
    previous_chart_date,
    upsert_chart_entries,
)
from podcasts.services import resolve_podcast

SLICE = dict(source="spotify", country="us", category="top-podcasts", chart_type="podcasts")


def _row(date, rank, podcast, **over):
    return {
        **SLICE,
        "date": date,
        "rank": rank,
        "podcast": podcast,
        "title": podcast.title,
        "external_id": podcast.apple_id or f"x{podcast.pk}",
        "scraped_at": timezone.now(),
        **over,
    }


class UpsertChartEntriesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.p1 = resolve_podcast(title="One", publisher="A", apple_id="1").podcast
        self.p2 = resolve_podcast(title="Two", publisher="B", apple_id="2").podcast
        self.today = timezone.localdate()

    def test_write_and_idempotent_rewrite(self):
        rows = [_row(self.today, 1, self.p1), _row(self.today, 2, self.p2)]
        self.assertEqual(upsert_chart_entries(rows)["written"], 2)
        self.assertEqual(ChartEntry.objects.count(), 2)
        # re-run same scrape -> still 2 rows, no duplicates (TT §3)
        upsert_chart_entries(rows)
        self.assertEqual(ChartEntry.objects.count(), 2)

    def test_rewrite_updates_snapshot_in_place(self):
        upsert_chart_entries([_row(self.today, 1, self.p1, title="old")])
        upsert_chart_entries([_row(self.today, 1, self.p1, title="new")])
        self.assertEqual(ChartEntry.objects.get(**SLICE, date=self.today, rank=1).title, "new")
        self.assertEqual(ChartEntry.objects.count(), 1)

    def test_history_is_preserved_across_days(self):
        y = self.today - dt.timedelta(days=1)
        upsert_chart_entries([_row(y, 1, self.p1)])
        upsert_chart_entries([_row(self.today, 1, self.p1)])
        self.assertEqual(ChartEntry.objects.filter(**SLICE).count(), 2)
        self.assertEqual(sorted(ChartEntry.objects.values_list("date", flat=True)), [y, self.today])


class RankMovementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.p1 = resolve_podcast(title="One", publisher="A", apple_id="1").podcast
        self.p2 = resolve_podcast(title="Two", publisher="B", apple_id="2").podcast
        self.today = timezone.localdate()
        self.yest = self.today - dt.timedelta(days=1)

    def test_backfill_fills_previous_rank_and_change(self):
        upsert_chart_entries([_row(self.yest, 1, self.p1), _row(self.yest, 2, self.p2)])
        upsert_chart_entries([_row(self.today, 2, self.p1), _row(self.today, 1, self.p2)])
        filled = backfill_rank_movement(date=self.today, **SLICE)
        self.assertEqual(filled, 2)
        e1 = ChartEntry.objects.get(**SLICE, date=self.today, podcast=self.p1)
        self.assertEqual((e1.previous_rank, e1.rank_change), (1, -1))  # 1 -> 2 = down
        e2 = ChartEntry.objects.get(**SLICE, date=self.today, podcast=self.p2)
        self.assertEqual((e2.previous_rank, e2.rank_change), (2, 1))  # 2 -> 1 = up

    def test_new_entry_has_no_previous_rank(self):
        upsert_chart_entries([_row(self.yest, 1, self.p1)])
        upsert_chart_entries([_row(self.today, 1, self.p1), _row(self.today, 2, self.p2)])
        backfill_rank_movement(date=self.today, **SLICE)
        self.assertIsNone(ChartEntry.objects.get(**SLICE, date=self.today, podcast=self.p2).previous_rank)

    def test_previous_chart_date(self):
        upsert_chart_entries([_row(self.yest, 1, self.p1)])
        upsert_chart_entries([_row(self.today, 1, self.p1)])
        self.assertEqual(previous_chart_date(**SLICE, before=self.today), self.yest)


class LatestDateCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.p1 = resolve_podcast(title="One", publisher="A", apple_id="1").podcast
        self.today = timezone.localdate()

    def test_latest_date_cached_and_invalidated_on_write(self):
        self.assertIsNone(latest_chart_date(**SLICE))  # caches the "no data" answer
        # cache invalidation is deferred to transaction commit
        with self.captureOnCommitCallbacks(execute=True):
            upsert_chart_entries([_row(self.today, 1, self.p1)])
        self.assertEqual(latest_chart_date(**SLICE), self.today)
