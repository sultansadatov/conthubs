"""Create/inspect the monthly range partitions of ``charts_chartentry``.

    python manage.py manage_partitions            # create any missing partitions
    python manage.py manage_partitions --list     # just show current layout
    python manage.py manage_partitions --months-ahead 6
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection

from charts.partitions import (
    DEFAULT_PARTITION,
    PARENT_TABLE,
    existing_partitions,
    ensure_partitions,
    planned_partitions,
)


def _default_partition_rows() -> int:
    with connection.cursor() as cur:
        try:
            cur.execute(f'SELECT count(*) FROM "{DEFAULT_PARTITION}"')
            return cur.fetchone()[0]
        except Exception:
            return 0


class Command(BaseCommand):
    help = "Ensure monthly range partitions exist for the chart-history table."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="only list current partitions")
        parser.add_argument("--months-ahead", type=int, default=None)

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING(
                f"DB backend is '{connection.vendor}' — partitioning is a PostgreSQL "
                f"feature; '{PARENT_TABLE}' is a plain table here."
            ))
            return

        if opts["list"]:
            have = sorted(existing_partitions())
            self.stdout.write(f"{len(have)} partition(s) on {PARENT_TABLE}:")
            for name in have:
                self.stdout.write(f"  - {name}")
            self.stdout.write("\nPlanned window:")
            for p in planned_partitions(months_ahead=opts["months_ahead"]):
                flag = "ok" if p.name in have else "MISSING"
                self.stdout.write(f"  [{flag:>7}] {p.name}  [{p.start} .. {p.end})")
            return

        created = ensure_partitions(months_ahead=opts["months_ahead"])
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created: {', '.join(created)}"))
        else:
            self.stdout.write(self.style.SUCCESS("All required partitions already exist."))

        stray = _default_partition_rows()
        if stray:
            self.stdout.write(self.style.WARNING(
                f"\n{DEFAULT_PARTITION} holds {stray} row(s) — the look-ahead buffer was "
                f"exhausted at some point. New monthly partitions cannot be created for "
                f"date ranges that overlap those rows until they are moved out "
                f"(DETACH default, create the month, re-INSERT, re-ATTACH)."
            ))
