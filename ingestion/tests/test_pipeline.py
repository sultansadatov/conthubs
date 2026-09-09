import datetime as dt

from django.test import TestCase
from django.utils import timezone

from charts.models import ChartEntry
from ingestion.pipeline import ingest_chart_result
from ingestion.scrapers.base import ChartRow, ChartScrapeResult
from podcasts.constants import ChartType, Source
from podcasts.models import Podcast


def _result(rows, *, date=None, chart_type=ChartType.PODCASTS):
    fetched = timezone.now()
    if date:
        fetched = timezone.make_aware(dt.datetime.combine(date, dt.time(12, 0)))
    return ChartScrapeResult(
        source=Source.SPOTIFY, country="us", category="top-podcasts",
        chart_type=chart_type, fetched_at=fetched, rows=rows,
    )


class IngestChartResultTests(TestCase):
    def test_creates_podcasts_and_chart_rows(self):
        rows = [
            ChartRow(rank=1, title="Show A", publisher="Pub A", spotify_id="sa", external_id="spotify:show:sa"),
            ChartRow(rank=2, title="Show B", publisher="Pub B", spotify_id="sb", external_id="spotify:show:sb"),
        ]
        summary = ingest_chart_result(_result(rows))

        self.assertEqual(summary["written"], 2)
        self.assertEqual(summary["new_podcasts"], 2)
        self.assertEqual(Podcast.objects.count(), 2)
        self.assertEqual(ChartEntry.objects.count(), 2)
        self.assertEqual(sorted(summary["enrich_ids"]), sorted(Podcast.objects.values_list("id", flat=True)))
        entry = ChartEntry.objects.get(rank=1)
        self.assertEqual(entry.podcast.spotify_id, "sa")
        self.assertEqual(entry.external_id, "spotify:show:sa")

    def test_rerun_is_idempotent_and_dedupes_podcasts(self):
        rows = [ChartRow(rank=1, title="Show A", spotify_id="sa", external_id="spotify:show:sa")]
        ingest_chart_result(_result(rows))
        ingest_chart_result(_result(rows))
        self.assertEqual(Podcast.objects.count(), 1)
        self.assertEqual(ChartEntry.objects.count(), 1)

    def test_second_day_fills_rank_movement(self):
        y = timezone.localdate() - dt.timedelta(days=1)
        t = timezone.localdate()
        ingest_chart_result(_result(
            [ChartRow(rank=1, title="A", spotify_id="a", external_id="spotify:show:a"),
             ChartRow(rank=2, title="B", spotify_id="b", external_id="spotify:show:b")],
            date=y,
        ))
        ingest_chart_result(_result(
            [ChartRow(rank=2, title="A", spotify_id="a", external_id="spotify:show:a"),
             ChartRow(rank=1, title="B", spotify_id="b", external_id="spotify:show:b")],
            date=t,
        ))
        a_today = ChartEntry.objects.get(date=t, rank=2)
        self.assertEqual(a_today.previous_rank, 1)
        self.assertEqual(a_today.rank_change, -1)

    def test_episode_chart_creates_episode_fk(self):
        rows = [ChartRow(
            rank=1, title="Parent Show", spotify_id="ps", external_id="spotify:episode:e1",
            episode_title="Hot Episode", episode_guid="spotify:episode:e1",
        )]
        ingest_chart_result(_result(rows, chart_type=ChartType.EPISODES))
        entry = ChartEntry.objects.get()
        self.assertEqual(entry.chart_type, ChartType.EPISODES)
        self.assertIsNotNone(entry.episode_id)
        self.assertEqual(entry.episode.title, "Hot Episode")
        self.assertEqual(entry.episode.podcast.title, "Parent Show")
