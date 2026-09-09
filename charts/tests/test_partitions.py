import datetime as dt

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from charts.models import ChartEntry
from charts.partitions import (
    ensure_partitions,
    existing_partitions,
    iter_month_partitions,
    planned_partitions,
)
from charts.services import upsert_chart_entries
from podcasts.services import resolve_podcast


class PartitionPlanningTests(TestCase):
    def test_iter_month_partitions_boundaries(self):
        parts = list(iter_month_partitions(dt.date(2024, 11, 15), dt.date(2025, 2, 1)))
        self.assertEqual([p.name for p in parts], [
            "charts_chartentry_2024_11",
            "charts_chartentry_2024_12",
            "charts_chartentry_2025_01",
        ])
        self.assertEqual(parts[0].start, dt.date(2024, 11, 1))
        self.assertEqual(parts[0].end, dt.date(2024, 12, 1))

    def test_planned_partitions_span_covers_now_plus_ahead(self):
        parts = planned_partitions(start=dt.date(2025, 1, 1), months_ahead=2)
        names = {p.name for p in parts}
        this_month = timezone.localdate().replace(day=1)
        self.assertIn(f"charts_chartentry_{this_month:%Y_%m}", names)


class PostgresPartitioningTests(TestCase):
    databases = {"default"}

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("partitioning is PostgreSQL-only")

    def test_default_and_monthly_partitions_exist(self):
        have = existing_partitions()
        self.assertIn("charts_chartentry_default", have)
        this_month = timezone.localdate().replace(day=1)
        self.assertIn(f"charts_chartentry_{this_month:%Y_%m}", have)

    def test_ensure_partitions_is_idempotent(self):
        self.assertEqual(ensure_partitions(), [])  # migration already created them

    def test_row_routes_to_month_partition(self):
        p = resolve_podcast(title="Routed", publisher="R", apple_id="7").podcast
        d = dt.date(2026, 2, 14)
        upsert_chart_entries([{
            "date": d, "source": "spotify", "country": "us", "category": "top-podcasts",
            "chart_type": "podcasts", "rank": 1, "podcast": p, "title": "Routed",
            "external_id": "7", "scraped_at": timezone.now(),
        }])
        with connection.cursor() as cur:
            cur.execute('SELECT count(*) FROM "charts_chartentry_2026_02"')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT count(*) FROM "charts_chartentry_2025_01"')
            self.assertEqual(cur.fetchone()[0], 0)
        self.assertEqual(ChartEntry.objects.filter(date=d).count(), 1)
