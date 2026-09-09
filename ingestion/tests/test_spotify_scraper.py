from django.test import TestCase

from ingestion.scrapers.spotify import SpotifyChartScraper
from podcasts.constants import ChartType

from .fixtures import SPOTIFY_EPISODES_JSON, SPOTIFY_HTML, SPOTIFY_TOP_JSON


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_chart(self, *, category, region):
        self.calls.append((category, region))
        return self.rows

    def close(self):
        pass


class SpotifyScraperTests(TestCase):
    def test_parses_top_podcasts(self):
        client = FakeClient(SPOTIFY_TOP_JSON)
        scraper = SpotifyChartScraper(client=client)
        result = scraper.scrape(region="us", category="top-podcasts")

        self.assertEqual(client.calls, [("top-podcasts", "us")])
        self.assertEqual(result.source, "spotify")
        self.assertEqual(result.country, "us")
        self.assertEqual(result.category, "top-podcasts")
        self.assertEqual(result.chart_type, ChartType.PODCASTS)
        self.assertEqual(len(result.rows), 3)

        first = result.rows[0]
        self.assertEqual(first.rank, 1)
        self.assertEqual(first.title, "The Example Daily")
        self.assertEqual(first.publisher, "Example News")
        self.assertEqual(first.spotify_id, "aaa111")
        self.assertEqual(first.external_id, "spotify:show:aaa111")
        self.assertEqual(first.external_url, "https://open.spotify.com/show/aaa111")
        self.assertFalse(first.raw["_isNew"])

        # rank comes from array position when the payload has no explicit rank
        self.assertEqual([r.rank for r in result.rows], [1, 2, 3])
        # "NEW" move flagged
        self.assertTrue(result.rows[2].raw["_isNew"])

    def test_parses_top_episodes(self):
        scraper = SpotifyChartScraper(client=FakeClient(SPOTIFY_EPISODES_JSON))
        result = scraper.scrape(region="us", category="top-episodes")
        self.assertEqual(result.chart_type, ChartType.EPISODES)
        row = result.rows[0]
        self.assertEqual(row.episode_title, "Big Interview")
        self.assertEqual(row.external_id, "spotify:episode:ep111")
        self.assertEqual(row.external_url, "https://open.spotify.com/episode/ep111")
        self.assertEqual(row.title, "The Example Daily")  # show name retained

    def test_genre_category_is_a_podcast_chart(self):
        scraper = SpotifyChartScraper(client=FakeClient(SPOTIFY_TOP_JSON))
        result = scraper.scrape(region="gb", category="comedy")
        self.assertEqual(result.chart_type, ChartType.PODCASTS)
        self.assertEqual(result.category, "comedy")

    def test_html_blob_walker(self):
        from ingestion.clients.spotify import _find_chart_rows
        import json
        import re

        blob = json.loads(
            re.search(r'__NEXT_DATA__" type="application/json">(.*?)</script>', SPOTIFY_HTML, re.S).group(1)
        )
        rows = _find_chart_rows(blob)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["showName"], "HTML Show One")
