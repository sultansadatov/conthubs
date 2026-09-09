"""Normalised representation of a scraped chart, shared by every source."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from podcasts.constants import ChartType


@dataclass
class ChartRow:
    rank: int
    title: str
    publisher: str = ""
    image_url: str = ""
    external_url: str = ""
    external_id: str = ""  # stable id for this entity within the source
    description: str = ""

    # identifiers we can opportunistically capture at scrape time — they make
    # de-duplication and enrichment cheaper later.
    rss_url: str = ""
    apple_id: str = ""
    spotify_id: str = ""
    podchaser_id: str = ""

    # episode-chart extras
    episode_title: str = ""
    episode_guid: str = ""
    episode_audio_url: str = ""
    episode_published_at: datetime | None = None

    previous_rank: int | None = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        self.rank = int(self.rank)
        self.title = (self.title or "").strip()


@dataclass
class ChartScrapeResult:
    source: str
    country: str
    category: str
    chart_type: str
    fetched_at: datetime
    rows: list[ChartRow] = field(default_factory=list)

    @property
    def is_episode_chart(self) -> bool:
        return self.chart_type == ChartType.EPISODES

    def __len__(self) -> int:
        return len(self.rows)
