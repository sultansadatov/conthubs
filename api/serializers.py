"""Serializers for the v1 API."""
from __future__ import annotations

from rest_framework import serializers
from rest_framework.reverse import reverse

from podcasts.models import Category, Episode, Podcast


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "source", "parent")


class EpisodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Episode
        fields = (
            "id",
            "guid",
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
            "source",
        )


class EpisodeListItemSerializer(serializers.ModelSerializer):
    """Compact episode representation for lists (detail view & episodes feed)."""

    class Meta:
        model = Episode
        fields = (
            "id",
            "guid",
            "title",
            "published_at",
            "duration_seconds",
            "episode_number",
            "season_number",
            "episode_type",
            "explicit",
            "audio_url",
            "image_url",
        )


class PodcastListSerializer(serializers.ModelSerializer):
    categories = serializers.SlugRelatedField(slug_field="slug", many=True, read_only=True)

    class Meta:
        model = Podcast
        fields = (
            "id",
            "title",
            "slug",
            "publisher",
            "author",
            "image_url",
            "language",
            "country",
            "explicit",
            "categories",
            "rating_average",
            "rating_count",
            "publish_frequency",
            "total_episodes",
            "last_published_at",
            "enrichment_status",
            "updated_at",
        )


class PodcastDetailSerializer(serializers.ModelSerializer):
    categories = CategorySerializer(many=True, read_only=True)
    external_ids = serializers.DictField(read_only=True)
    recent_episodes = serializers.SerializerMethodField()
    episodes_url = serializers.SerializerMethodField()

    class Meta:
        model = Podcast
        fields = (
            "id",
            "title",
            "slug",
            "description",
            "publisher",
            "author",
            "image_url",
            "website_url",
            "language",
            "country",
            "explicit",
            "itunes_type",
            "categories",
            "rating_average",
            "rating_count",
            "publish_frequency",
            "total_episodes",
            "last_published_at",
            "enrichment_status",
            "enrichment_sources",
            "enriched_at",
            "episodes_synced_at",
            "external_ids",
            "created_at",
            "updated_at",
            "episodes_url",
            "recent_episodes",
        )

    def get_episodes_url(self, obj) -> str:
        request = self.context.get("request")
        return reverse("api:podcast-episodes", kwargs={"pk": obj.pk}, request=request)

    def get_recent_episodes(self, obj):
        # a small, ready-to-render first page; full history via ``episodes_url``
        limit = int(self.context.get("recent_episode_limit", 10))
        episodes = getattr(obj, "prefetched_recent_episodes", None)
        if episodes is None:
            episodes = list(obj.episodes.latest_first()[:limit])
        return EpisodeListItemSerializer(episodes[:limit], many=True, context=self.context).data


class _ChartEntityMixin(serializers.Serializer):
    def _podcast_summary(self, podcast):
        if not podcast:
            return None
        return {
            "id": podcast.id,
            "title": podcast.title,
            "publisher": podcast.publisher,
            "image_url": podcast.image_url,
            "apple_id": podcast.apple_id,
            "spotify_id": podcast.spotify_id,
            "enrichment_status": podcast.enrichment_status,
        }

    def _episode_summary(self, episode):
        if not episode:
            return None
        return {
            "id": episode.id,
            "title": episode.title,
            "podcast_id": episode.podcast_id,
            "published_at": episode.published_at,
            "audio_url": episode.audio_url,
            "duration_seconds": episode.duration_seconds,
        }


class ChartEntrySerializer(_ChartEntityMixin, serializers.Serializer):
    rank = serializers.IntegerField()
    previous_rank = serializers.IntegerField(allow_null=True)
    rank_change = serializers.IntegerField(allow_null=True)
    is_new = serializers.SerializerMethodField()
    title = serializers.CharField()
    publisher = serializers.CharField()
    image_url = serializers.CharField()
    external_url = serializers.CharField()
    external_id = serializers.CharField()
    podcast = serializers.SerializerMethodField()
    episode = serializers.SerializerMethodField()

    def get_is_new(self, obj) -> bool:
        return obj.previous_rank is None

    def get_podcast(self, obj):
        return self._podcast_summary(obj.podcast)

    def get_episode(self, obj):
        return self._episode_summary(obj.episode)


# --- request/param serializer (drf-yasg documentation only) ---------------
class ChartQuerySerializer(serializers.Serializer):
    country = serializers.CharField(required=False, help_text="ISO-3166 alpha-2, e.g. 'us' (default from settings)")
    category = serializers.CharField(required=False, help_text="category slug, e.g. 'top-podcasts', 'comedy'")
    date = serializers.DateField(required=False, help_text="chart date (YYYY-MM-DD); default = latest available")
    source = serializers.ChoiceField(choices=["spotify", "podchaser"], required=False)
    type = serializers.ChoiceField(choices=["podcasts", "episodes"], required=False, help_text="chart entity type")
    limit = serializers.IntegerField(required=False, min_value=1, max_value=200)
    offset = serializers.IntegerField(required=False, min_value=0)
