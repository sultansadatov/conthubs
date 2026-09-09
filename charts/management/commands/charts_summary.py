"""Operational snapshot of the chart-history table.

    python manage.py charts_summary
    python manage.py charts_summary --days 14
"""
from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min

from charts.models import ChartEntry
from charts.partitions import existing_partitions
from django.db import connection


class Command(BaseCommand):
    help = "Print row counts, date coverage and per-slice freshness for chart history."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **opts):
        total = ChartEntry.objects.count()
        span = ChartEntry.objects.aggregate(lo=Min("date"), hi=Max("date"))
        self.stdout.write(self.style.MIGRATE_HEADING("Chart history"))
        self.stdout.write(f"  rows            : {total:,}")
        self.stdout.write(f"  date coverage   : {span['lo']} .. {span['hi']}")
        if connection.vendor == "postgresql":
            self.stdout.write(f"  partitions      : {len(existing_partitions())}")

        since = dt.date.today() - dt.timedelta(days=opts["days"])
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nPer-slice (last {opts['days']} days)"))
        rows = (
            ChartEntry.objects.filter(date__gte=since)
            .values("source", "country", "category", "chart_type")
            .annotate(n=Count("*"), last=Max("date"))
            .order_by("source", "country", "category", "chart_type")
        )
        for r in rows:
            self.stdout.write(
                f"  {r['source']:<10} {r['country']:<4} {r['category']:<16} "
                f"{r['chart_type']:<9} rows={r['n']:<6} last={r['last']}"
            )
        if not rows:
            self.stdout.write("  (no data yet — run `scrape_charts`)")
