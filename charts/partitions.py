"""Monthly range-partition management for ``charts_chartentry`` (TT §3
"Table Partitioning").

The physical layout on PostgreSQL::

    charts_chartentry                      -- PARTITION BY RANGE (date)
      charts_chartentry_2024_01            -- FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')
      charts_chartentry_2024_02
      ...
      charts_chartentry_default            -- catch-all, so an INSERT can never fail

``ensure_partitions`` is idempotent and is called from the ``0001_initial``
migration, the ``manage_partitions`` command and the monthly Celery beat task.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import connection

log = logging.getLogger("charts.partitions")

PARENT_TABLE = "charts_chartentry"
DEFAULT_PARTITION = f"{PARENT_TABLE}_default"


@dataclass(frozen=True)
class MonthPartition:
    name: str
    start: dt.date
    end: dt.date  # exclusive

    @property
    def ddl(self) -> str:
        return (
            f'CREATE TABLE IF NOT EXISTS "{self.name}" '
            f'PARTITION OF "{PARENT_TABLE}" '
            f"FOR VALUES FROM ('{self.start.isoformat()}') TO ('{self.end.isoformat()}');"
        )


def _first_of_month(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _add_month(d: dt.date) -> dt.date:
    return dt.date(d.year + (d.month // 12), (d.month % 12) + 1, 1)


def iter_month_partitions(start: dt.date, end: dt.date):
    """Yield one :class:`MonthPartition` per calendar month in ``[start, end)``."""
    cur = _first_of_month(start)
    end = _first_of_month(end)
    while cur < end:
        nxt = _add_month(cur)
        yield MonthPartition(f"{PARENT_TABLE}_{cur:%Y_%m}", cur, nxt)
        cur = nxt


def planned_partitions(
    *, start: dt.date | None = None, months_ahead: int | None = None
) -> list[MonthPartition]:
    if start is None:
        start = dt.date.fromisoformat(settings.CHARTS["PARTITION_START"])
    if months_ahead is None:
        months_ahead = settings.CHARTS["PARTITION_AHEAD_MONTHS"]
    today = dt.date.today()
    horizon = _first_of_month(today)
    for _ in range(months_ahead + 1):
        horizon = _add_month(horizon)
    return list(iter_month_partitions(min(start, _first_of_month(today)), horizon))


def existing_partitions(using=connection) -> set[str]:
    with using.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = %s
            """,
            [PARENT_TABLE],
        )
        return {row[0] for row in cur.fetchall()}


def ensure_default_partition(using=connection) -> bool:
    if using.vendor != "postgresql":
        return False
    if DEFAULT_PARTITION in existing_partitions(using):
        return False
    with using.cursor() as cur:
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{DEFAULT_PARTITION}" '
            f'PARTITION OF "{PARENT_TABLE}" DEFAULT;'
        )
    log.info("created default chart partition %s", DEFAULT_PARTITION)
    return True


def ensure_partitions(
    *, start: dt.date | None = None, months_ahead: int | None = None, using=connection
) -> list[str]:
    """Create any missing monthly partitions (and the DEFAULT one). Idempotent.
    Returns the names actually created this call."""
    if using.vendor != "postgresql":
        return []

    created: list[str] = []
    if ensure_default_partition(using):
        created.append(DEFAULT_PARTITION)

    have = existing_partitions(using)
    for part in planned_partitions(start=start, months_ahead=months_ahead):
        if part.name in have:
            continue
        with using.cursor() as cur:
            cur.execute(part.ddl)
        created.append(part.name)
        log.info("created chart partition %s [%s, %s)", part.name, part.start, part.end)
    return created
