"""Podchaser client.

Two capabilities:

1. **Charts** (TT §2.1) — Podchaser does not publish a stable public charts
   endpoint, so we fetch the rendered ``/charts`` page and hand the HTML to
   ``ingestion/scrapers/podchaser.py`` (which reads the embedded ``__NEXT_DATA__``
   / Apollo state, falling back to the DOM).

2. **Enrichment** (TT §2.2) — when ``PODCHASER_API_KEY`` / ``PODCHASER_API_SECRET``
   are configured we use the GraphQL API (OAuth2 client-credentials) to pull
   ratings, description and categories. Without keys this half is a no-op.
"""
from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

from .base import HttpClient, HttpError

log = logging.getLogger("ingestion.podchaser")

_TOKEN_MUTATION = """
mutation ($id: String!, $secret: String!) {
  requestAccessToken(input: {grant_type: CLIENT_CREDENTIALS, client_id: $id, client_secret: $secret}) {
    access_token
    expires_in
  }
}
"""

_PODCAST_QUERY = """
query ($identifier: PodcastIdentifier!) {
  podcast(identifier: $identifier) {
    id
    title
    description
    webUrl
    rssUrl
    imageUrl
    language
    applePodcastsId
    ratingAverage
    ratingCount
    numberOfEpisodes
    latestEpisodeDate
    author { name }
    categories { title slug }
  }
}
"""


class PodchaserClient:
    name = "podchaser"

    def __init__(self, http: HttpClient | None = None):
        self.api_url = settings.PODCHASER_API_URL
        self.charts_url = settings.PODCHASER_CHARTS_URL.rstrip("/")
        self.key = settings.PODCHASER_API_KEY
        self.secret = settings.PODCHASER_API_SECRET
        self.http = http or HttpClient(rate_limit_per_host=2)
        self._token = None
        self._token_exp = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.key and self.secret)

    # -- charts (GraphQL, primary) --------------------------------
    def fetch_chart(self, *, category: str, country: str, limit: int | None = None) -> list[dict]:
        """Return ranked chart nodes from the Podchaser GraphQL API.

        Returns ``[]`` (never raises) when the API is not configured or the
        query shape does not match the current schema — the scraper then falls
        back to HTML.
        """
        if not self.enabled:
            return []
        limit = limit or settings.PODCHASER_CHART_LIMIT
        cat = None if category in ("", "all", "top-100", "top") else category
        variables = {
            "first": limit,
            "category": cat,
            "country": country.upper() if country else None,
        }
        data = self.graphql(settings.PODCHASER_CHART_QUERY, variables)
        return _extract_chart_nodes(data)

    # -- charts (HTML, fallback) ---------------------------------
    def fetch_charts_html(self, *, category: str, country: str) -> str:
        """Return the raw HTML of a Podchaser chart page (best-effort fallback)."""
        cat = (category or "").strip("/")
        url = f"{self.charts_url}/{cat}" if cat and cat not in ("all", "top-100") else self.charts_url
        params = {}
        if country:
            params["country"] = country.upper()
        return self.http.get_text(url, params=params or None)

    # -- enrichment (GraphQL) ------------------------------------
    def _access_token(self) -> str | None:
        if not self.enabled:
            return None
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            try:
                data = self.http.post_json(
                    self.api_url,
                    json={"query": _TOKEN_MUTATION, "variables": {"id": self.key, "secret": self.secret}},
                    headers={"Content-Type": "application/json"},
                )
            except HttpError as exc:
                log.warning("podchaser token request failed: %s", exc)
                return None
            payload = (((data or {}).get("data") or {}).get("requestAccessToken")) or {}
            self._token = payload.get("access_token")
            self._token_exp = time.time() + float(payload.get("expires_in") or 3600)
            return self._token

    def graphql(self, query: str, variables: dict) -> dict | None:
        token = self._access_token()
        if not token:
            return None
        try:
            data = self.http.post_json(
                self.api_url,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        except HttpError as exc:
            log.warning("podchaser graphql failed: %s", exc)
            return None
        if data.get("errors"):
            log.warning("podchaser graphql errors: %s", data["errors"])
        return data.get("data")

    def podcast_metadata(self, *, podchaser_id=None, itunes_id=None, feed_url=None, title=None) -> dict | None:
        if not self.enabled:
            return None
        if podchaser_id:
            identifier = {"id": str(podchaser_id), "type": "PODCHASER"}
        elif itunes_id:
            identifier = {"id": str(itunes_id), "type": "APPLE_PODCASTS"}
        elif feed_url:
            identifier = {"id": feed_url, "type": "RSS"}
        else:
            return None
        data = self.graphql(_PODCAST_QUERY, {"identifier": identifier})
        return (data or {}).get("podcast") if data else None

    def close(self):
        self.http.close()


def _extract_chart_nodes(data) -> list[dict]:
    """Find the ranked list in a GraphQL ``data`` payload of unknown exact shape.

    Accepts either ``[{rank, podcast{...}}, ...]`` or ``[{...podcast...}, ...]``
    and returns a flat list of podcast dicts, each with a ``rank`` key.
    """
    if not data:
        return []

    def score(lst):
        if not isinstance(lst, list) or not lst or not all(isinstance(x, dict) for x in lst):
            return 0
        sample = lst[0]
        if "podcast" in sample and isinstance(sample["podcast"], dict):
            return len(lst)
        if {"title", "id"} <= set(sample):
            return len(lst)
        return 0

    best, best_score = [], 0
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            s = score(node)
            if s > best_score:
                best, best_score = node, s
            stack.extend(x for x in node if isinstance(x, (list, dict)))
        elif isinstance(node, dict):
            stack.extend(node.values())

    out = []
    for i, entry in enumerate(best, start=1):
        podcast = entry.get("podcast") if isinstance(entry.get("podcast"), dict) else entry
        rank = entry.get("rank") or podcast.get("rank") or i
        out.append({**podcast, "rank": rank})
    return out
