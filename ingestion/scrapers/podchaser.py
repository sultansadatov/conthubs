"""Parse a Podchaser ``/charts`` HTML page into a :class:`ChartScrapeResult`.

Strategy, most-reliable first:

1. read the ``__NEXT_DATA__`` / ``__APOLLO_STATE__`` JSON embedded in the page
   and walk it for the ranked list of podcast objects;
2. fall back to parsing the visible chart list with BeautifulSoup.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup
from django.utils import timezone

from podcasts.constants import ChartType, Source

from ..clients.podchaser import PodchaserClient
from .base import ChartRow, ChartScrapeResult

log = logging.getLogger("ingestion.podchaser")

_JSON_BLOB_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(?P<body>.*?)</script>', re.DOTALL
)
_APOLLO_RE = re.compile(
    r'window\.__APOLLO_STATE__\s*=\s*(?P<body>\{.*?\});?\s*</script>', re.DOTALL
)
_PODCAST_KEYS = {"title", "id"}
_PODCAST_HINT_KEYS = {"imageUrl", "image_url", "rssUrl", "applePodcastsId", "webUrl", "author"}


def _first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


class PodchaserChartScraper:
    source = Source.PODCHASER

    def __init__(self, client: PodchaserClient | None = None):
        self.client = client or PodchaserClient()

    def scrape(self, *, country: str, category: str) -> ChartScrapeResult:
        # 1) official GraphQL API (needs PODCHASER_API_KEY/SECRET)
        try:
            nodes = self.client.fetch_chart(category=category, country=country)
        except Exception:  # noqa: BLE001 - fall through to HTML
            log.exception("podchaser graphql chart fetch failed")
            nodes = []
        if nodes:
            result = self._empty_result(country, category)
            for idx, node in enumerate(nodes, start=1):
                row = self._to_row(node, idx)
                if row.title:
                    result.rows.append(row)
            log.info("scraped podchaser %s/%s: %d rows (api)", category, country, len(result.rows))
            return result

        # 2) HTML fallback (usually empty — the page is client-rendered)
        try:
            html = self.client.fetch_charts_html(category=category, country=country)
        except Exception:  # noqa: BLE001
            log.exception("podchaser html chart fetch failed")
            return self._empty_result(country, category)
        return self.parse(html, country=country, category=category)

    def _empty_result(self, country: str, category: str) -> ChartScrapeResult:
        return ChartScrapeResult(
            source=self.source,
            country=country.lower(),
            category=category or "all",
            chart_type=ChartType.PODCASTS,
            fetched_at=timezone.now(),
        )

    # -- parsing (kept pure & static so tests can feed fixture HTML) -----
    def parse(self, html: str, *, country: str, category: str) -> ChartScrapeResult:
        result = self._empty_result(country, category)
        entries = self._from_json_blob(html) or self._from_dom(html)
        for idx, raw in enumerate(entries, start=1):
            row = self._to_row(raw, idx)
            if row and row.title:
                result.rows.append(row)
        log.info("scraped podchaser %s/%s: %d rows", category, country, len(result.rows))
        return result

    # -- strategy 1: embedded JSON -----------------------------------
    def _from_json_blob(self, html: str) -> list[dict]:
        for rx in (_JSON_BLOB_RE, _APOLLO_RE):
            m = rx.search(html or "")
            if not m:
                continue
            try:
                blob = json.loads(m.group("body"))
            except ValueError:
                continue
            rows = _find_ranked_podcasts(blob)
            if rows:
                return rows
        return []

    # -- strategy 2: DOM -------------------------------------------
    def _from_dom(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html or "", "lxml")
        out: list[dict] = []
        # chart rows tend to be <li>/<div> carrying a link to /podcasts/<slug>
        for node in soup.select('[class*="chart"] a[href*="/podcasts/"], li a[href*="/podcasts/"]'):
            title = node.get_text(strip=True)
            if not title:
                img = node.find("img")
                title = img.get("alt", "").strip() if img else ""
            if not title:
                continue
            img = node.find("img")
            out.append(
                {
                    "title": title,
                    "webUrl": node.get("href", ""),
                    "imageUrl": (img.get("src") or img.get("data-src") or "") if img else "",
                }
            )
            if len(out) >= 200:
                break
        # de-dup preserving order
        seen, deduped = set(), []
        for r in out:
            key = r["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        return deduped

    def _to_row(self, raw: dict, idx: int) -> ChartRow:
        rank = int(_first(raw, "rank", "position", "chartRank", default=idx) or idx)
        title = str(_first(raw, "title", "name"))
        author = _first(raw, "author", "publisherName", "publisher")
        if isinstance(author, dict):
            author = author.get("name", "")
        web = str(_first(raw, "webUrl", "url", "link"))
        if web and web.startswith("/"):
            web = f"https://www.podchaser.com{web}"
        pid = str(_first(raw, "id", "podchaserId", default=""))
        return ChartRow(
            rank=rank,
            title=title,
            publisher=str(author or ""),
            image_url=str(_first(raw, "imageUrl", "image_url", "image")),
            description=str(_first(raw, "description")),
            external_url=web,
            external_id=pid or web,
            podchaser_id=pid if pid.isdigit() else "",
            rss_url=str(_first(raw, "rssUrl", "rss_url", "feedUrl")),
            apple_id=str(_first(raw, "applePodcastsId", "appleId", "itunesId", default="")),
            raw=raw,
        )

    def close(self):
        self.client.close()


def _looks_like_podcast(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    keys = set(d.keys())
    if not _PODCAST_KEYS.issubset(keys):
        return False
    return bool(keys & _PODCAST_HINT_KEYS) or "podcast" in str(d.get("__typename", "")).lower()


def _find_ranked_podcasts(blob) -> list[dict]:
    """Find the first list of podcast-shaped dicts in a nested JSON structure."""
    best: list[dict] = []
    stack = [blob]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            podcasts = [x for x in node if _looks_like_podcast(x)]
            if len(podcasts) > len(best):
                best = podcasts
            stack.extend(x for x in node if isinstance(x, (list, dict)))
        elif isinstance(node, dict):
            stack.extend(v for v in node.values() if isinstance(v, (list, dict)))
    return best
