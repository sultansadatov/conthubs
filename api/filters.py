"""django-filter FilterSets for the podcast list endpoint (TT §3.2)."""
from __future__ import annotations

from django.db.models import Q
from django_filters import rest_framework as filters

from podcasts.models import Podcast


class PodcastFilter(filters.FilterSet):
    # free-text search across title / publisher / author / description
    search = filters.CharFilter(method="filter_search", label="search")
    # category by slug (any source) or by numeric id
    category = filters.CharFilter(method="filter_category", label="category slug or id")
    language = filters.CharFilter(field_name="language", lookup_expr="iexact")
    publisher = filters.CharFilter(field_name="publisher", lookup_expr="icontains")
    publish_frequency = filters.CharFilter(field_name="publish_frequency", lookup_expr="iexact")
    min_rating = filters.NumberFilter(field_name="rating_average", lookup_expr="gte")
    has_episodes = filters.BooleanFilter(method="filter_has_episodes")
    updated_since = filters.IsoDateTimeFilter(field_name="updated_at", lookup_expr="gte")

    class Meta:
        model = Podcast
        fields = [
            "search",
            "category",
            "language",
            "publisher",
            "publish_frequency",
            "min_rating",
            "has_episodes",
            "updated_since",
        ]

    def filter_search(self, queryset, name, value):
        # matched against the trigram-indexed columns only (title / publisher /
        # author) so `?search=` stays fast as the catalog grows — see
        # podcasts/migrations/0002_search_trgm.py. `description` is intentionally
        # excluded (multi-KB text, no trigram index).
        value = (value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(title__icontains=value)
            | Q(publisher__icontains=value)
            | Q(author__icontains=value)
        )

    def filter_category(self, queryset, name, value):
        value = (value or "").strip()
        if not value:
            return queryset
        if value.isdigit():
            return queryset.filter(categories__id=int(value)).distinct()
        return queryset.filter(categories__slug=value.lower()).distinct()

    def filter_has_episodes(self, queryset, name, value):
        if value is True:
            return queryset.filter(total_episodes__gt=0)
        if value is False:
            return queryset.filter(total_episodes=0)
        return queryset
