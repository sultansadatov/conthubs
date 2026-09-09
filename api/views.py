"""v1 API views (TT §3).

    GET /api/v1/charts                     -> ranked chart for country/category/date/source
    GET /api/v1/podcasts                   -> paginated, searchable, filterable podcast list
    GET /api/v1/podcasts/{id}              -> full podcast metadata + recent episodes
    GET /api/v1/podcasts/{id}/episodes     -> cursor/offset-paginated episode history
    GET /api/v1/categories                 -> category taxonomy (backs the ?category= filter)
    GET /api/v1/health                     -> liveness / data-freshness probe
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.db.models import Prefetch
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from charts.models import ChartEntry
from charts.services import latest_chart_date
from podcasts.constants import ChartType, Source
from podcasts.models import Category, Episode, Podcast

from .exceptions import ChartNotAvailable
from .filters import PodcastFilter
from .pagination import (
    DefaultPageNumberPagination,
    EpisodeCursorPagination,
    EpisodeLimitOffsetPagination,
)
from .serializers import (
    CategorySerializer,
    ChartEntrySerializer,
    ChartQuerySerializer,
    EpisodeListItemSerializer,
    EpisodeSerializer,
    PodcastDetailSerializer,
    PodcastListSerializer,
)

RECENT_EPISODES_ON_DETAIL = 10

_SOURCE_ALIASES = {"spotify": Source.SPOTIFY, "podchaser": Source.PODCHASER}
_TYPE_ALIASES = {
    "podcasts": ChartType.PODCASTS,
    "podcast": ChartType.PODCASTS,
    "shows": ChartType.PODCASTS,
    "episodes": ChartType.EPISODES,
    "episode": ChartType.EPISODES,
}


class ChartView(APIView):
    """Ranked chart for a given country / category / date / source (TT §3.1)."""

    pagination_class = None

    @swagger_auto_schema(
        operation_id="charts_list",
        operation_description=(
            "Return the ranked list of podcasts (or top episodes) for the given "
            "country, category and source on a given date. `date` defaults to the "
            "most recent snapshot available. Results are ordered by `rank`."
        ),
        query_serializer=ChartQuerySerializer,
        tags=["Charts"],
    )
    def get(self, request):
        cfg = settings.CHARTS
        source = _SOURCE_ALIASES.get(
            request.query_params.get("source", cfg["DEFAULT_SOURCE"]).lower()
        )
        if source is None:
            return _bad_request("source must be one of: spotify, podchaser")

        chart_type = _TYPE_ALIASES.get(
            request.query_params.get("type", cfg["DEFAULT_TYPE"]).lower()
        )
        if chart_type is None:
            return _bad_request("type must be one of: podcasts, episodes")

        country = request.query_params.get("country", cfg["DEFAULT_COUNTRY"]).lower().strip()
        category = request.query_params.get("category", cfg["DEFAULT_CATEGORY"]).lower().strip()

        latest = latest_chart_date(
            source=source, country=country, category=category, chart_type=chart_type
        )
        date_param = request.query_params.get("date")
        if date_param:
            parsed = _parse_date(date_param)
            if parsed is None:
                return _bad_request("date must be in YYYY-MM-DD format")
            chart_date = parsed
        else:
            if latest is None:
                raise ChartNotAvailable()
            chart_date = latest

        try:
            limit = min(int(request.query_params.get("limit", cfg["MAX_ROWS"])), cfg["MAX_ROWS"])
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            return _bad_request("limit and offset must be integers")

        payload = self._build_payload(
            source, country, category, chart_type, chart_date, latest, limit, offset
        )
        if payload is None:
            raise ChartNotAvailable()
        return Response(payload)

    def _build_payload(self, source, country, category, chart_type, chart_date, latest, limit, offset):
        cache_key = "charts:resp:" + hashlib.md5(
            json.dumps(
                [str(source), country, category, str(chart_type), chart_date.isoformat(), limit, offset]
            ).encode()
        ).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        rows = list(
            ChartEntry.objects.filter(
                source=source, country=country, category=category,
                chart_type=chart_type, date=chart_date,
            )
            .select_related("podcast", "episode", "episode__podcast")
            .order_by("rank")
        )
        if not rows:
            return None

        page = rows[offset : offset + limit]
        payload = {
            "source": str(source),
            "country": country,
            "category": category,
            "chart_type": str(chart_type),
            "date": chart_date.isoformat(),
            "is_latest": latest is not None and chart_date == latest,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "results": ChartEntrySerializer(page, many=True, context={"request": self.request}).data,
        }
        cache.set(cache_key, payload, settings.CHARTS["RESPONSE_CACHE_TTL"])
        return payload


class PodcastListView(generics.ListAPIView):
    """Paginated, searchable, category-filterable list of podcasts (TT §3.2)."""

    serializer_class = PodcastListSerializer
    pagination_class = DefaultPageNumberPagination
    filterset_class = PodcastFilter
    ordering_fields = [
        "title",
        "rating_average",
        "rating_count",
        "total_episodes",
        "last_published_at",
        "updated_at",
    ]
    ordering = ["title"]

    def get_queryset(self):
        return (
            Podcast.objects.active()
            .prefetch_related("categories")
            .only(
                "id",
                "title",
                "slug",
                "publisher",
                "author",
                "image_url",
                "language",
                "country",
                "explicit",
                "rating_average",
                "rating_count",
                "publish_frequency",
                "total_episodes",
                "last_published_at",
                "enrichment_status",
                "updated_at",
            )
        )

    @swagger_auto_schema(
        operation_id="podcasts_list",
        operation_description=(
            "Paginated list of podcasts. Supports `?search=`, `?category=<slug|id>`, "
            "`?language=`, `?publish_frequency=`, `?min_rating=`, `?has_episodes=` and "
            "`?ordering=` (title, rating_average, total_episodes, last_published_at, ...)."
        ),
        tags=["Podcasts"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PodcastDetailView(generics.RetrieveAPIView):
    """Full podcast metadata + a first page of recent episodes (TT §3.2)."""

    serializer_class = PodcastDetailSerializer
    queryset = Podcast.objects.all()  # for schema introspection; real lookup in get_object()

    def get_object(self):
        if getattr(self, "swagger_fake_view", False):
            return Podcast()
        id_type = self.request.query_params.get("id_type", "").lower()
        raw = self.kwargs["pk"]
        lookup_map = {
            "apple": "apple_id",
            "itunes": "apple_id",
            "podcastindex": "podcastindex_id",
            "podchaser": "podchaser_id",
            "spotify": "spotify_id",
        }
        qs = Podcast.objects.prefetch_related(
            "categories",
            Prefetch(
                "episodes",
                queryset=Episode.objects.latest_first(),
                to_attr="prefetched_recent_episodes",
            ),
        )
        if id_type in lookup_map:
            obj = generics.get_object_or_404(qs, **{lookup_map[id_type]: str(raw)})
        else:
            obj = generics.get_object_or_404(qs, pk=raw)
        obj.prefetched_recent_episodes = obj.prefetched_recent_episodes[:RECENT_EPISODES_ON_DETAIL]
        return obj

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["recent_episode_limit"] = RECENT_EPISODES_ON_DETAIL
        return ctx

    @swagger_auto_schema(
        operation_id="podcasts_read",
        operation_description=(
            "Detailed metadata for one podcast (image, author, ratings, categories, "
            "external ids) plus its 10 most recent episodes and a link (`episodes_url`) "
            "to the cursor-paginated full episode history. Pass `?id_type=apple|"
            "podcastindex|podchaser|spotify` to look up by an external id instead of the "
            "internal id."
        ),
        tags=["Podcasts"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PodcastEpisodesView(generics.ListAPIView):
    """Cursor- (default) or offset-paginated episode history for one podcast
    (TT §3.2 "cursor-based və ya offset-based pagination")."""

    def get_serializer_class(self):
        if self.request.query_params.get("detail") in ("1", "true", "yes"):
            return EpisodeSerializer
        return EpisodeListItemSerializer

    def _use_offset(self) -> bool:
        return self.request.query_params.get("paginate", "cursor").lower() == "offset"

    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            cls = EpisodeLimitOffsetPagination if self._use_offset() else EpisodeCursorPagination
            self._paginator = cls()
        return self._paginator

    def get_queryset(self):
        podcast_id = self.kwargs.get("pk")
        if getattr(self, "swagger_fake_view", False) or podcast_id is None:
            return Episode.objects.none()
        if not Podcast.objects.filter(pk=podcast_id).exists():
            from rest_framework.exceptions import NotFound

            raise NotFound("Podcast not found.")

        qs = Episode.objects.filter(podcast_id=podcast_id)
        # cursor pagination requires a non-null, ordered key, so it always
        # excludes undated episodes; offset mode honours ?published_only=
        # (default true) and keeps undated episodes last.
        published_only = self.request.query_params.get("published_only", "true").lower() in ("1", "true", "yes")
        if published_only or not self._use_offset():
            qs = qs.filter(published_at__isnull=False)
        return qs.latest_first()

    @swagger_auto_schema(
        operation_id="podcasts_episodes",
        operation_description=(
            "Episode history for a podcast, newest first. Cursor pagination by default "
            "(`?cursor=`); pass `?paginate=offset` for limit/offset. `?detail=1` returns "
            "the full episode payload (descriptions, media metadata)."
        ),
        tags=["Podcasts"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    pagination_class = DefaultPageNumberPagination

    def get_queryset(self):
        qs = Category.objects.select_related("parent").order_by("source", "name")
        source = self.request.query_params.get("source")
        return qs.filter(source=source) if source else qs

    @swagger_auto_schema(operation_id="categories_list", tags=["Podcasts"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class HealthView(APIView):
    """Liveness + data-freshness probe. Kept O(1): it checks DB connectivity and
    the newest chart date (index-only), never a full COUNT of the partitioned
    history table."""

    swagger_schema = None

    def get(self, request):
        now = timezone.now()
        try:
            last_chart = (
                ChartEntry.objects.order_by("-date").values_list("date", flat=True).first()
            )
        except Exception as exc:  # DB unreachable
            return Response(
                {"status": "down", "time": now.isoformat(), "reason": str(exc)[:200]},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = {
            "status": "ok",
            "time": now.isoformat(),
            "latest_chart_date": last_chart.isoformat() if last_chart else None,
        }
        if last_chart is None:
            data["status"] = "warming-up"
        elif (now.date() - last_chart).days > 3:
            data["status"] = "degraded"
            data["reason"] = "chart data is stale (>3 days old)"
            return Response(data, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(data)


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def _bad_request(message: str) -> Response:
    return Response(
        {"error": {"status": 400, "code": "invalid_parameter", "detail": message}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None
