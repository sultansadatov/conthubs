"""Turn Spotify chart API rows into a :class:`ChartScrapeResult`.

Rank is the array position (the endpoint returns rows already ordered).
``chartRankMove`` (UP / DOWN / NEW / UNCHANGED) only tells us *whether* a show is
new; the authoritative rank delta is computed later by
``charts.services.backfill_rank_movement`` against our own stored history.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from podcasts.constants import ChartType, Source

from ..clients.spotify import SpotifyChartsClient
from .base import ChartRow, ChartScrapeResult

log = logging.getLogger("ingestion.spotify")


def _first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def _spotify_id(uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("spotify:"):
        return uri.rsplit(":", 1)[-1]
    if "open.spotify.com" in uri:
        return uri.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    return ""


def _open_url(uri: str, kind: str) -> str:
    sid = _spotify_id(uri)
    return f"https://open.spotify.com/{kind}/{sid}" if sid else ""


class SpotifyChartScraper:
    source = Source.SPOTIFY

    def __init__(self, client: SpotifyChartsClient | None = None):
        self.client = client or SpotifyChartsClient()

    def scrape(self, *, region: str, category: str) -> ChartScrapeResult:
        chart_type = (
            ChartType.EPISODES
            if category in settings.SPOTIFY_EPISODE_CATEGORIES
            else ChartType.PODCASTS
        )
        raw_rows = self.client.fetch_chart(category=category, region=region)
        result = ChartScrapeResult(
            source=self.source,
            country=region.lower(),
            category=category,
            chart_type=chart_type,
            fetched_at=timezone.now(),
        )
        for idx, raw in enumerate(raw_rows, start=1):
            row = self._parse_row(raw, idx, chart_type)
            if row and row.title:
                result.rows.append(row)
        log.info("scraped spotify %s/%s: %d rows", category, region, len(result.rows))
        return result

    def _parse_row(self, raw: dict, idx: int, chart_type: str) -> ChartRow:
        rank = int(_first(raw, "rank", "chartRank", "position", default=idx) or idx)
        move = str(_first(raw, "chartRankMove", "rankMove", default="")).upper()
        prev = _first(raw, "previousRank", "prevRank", default=None)
        previous_rank = int(prev) if isinstance(prev, (int, float)) and prev else None

        show_uri = str(_first(raw, "showUri", "showUrl", "uri"))
        spotify_show_id = _spotify_id(show_uri)
        row = ChartRow(
            rank=rank,
            title=str(_first(raw, "showName", "show_title", "name")),
            publisher=str(_first(raw, "showPublisher", "publisher", "publisherName")),
            image_url=str(_first(raw, "showImageUrl", "imageUrl", "image")),
            description=str(_first(raw, "showDescription", "description")),
            previous_rank=previous_rank,
            spotify_id=spotify_show_id,
            external_id=show_uri or spotify_show_id,
            external_url=_open_url(show_uri, "show"),
            raw={**raw, "_chartRankMove": move, "_isNew": move == "NEW"},
        )

        if chart_type == ChartType.EPISODES:
            ep_uri = str(_first(raw, "episodeUri", "episodeUrl"))
            row.episode_title = str(_first(raw, "episodeName", "episodeTitle"))
            row.episode_guid = ep_uri or _spotify_id(ep_uri)
            row.external_id = ep_uri or row.external_id
            row.external_url = _open_url(ep_uri, "episode") or row.external_url
            ep_img = str(_first(raw, "episodeImageUrl", default=""))
            if ep_img:
                row.image_url = ep_img
            if not row.title:
                row.title = row.episode_title
        return row

    def close(self):
        self.client.close()
