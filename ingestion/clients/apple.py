"""Apple Podcasts enrichment via the public iTunes Search API (no key required).

    GET https://itunes.apple.com/lookup?id=<collectionId>&entity=podcast
    GET https://itunes.apple.com/search?term=<term>&entity=podcast&limit=5

Apple rate-limits this endpoint to roughly 20 requests/minute per IP, so the
client throttles hard by default.
"""
from __future__ import annotations

import logging

from django.conf import settings

from .base import HttpClient, HttpError

log = logging.getLogger("ingestion.apple")


class ApplePodcastsClient:
    name = "apple"

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient(rate_limit_per_host=0.3, respect_robots=False)
        self.lookup_url = settings.APPLE_PODCASTS_LOOKUP_URL
        self.search_url = settings.APPLE_PODCASTS_SEARCH_URL
        self.storefront = settings.APPLE_PODCASTS_STOREFRONT

    def lookup(self, apple_id: str, *, country: str | None = None) -> dict | None:
        try:
            data = self.http.get_json(
                self.lookup_url,
                params={"id": str(apple_id), "entity": "podcast", "country": country or self.storefront},
            )
        except HttpError as exc:
            log.warning("apple lookup id=%s failed: %s", apple_id, exc)
            return None
        results = data.get("results") or []
        return results[0] if results else None

    def search(self, term: str, *, author: str = "", country: str | None = None, limit: int = 5) -> list[dict]:
        query = term if not author else f"{term} {author}"
        try:
            data = self.http.get_json(
                self.search_url,
                params={
                    "term": query,
                    "entity": "podcast",
                    "limit": limit,
                    "country": country or self.storefront,
                },
            )
        except HttpError as exc:
            log.warning("apple search %r failed: %s", query, exc)
            return []
        return data.get("results") or []

    def best_match(self, *, title: str, author: str = "", feed_url: str = "") -> dict | None:
        """Resolve a podcast we only know by name/feed to an Apple record."""
        results = self.search(title, author=author)
        if not results:
            return None
        feed_url = (feed_url or "").rstrip("/").lower()
        title_l = title.strip().lower()
        for r in results:
            if feed_url and (r.get("feedUrl") or "").rstrip("/").lower() == feed_url:
                return r
        for r in results:
            if (r.get("collectionName") or "").strip().lower() == title_l:
                return r
        return results[0]

    def close(self):
        self.http.close()
