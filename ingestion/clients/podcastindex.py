"""PodcastIndex API client (https://podcastindex.org).

Requires ``PODCASTINDEX_API_KEY`` / ``PODCASTINDEX_API_SECRET``. When they are
not configured every method is a safe no-op so the pipeline still runs on Apple
data alone (TT §2.2: "biri və ya bir neçəsi ilə").

Auth per request:
    X-Auth-Key    = api key
    X-Auth-Date   = unix seconds
    Authorization = sha1_hex(api_key + api_secret + X-Auth-Date)
"""
from __future__ import annotations

import hashlib
import logging
import time

from django.conf import settings

from .base import HttpClient, HttpError

log = logging.getLogger("ingestion.podcastindex")


class PodcastIndexClient:
    name = "podcastindex"

    def __init__(self, http: HttpClient | None = None):
        self.api_key = settings.PODCASTINDEX_API_KEY
        self.api_secret = settings.PODCASTINDEX_API_SECRET
        self.base = settings.PODCASTINDEX_API_URL.rstrip("/")
        self.http = http or HttpClient(rate_limit_per_host=4, respect_robots=False)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    # -- auth ---------------------------------------------------------
    def _headers(self) -> dict:
        auth_date = str(int(time.time()))
        token = hashlib.sha1(
            (self.api_key + self.api_secret + auth_date).encode("utf-8")
        ).hexdigest()
        return {
            "X-Auth-Key": self.api_key,
            "X-Auth-Date": auth_date,
            "Authorization": token,
        }

    def _get(self, path: str, params: dict) -> dict | None:
        if not self.enabled:
            return None
        try:
            return self.http.get_json(f"{self.base}{path}", params=params, headers=self._headers())
        except HttpError as exc:
            log.warning("podcastindex %s failed: %s", path, exc)
            return None

    # -- podcast lookup --------------------------------------------
    def by_feed_url(self, feed_url: str) -> dict | None:
        data = self._get("/podcasts/byfeedurl", {"url": feed_url})
        return (data or {}).get("feed") or None

    def by_itunes_id(self, itunes_id: str) -> dict | None:
        data = self._get("/podcasts/byitunesid", {"id": str(itunes_id)})
        return (data or {}).get("feed") or None

    def by_guid(self, guid: str) -> dict | None:
        data = self._get("/podcasts/byguid", {"guid": guid})
        return (data or {}).get("feed") or None

    def search(self, term: str, *, limit: int = 5) -> list[dict]:
        data = self._get("/search/byterm", {"q": term, "max": limit})
        return (data or {}).get("feeds") or []

    # -- episodes -------------------------------------------------
    def episodes_by_feed_id(self, feed_id, *, max_results: int = 1000, since: int | None = None) -> list[dict]:
        params = {"id": str(feed_id), "max": max_results}
        if since:
            params["since"] = since
        data = self._get("/episodes/byfeedid", params)
        return (data or {}).get("items") or []

    def episodes_by_feed_url(self, feed_url, *, max_results: int = 1000) -> list[dict]:
        data = self._get("/episodes/byfeedurl", {"url": feed_url, "max": max_results})
        return (data or {}).get("items") or []

    def close(self):
        self.http.close()
