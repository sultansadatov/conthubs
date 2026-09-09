import time

import responses
from django.test import TestCase

from ingestion.clients.base import HttpClient, HttpError, RobotsDisallowed


class HttpClientTests(TestCase):
    @responses.activate
    def test_retries_on_500_then_succeeds(self):
        responses.add(responses.GET, "https://api.test/data", status=500)
        responses.add(responses.GET, "https://api.test/data", status=500)
        responses.add(responses.GET, "https://api.test/data", json={"ok": True}, status=200)

        client = HttpClient(respect_robots=False, rate_limit_per_host=0, backoff_factor=0.01, max_retries=4)
        self.assertEqual(client.get_json("https://api.test/data"), {"ok": True})
        self.assertEqual(len(responses.calls), 3)

    @responses.activate
    def test_gives_up_after_max_retries(self):
        responses.add(responses.GET, "https://api.test/down", status=503)
        client = HttpClient(respect_robots=False, rate_limit_per_host=0, backoff_factor=0.01, max_retries=3)
        with self.assertRaises(HttpError):
            client.get("https://api.test/down")
        self.assertEqual(len(responses.calls), 3)

    @responses.activate
    def test_4xx_is_not_retried(self):
        responses.add(responses.GET, "https://api.test/missing", status=404)
        client = HttpClient(respect_robots=False, rate_limit_per_host=0, max_retries=4)
        with self.assertRaises(HttpError) as ctx:
            client.get("https://api.test/missing")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(responses.calls), 1)

    def test_robots_txt_disallow_is_enforced(self):
        from urllib import robotparser

        rp = robotparser.RobotFileParser()
        rp.parse(["User-agent: *", "Disallow: /private"])
        client = HttpClient(respect_robots=True, rate_limit_per_host=0)
        client._robots["https://blocked.test"] = rp  # pre-seed the parsed rules

        with self.assertRaises(RobotsDisallowed):
            client.get("https://blocked.test/private/page")

    def test_robots_txt_allows_permitted_path(self):
        from urllib import robotparser

        rp = robotparser.RobotFileParser()
        rp.parse(["User-agent: *", "Disallow: /private"])
        client = HttpClient(respect_robots=True, rate_limit_per_host=0)
        client._robots["https://ok.test"] = rp
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "https://ok.test/public", json={"ok": 1}, status=200)
            self.assertEqual(client.get_json("https://ok.test/public"), {"ok": 1})

    @responses.activate
    def test_rate_limit_spaces_requests(self):
        for _ in range(3):
            responses.add(responses.GET, "https://slow.test/x", json={}, status=200)
        client = HttpClient(respect_robots=False, rate_limit_per_host=20)  # 50ms apart
        start = time.monotonic()
        for _ in range(3):
            client.get("https://slow.test/x")
        self.assertGreaterEqual(time.monotonic() - start, 0.09)
