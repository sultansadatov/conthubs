"""Client for Spotify's public podcast charts (https://podcastcharts.byspotify.com).

The chart site is a Next.js app whose UI fetches ranked chart data from:

    GET https://podcastcharts.byspotify.com/api/charts/<category>?region=<cc>&limit=100

``<category>`` is one of ``top-podcasts`` / ``top-episodes`` / a genre slug
(``comedy``, ``news`` …). The response is a **rank-ordered JSON array** (rank =
array position) of objects shaped like::

    {"showUri": "spotify:show:…", "showName": "…", "showPublisher": "…",
     "showImageUrl": "…", "showDescription": "…", "chartRankMove": "UP|DOWN|NEW|UNCHANGED",
     # top-episodes only:
     "episodeUri": "spotify:episode:…", "episodeName": "…", "episodeImageUrl": "…"}

If the JSON endpoint is unavailable we try to salvage a chart list from any JSON
embedded in the HTML page (``__NEXT_DATA__`` / streamed React payload); that
usually yields nothing (the page fetches client-side) but is a harmless
last resort.
"""
from __future__ import annotations

import json
import logging
import re

from django.conf import settings

from .base import HttpClient, HttpError

log = logging.getLogger("ingestion.spotify")

_JSON_ISLAND_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(?P<body>.*?)</script>', re.DOTALL
)


class SpotifyChartsClient:
    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient(rate_limit_per_host=2)
        self.api_tmpl = settings.SPOTIFY_CHART_API_URL
        self.base_url = settings.SPOTIFY_CHART_BASE_URL.rstrip("/")
        self.limit = settings.SPOTIFY_CHART_LIMIT

    def fetch_chart(self, *, category: str, region: str) -> list[dict]:
        """Return the raw, rank-ordered list of chart rows for ``category``/``region``."""
        region = region.lower()
        url = self.api_tmpl.format(category=category)
        try:
            data = self.http.get_json(
                url,
                params={"region": region, "limit": self.limit},
                headers={"Accept": "application/json"},
            )
            rows = _coerce_rows(data)
            if rows:
                log.info("spotify %s/%s -> %d rows (api)", category, region, len(rows))
                return rows
            log.warning("spotify %s/%s api returned no rows; trying HTML", category, region)
        except HttpError as exc:
            log.warning("spotify api %s failed (%s); trying HTML", url, exc)

        return self._fetch_from_html(category=category, region=region)

    def _fetch_from_html(self, *, category: str, region: str) -> list[dict]:
        url = f"{self.base_url}/{region}/{category}"
        try:
            html = self.http.get_text(url)
        except HttpError as exc:
            log.warning("spotify html %s failed: %s", url, exc)
            return []
        match = _JSON_ISLAND_RE.search(html)
        if not match:
            return []
        try:
            blob = json.loads(match.group("body"))
        except ValueError:
            return []
        rows = _find_chart_rows(blob)
        if rows:
            log.info("spotify %s/%s -> %d rows (html)", category, region, len(rows))
        return rows

    def close(self):
        self.http.close()


def _coerce_rows(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("chartEntries", "entries", "items", "shows", "episodes", "data", "results"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def _looks_like_row(d: dict) -> bool:
    return isinstance(d, dict) and bool(
        set(d) & {"showUri", "showName", "episodeUri", "episodeName", "chartRankMove"}
    )


def _find_chart_rows(blob) -> list[dict]:
    stack = [blob]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            if node and any(_looks_like_row(x) for x in node):
                return [x for x in node if isinstance(x, dict)]
            stack.extend(x for x in node if isinstance(x, (list, dict)))
        elif isinstance(node, dict):
            stack.extend(node.values())
    return []
