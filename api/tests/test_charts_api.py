import datetime as dt

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .factories import make_chart, make_podcast


class ChartAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.today = timezone.localdate()
        self.yest = self.today - dt.timedelta(days=1)
        self.a = make_podcast("Chart A", apple_id="1", publisher="AA")
        self.b = make_podcast("Chart B", apple_id="2", publisher="BB")
        self.c = make_podcast("Chart C", apple_id="3", publisher="CC")
        make_chart(self.yest, [(1, self.a), (2, self.b), (3, self.c)])
        make_chart(self.today, [(1, self.b), (2, self.a), (3, self.c)])

    def test_defaults_to_latest_date_ordered_by_rank(self):
        r = self.client.get("/api/v1/charts")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["date"], self.today.isoformat())
        self.assertTrue(body["is_latest"])
        self.assertEqual([row["rank"] for row in body["results"]], [1, 2, 3])
        self.assertEqual(body["results"][0]["podcast"]["title"], "Chart B")

    def test_rank_movement_is_exposed(self):
        row = self.client.get("/api/v1/charts").json()["results"][0]  # B: 2 -> 1
        self.assertEqual(row["previous_rank"], 2)
        self.assertEqual(row["rank_change"], 1)
        self.assertFalse(row["is_new"])

    def test_explicit_date_param(self):
        body = self.client.get("/api/v1/charts", {"date": self.yest.isoformat()}).json()
        self.assertEqual(body["date"], self.yest.isoformat())
        self.assertFalse(body["is_latest"])
        self.assertEqual(body["results"][0]["podcast"]["title"], "Chart A")

    def test_limit_and_offset(self):
        body = self.client.get("/api/v1/charts", {"limit": 1, "offset": 1}).json()
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["rank"], 2)
        self.assertEqual(body["count"], 3)

    def test_unknown_slice_returns_404(self):
        r = self.client.get("/api/v1/charts", {"country": "zz"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "chart_not_available")

    def test_bad_source_returns_400(self):
        r = self.client.get("/api/v1/charts", {"source": "nope"})
        self.assertEqual(r.status_code, 400)

    def test_bad_date_returns_400(self):
        r = self.client.get("/api/v1/charts", {"date": "2026/01/01"})
        self.assertEqual(r.status_code, 400)

    def test_response_is_cached(self):
        self.client.get("/api/v1/charts")  # warm the cache
        with self.assertNumQueries(0):
            r = self.client.get("/api/v1/charts")  # served entirely from cache
        self.assertEqual(r.status_code, 200)
