from django.test import TestCase

from ingestion.clients.podchaser import _extract_chart_nodes
from ingestion.scrapers.podchaser import PodchaserChartScraper

from .fixtures import PODCHASER_DOM_HTML, PODCHASER_GQL_DATA, PODCHASER_HTML


class FakeClient:
    def __init__(self, *, nodes=None, html=""):
        self._nodes = nodes or []
        self._html = html

    def fetch_chart(self, *, category, country):
        return self._nodes

    def fetch_charts_html(self, *, category, country):
        return self._html

    def close(self):
        pass


class PodchaserGraphQLTests(TestCase):
    def test_extract_chart_nodes_flattens_rank_and_podcast(self):
        nodes = _extract_chart_nodes(PODCHASER_GQL_DATA)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["rank"], 1)
        self.assertEqual(nodes[0]["title"], "Podchaser Number One")

    def test_scrape_uses_graphql_when_available(self):
        nodes = _extract_chart_nodes(PODCHASER_GQL_DATA)
        scraper = PodchaserChartScraper(client=FakeClient(nodes=nodes))
        result = scraper.scrape(country="us", category="all")

        self.assertEqual(result.source, "podchaser")
        self.assertEqual(len(result.rows), 2)
        first = result.rows[0]
        self.assertEqual(first.rank, 1)
        self.assertEqual(first.title, "Podchaser Number One")
        self.assertEqual(first.publisher, "PC Publisher")
        self.assertEqual(first.podchaser_id, "12345")
        self.assertEqual(first.apple_id, "555001")
        self.assertEqual(first.rss_url, "https://feeds.example/pc1.xml")

    def test_scrape_falls_back_to_html_when_no_api(self):
        scraper = PodchaserChartScraper(client=FakeClient(nodes=[], html=PODCHASER_HTML))
        result = scraper.scrape(country="us", category="all")
        self.assertEqual([r.title for r in result.rows], ["Podchaser Number One", "Podchaser Number Two"])


class PodchaserHtmlParseTests(TestCase):
    def setUp(self):
        self.scraper = PodchaserChartScraper(client=object())  # parse() is pure

    def test_parses_next_data_blob(self):
        result = self.scraper.parse(PODCHASER_HTML, country="us", category="all")
        self.assertEqual(len(result.rows), 2)
        first = result.rows[0]
        self.assertEqual(first.title, "Podchaser Number One")
        self.assertEqual(first.podchaser_id, "12345")
        self.assertEqual(first.external_url, "https://www.podchaser.com/podcasts/podchaser-number-one-12345")

    def test_dom_fallback_when_no_json(self):
        result = self.scraper.parse(PODCHASER_DOM_HTML, country="gb", category="comedy")
        self.assertEqual([r.title for r in result.rows], ["Dom Show One", "Dom Show Two"])
        self.assertEqual(result.rows[0].rank, 1)

    def test_empty_html_yields_no_rows(self):
        result = self.scraper.parse("<html><body>nothing</body></html>", country="us", category="x")
        self.assertEqual(len(result.rows), 0)
