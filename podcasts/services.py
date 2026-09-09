"""Write-side domain services: podcast de-duplication, episode UPSERT and
aggregate recomputation.

Everything that mutates :class:`~podcasts.models.Podcast` / :class:`Episode`
goes through here so the de-dup and UPSERT rules (TT §3) live in exactly one
place and are unit-tested in isolation.
"""
from __future__ import annotations

import hashlib
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from django.db import DataError, IntegrityError, transaction
from django.utils.text import slugify

from .constants import PublishFrequency, Source
from .models import Category, Episode, Podcast, build_match_key

log = logging.getLogger(__name__)

# external-id fields tried, in order, when resolving a podcast
_ID_FIELDS = (
    "rss_url",
    "apple_id",
    "podcastindex_id",
    "podcastindex_guid",
    "podchaser_id",
    "spotify_id",
)


@dataclass
class PodcastResolution:
    podcast: Podcast
    created: bool
    matched_on: str = ""


def _clean_ids(ids: dict) -> dict:
    out = {}
    for key in _ID_FIELDS:
        val = ids.get(key)
        if val is None:
            continue
        val = str(val).strip()
        if val:
            out[key] = val
    return out


@transaction.atomic
def resolve_podcast(
    *,
    title: str,
    publisher: str = "",
    defaults: dict | None = None,
    source: str = Source.INTERNAL,
    **identifiers,
) -> PodcastResolution:
    """Find an existing podcast by any stable id, else by a normalised
    title/publisher key, else create it. Missing ids on a matched row are
    back-filled so future look-ups are cheaper and duplicates cannot appear.
    """
    ids = _clean_ids(identifiers)
    defaults = dict(defaults or {})

    podcast: Podcast | None = None
    matched_on = ""

    for field_name in _ID_FIELDS:
        value = ids.get(field_name)
        if not value:
            continue
        podcast = (
            Podcast.objects.select_for_update()
            .filter(**{field_name: value})
            .first()
        )
        if podcast:
            matched_on = field_name
            break

    if podcast is None and title:
        match_key = build_match_key(title, publisher)
        if match_key:
            podcast = (
                Podcast.objects.select_for_update()
                .filter(match_key=match_key)
                .order_by("id")
                .first()
            )
            if podcast:
                matched_on = "match_key"

    if podcast is None:
        podcast = Podcast(
            title=title.strip()[:500],
            publisher=(publisher or "").strip()[:300],
            match_key=build_match_key(title, publisher),
            first_seen_source=source,
            **ids,
            **{k: v for k, v in defaults.items() if v not in (None, "")},
        )
        try:
            with transaction.atomic():
                podcast.save()
        except IntegrityError:
            # a concurrent scrape (Spotify + Podchaser running together) created
            # the same podcast first — re-resolve by whatever id collided.
            existing = None
            for field_name in _ID_FIELDS:
                if ids.get(field_name):
                    existing = Podcast.objects.filter(**{field_name: ids[field_name]}).first()
                    if existing:
                        matched_on = field_name
                        break
            if existing is None:
                existing = Podcast.objects.filter(match_key=podcast.match_key).order_by("id").first()
                matched_on = "match_key"
            if existing is None:
                raise
            podcast = existing
        else:
            log.info("created podcast id=%s title=%r via %s", podcast.pk, podcast.title, source)
            return PodcastResolution(podcast=podcast, created=True, matched_on="")

    # back-fill any identifier / metadata we now know but were missing
    dirty: list[str] = []
    for field_name, value in ids.items():
        if not getattr(podcast, field_name):
            setattr(podcast, field_name, value)
            dirty.append(field_name)
    for field_name, value in defaults.items():
        if value in (None, "") or field_name not in _BACKFILLABLE:
            continue
        if not getattr(podcast, field_name, None):
            setattr(podcast, field_name, value)
            dirty.append(field_name)
    if not podcast.match_key and title:
        podcast.match_key = build_match_key(title, publisher)
        dirty.append("match_key")
    if dirty:
        podcast.save(update_fields=list(set(dirty)) + ["updated_at"])

    return PodcastResolution(podcast=podcast, created=False, matched_on=matched_on)


_BACKFILLABLE = {
    "description",
    "image_url",
    "website_url",
    "language",
    "country",
    "author",
    "itunes_type",
}


# ---------------------------------------------------------------------------
#  Episode UPSERT  (TT §3 "GUID və ya URL vasitəsilə UPSERT")
# ---------------------------------------------------------------------------
_EPISODE_UPDATE_FIELDS = [
    "title",
    "description",
    "summary",
    "published_at",
    "duration_seconds",
    "episode_number",
    "season_number",
    "episode_type",
    "explicit",
    "image_url",
    "audio_url",
    "audio_length_bytes",
    "audio_type",
    "external_ids",
    "raw_metadata",
    "source",
    "updated_at",
]


_INT4_MAX = 2_147_483_647
_INT8_MAX = 9_223_372_036_854_775_807


def _uint(value, *, big: bool = False) -> int | None:
    """Non-negative int within Postgres range, else None. Feeds sometimes stuff
    timestamps into ``itunes:episode`` etc., which would overflow int4."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= (_INT8_MAX if big else _INT4_MAX) else None


def episode_guid(entry: dict, podcast: Podcast) -> str:
    """Best available stable identifier for an episode.

    Preference: explicit feed <guid> -> enclosure/audio url -> apple/other
    episode id -> deterministic hash of (title, published). Guarantees a
    non-empty, stable value so the ``(podcast, guid)`` UPSERT key always holds.
    """
    for key in ("guid", "id"):
        val = (entry.get(key) or "").strip() if isinstance(entry.get(key), str) else entry.get(key)
        if val:
            return str(val)[:500]
    audio = (entry.get("audio_url") or "").strip()
    if audio:
        return audio[:500]
    ext = entry.get("external_ids") or {}
    for val in ext.values():
        if val:
            return f"ext:{val}"[:500]
    basis = f"{podcast.pk}:{entry.get('title', '')}:{entry.get('published_at', '')}"
    return "sha1:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    total: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "total": self.total,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def upsert_episodes(podcast: Podcast, entries: Sequence[dict]) -> UpsertResult:
    """Bulk-UPSERT a batch of parsed episode dicts for one podcast.

    ``entries`` items may contain any subset of Episode fields plus an optional
    ``guid``; :func:`episode_guid` fills the gap. Idempotent: running twice with
    the same feed produces 0 new rows.
    """
    result = UpsertResult()
    if not entries:
        return result

    seen: set[str] = set()
    objs: list[Episode] = []
    for raw in entries:
        try:
            guid = episode_guid(raw, podcast)
            if guid in seen:
                result.skipped += 1
                continue
            seen.add(guid)
            objs.append(
                Episode(
                    podcast=podcast,
                    guid=guid,
                    title=(raw.get("title") or "Untitled episode")[:600],
                    description=raw.get("description") or "",
                    summary=raw.get("summary") or "",
                    published_at=raw.get("published_at"),
                    # feeds are dirty — coerce the unsigned-int fields so a bad
                    # value (e.g. a negative itunes:season) can't trip a CHECK
                    # constraint and abort the whole batch.
                    duration_seconds=_uint(raw.get("duration_seconds")),
                    episode_number=_uint(raw.get("episode_number")),
                    season_number=_uint(raw.get("season_number")),
                    episode_type=(raw.get("episode_type") or "")[:16],
                    explicit=bool(raw.get("explicit", False)),
                    image_url=(raw.get("image_url") or "")[:1000],
                    audio_url=(raw.get("audio_url") or "")[:1500],
                    audio_length_bytes=_uint(raw.get("audio_length_bytes"), big=True),
                    audio_type=(raw.get("audio_type") or "")[:60],
                    external_ids=raw.get("external_ids") or {},
                    raw_metadata=raw.get("raw_metadata") or {},
                    source=raw.get("source") or Source.RSS,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            result.errors.append(str(exc))

    if not objs:
        return result

    existing = set(
        Episode.objects.filter(
            podcast=podcast, guid__in=[o.guid for o in objs]
        ).values_list("guid", flat=True)
    )
    update_fields = [f for f in _EPISODE_UPDATE_FIELDS if f != "updated_at"]

    def _bulk(rows):
        with transaction.atomic():
            Episode.objects.bulk_create(
                rows, update_conflicts=True, unique_fields=["podcast", "guid"],
                update_fields=update_fields, batch_size=500,
            )

    try:
        _bulk(objs)
        written = objs
    except (IntegrityError, DataError):
        # one malformed episode must not sink the whole feed sync — retry
        # row-by-row and skip only the offender(s).
        written = []
        for obj in objs:
            try:
                _bulk([obj])
                written.append(obj)
            except (IntegrityError, DataError) as exc:
                log.warning("skipped bad episode podcast=%s guid=%r: %s", podcast.pk, obj.guid, exc)
                result.errors.append(f"{obj.guid}: {exc}")
                result.skipped += 1

    written_guids = {o.guid for o in written}
    result.total = len(written)
    result.updated = len(existing & written_guids)
    result.created = result.total - result.updated
    return result


# ---------------------------------------------------------------------------
#  Aggregates
# ---------------------------------------------------------------------------
def infer_publish_frequency(published_dates: Sequence[datetime]) -> str:
    """Classify cadence from the gaps between the most recent episodes."""
    dates = sorted([d for d in published_dates if d], reverse=True)[:12]
    if len(dates) < 3:
        return PublishFrequency.UNKNOWN
    gaps_days = [
        (dates[i] - dates[i + 1]).total_seconds() / 86400.0
        for i in range(len(dates) - 1)
    ]
    median_gap = statistics.median(gaps_days)
    if median_gap <= 0:
        return PublishFrequency.UNKNOWN
    if median_gap <= 2:
        return PublishFrequency.DAILY
    if median_gap <= 10:
        return PublishFrequency.WEEKLY
    if median_gap <= 20:
        return PublishFrequency.BIWEEKLY
    if median_gap <= 45:
        return PublishFrequency.MONTHLY
    return PublishFrequency.SPORADIC


def recompute_podcast_aggregates(podcast: Podcast, *, save: bool = True) -> dict:
    """Refresh ``total_episodes`` / ``last_published_at`` / ``publish_frequency``."""
    qs = podcast.episodes.all()
    total = qs.count()
    recent_dates = list(
        qs.published().order_by("-published_at").values_list("published_at", flat=True)[:12]
    )
    last_pub = recent_dates[0] if recent_dates else None
    frequency = infer_publish_frequency(recent_dates)

    changed = (
        podcast.total_episodes != total
        or podcast.last_published_at != last_pub
        or podcast.publish_frequency != frequency
    )
    podcast.total_episodes = total
    podcast.last_published_at = last_pub
    podcast.publish_frequency = frequency
    if save and changed:
        podcast.save(
            update_fields=[
                "total_episodes",
                "last_published_at",
                "publish_frequency",
                "updated_at",
            ]
        )
    return {
        "total_episodes": total,
        "last_published_at": last_pub,
        "publish_frequency": frequency,
        "changed": changed,
    }


def attach_categories(podcast: Podcast, categories: Iterable[tuple[str, str]]) -> None:
    """``categories`` = iterable of ``(source, name)``; get-or-creates each and
    links it to the podcast without dropping links from other sources."""
    to_add = []
    seen = set()
    for source, name in categories:
        name = (name or "").strip()
        slug = slugify(name)[:160]
        if not slug or (source, slug) in seen:
            continue
        seen.add((source, slug))
        obj, _ = Category.objects.get_or_create(
            source=source or Source.INTERNAL,
            slug=slug,
            defaults={"name": name[:150]},
        )
        to_add.append(obj)
    if to_add:
        podcast.categories.add(*to_add)
