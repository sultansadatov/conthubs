"""Episode discovery & sync (TT §2.3).

For each podcast we periodically re-read its feed and UPSERT every episode,
keyed on ``(podcast, guid)`` so re-runs never duplicate rows. RSS is the primary
source; the PodcastIndex episodes API is the fallback when a podcast has no
usable RSS url.

The feed body is fetched through :class:`ingestion.clients.base.HttpClient`
(timeout, retries, rate-limiting, conditional GET) rather than
``feedparser.parse(url)`` directly, because feedparser's built-in fetch has *no
timeout* — a single hanging feed server would otherwise pin a Celery worker
until the task hard-limit.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone as _tz

import feedparser
from django.utils import timezone
from django.utils.http import http_date, parse_http_date_safe

from podcasts.constants import Source
from podcasts.models import Podcast
from podcasts.services import recompute_podcast_aggregates, upsert_episodes

from .clients.base import HttpClient, HttpError
from .clients.podcastindex import PodcastIndexClient

log = logging.getLogger("ingestion.episodes")

_HMS_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_MAX_FEED_BYTES = 25 * 1024 * 1024


def sync_podcast_episodes(podcast: Podcast, *, force: bool = False, max_episodes: int | None = None) -> dict:
    """Fetch new/changed episodes for one podcast and UPSERT them."""
    entries: list[dict] = []
    used = ""
    conditional: dict = {}
    not_modified = False

    if podcast.rss_url:
        entries, conditional, not_modified = _from_rss(podcast, force=force)
        used = "rss"
    if not entries and not not_modified and (podcast.podcastindex_id or podcast.rss_url):
        pi_entries = _from_podcastindex(podcast)
        if pi_entries:
            entries, used = pi_entries, "podcastindex"

    if max_episodes:
        entries = entries[:max_episodes]

    result = upsert_episodes(podcast, entries) if entries else None

    fields = ["episodes_synced_at", "updated_at"]
    podcast.episodes_synced_at = timezone.now()
    if conditional.get("etag"):
        podcast.feed_etag = conditional["etag"][:250]
        fields.append("feed_etag")
    if conditional.get("modified"):
        podcast.feed_last_modified = conditional["modified"][:100]
        fields.append("feed_last_modified")
    podcast.save(update_fields=fields)

    # only re-derive aggregates when the episode set actually moved
    if result and (result.created or result.updated):
        agg = recompute_podcast_aggregates(podcast)
    else:
        agg = {"total_episodes": podcast.total_episodes, "last_published_at": podcast.last_published_at}

    summary = {
        "podcast_id": podcast.pk,
        "source": used or "none",
        "fetched": len(entries),
        "not_modified": not_modified,
        **(result.as_dict() if result else {"created": 0, "updated": 0, "total": 0}),
        "total_episodes": agg["total_episodes"],
        "last_published_at": agg["last_published_at"].isoformat() if agg["last_published_at"] else None,
    }
    log.info("episode sync id=%s: %s", podcast.pk, summary)
    return summary


# ---------------------------------------------------------------------------
#  RSS
# ---------------------------------------------------------------------------
def _from_rss(podcast: Podcast, *, force: bool) -> tuple[list[dict], dict, bool]:
    """Return ``(episode_dicts, conditional_headers, not_modified)``."""
    headers = {}
    if not force and podcast.feed_etag:
        headers["If-None-Match"] = podcast.feed_etag
    if not force and podcast.feed_last_modified:
        headers["If-Modified-Since"] = podcast.feed_last_modified

    http = HttpClient(rate_limit_per_host=1)
    try:
        resp = http.get(podcast.rss_url, headers=headers)
    except HttpError as exc:
        log.warning("feed %s fetch failed: %s", podcast.rss_url, exc)
        return [], {}, False
    finally:
        http.close()

    if resp.status_code == 304:
        log.info("feed %s not modified (304)", podcast.rss_url)
        return [], {}, True

    body = resp.content[:_MAX_FEED_BYTES]
    parsed = feedparser.parse(body)
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        log.warning("feed %s parse error: %s", podcast.rss_url, getattr(parsed, "bozo_exception", ""))
        return [], {}, False

    meta = {}
    if resp.headers.get("ETag"):
        meta["etag"] = resp.headers["ETag"]
    last_mod = resp.headers.get("Last-Modified")
    if last_mod and parse_http_date_safe(last_mod) is not None:
        meta["modified"] = last_mod
    elif getattr(parsed, "updated_parsed", None):
        meta["modified"] = http_date(calendar.timegm(parsed.updated_parsed))

    feed_image = _dig(parsed, "feed", "image", "href") or _dig(parsed, "feed", "itunes_image", "href")
    return [_rss_entry_to_dict(e, feed_image) for e in parsed.entries], meta, False


def _rss_entry_to_dict(entry, feed_image: str) -> dict:
    enclosure = None
    for enc in getattr(entry, "enclosures", []) or []:
        if (enc.get("type") or "").startswith("audio") or enc.get("href"):
            enclosure = enc
            break

    description = ""
    if getattr(entry, "content", None):
        description = entry.content[0].get("value", "")
    description = description or entry.get("summary", "") or entry.get("description", "")

    return {
        "guid": entry.get("id") or entry.get("guid") or entry.get("link") or "",
        "title": entry.get("title", "").strip(),
        "description": description,
        "summary": entry.get("summary", ""),
        "published_at": _struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed")),
        "duration_seconds": _parse_duration(entry.get("itunes_duration")),
        "episode_number": _to_int(entry.get("itunes_episode")),
        "season_number": _to_int(entry.get("itunes_season")),
        "episode_type": (entry.get("itunes_episodetype") or "")[:16],
        "explicit": str(entry.get("itunes_explicit", "")).lower() in ("yes", "true", "explicit"),
        "image_url": _dig(entry, "image", "href") or entry.get("itunes_image", {}).get("href", "") or feed_image or "",
        "audio_url": (enclosure or {}).get("href", "") if enclosure else "",
        "audio_type": (enclosure or {}).get("type", "") if enclosure else "",
        "audio_length_bytes": _to_int((enclosure or {}).get("length")) if enclosure else None,
        "source": Source.RSS,
        "raw_metadata": {
            "link": entry.get("link", ""),
            "author": entry.get("author", ""),
        },
    }


# ---------------------------------------------------------------------------
#  PodcastIndex
# ---------------------------------------------------------------------------
def _from_podcastindex(podcast: Podcast) -> list[dict]:
    client = PodcastIndexClient()
    if not client.enabled:
        return []
    try:
        items = []
        if podcast.podcastindex_id:
            items = client.episodes_by_feed_id(podcast.podcastindex_id, max_results=1000)
        if not items and podcast.rss_url:
            items = client.episodes_by_feed_url(podcast.rss_url, max_results=1000)
        return [_pi_item_to_dict(it) for it in items]
    finally:
        client.close()


def _pi_item_to_dict(item: dict) -> dict:
    return {
        "guid": str(item.get("guid") or item.get("id") or item.get("enclosureUrl") or ""),
        "title": (item.get("title") or "").strip(),
        "description": item.get("description") or "",
        "published_at": _unix_to_dt(item.get("datePublished")),
        "duration_seconds": _to_int(item.get("duration")),
        "episode_number": _to_int(item.get("episode")),
        "season_number": _to_int(item.get("season")),
        "episode_type": (item.get("episodeType") or "")[:16],
        "explicit": str(item.get("explicit")) in ("1", "true", "True"),
        "image_url": item.get("image") or item.get("feedImage") or "",
        "audio_url": item.get("enclosureUrl") or "",
        "audio_type": item.get("enclosureType") or "",
        "audio_length_bytes": _to_int(item.get("enclosureLength")),
        "external_ids": {"podcastindex": item.get("id")},
        "source": Source.PODCASTINDEX,
        "raw_metadata": {"link": item.get("link", "")},
    }


# ---------------------------------------------------------------------------
#  parsing helpers
# ---------------------------------------------------------------------------
def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=_tz.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _unix_to_dt(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=_tz.utc) if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_duration(value) -> int | None:
    if value in (None, ""):
        return None
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    if _HMS_RE.match(value):
        parts = [int(p) for p in value.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts
        return h * 3600 + m * 60 + s
    try:
        return _non_negative(int(float(value)))
    except ValueError:
        return None


def _to_int(value) -> int | None:
    """Parse an unsigned integer field (episode/season number, byte length,
    duration). Feeds are dirty — negatives / garbage become ``None`` rather than
    blowing up a ``PositiveIntegerField`` CHECK constraint."""
    try:
        n = int(str(value).strip()) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return _non_negative(n)


def _non_negative(n: int | None) -> int | None:
    return n if (n is not None and n >= 0) else None


def _dig(obj, *path):
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
        if cur is None:
            return None
    return cur
