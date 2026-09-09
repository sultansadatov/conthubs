from django.contrib import admin
from django.utils.html import format_html

from .models import Category, Episode, Podcast


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "source", "slug", "parent")
    list_filter = ("source",)
    search_fields = ("name", "slug", "external_id")
    autocomplete_fields = ("parent",)


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0
    fields = ("title", "published_at", "duration_seconds", "episode_number", "guid")
    readonly_fields = fields
    ordering = ("-published_at",)
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).only(
            "title", "published_at", "duration_seconds", "episode_number", "guid", "podcast"
        )[:50]


@admin.register(Podcast)
class PodcastAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "publisher",
        "enrichment_status",
        "total_episodes",
        "last_published_at",
        "publish_frequency",
        "rating_average",
        "is_active",
    )
    list_filter = ("enrichment_status", "is_active", "publish_frequency", "language", "first_seen_source")
    search_fields = (
        "title",
        "publisher",
        "author",
        "rss_url",
        "apple_id",
        "podcastindex_id",
        "podchaser_id",
        "spotify_id",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "enriched_at",
        "episodes_synced_at",
        "match_key",
        "cover_preview",
        "raw_metadata",
    )
    filter_horizontal = ("categories",)
    inlines = (EpisodeInline,)
    list_select_related = True
    actions = ("action_enqueue_enrichment", "action_enqueue_episode_sync")
    fieldsets = (
        (None, {"fields": ("title", "slug", "publisher", "author", "description")}),
        ("Media", {"fields": ("cover_preview", "image_url", "website_url", "language", "country", "explicit", "itunes_type")}),
        ("Identifiers", {"fields": ("rss_url", "apple_id", "podcastindex_id", "podcastindex_guid", "podchaser_id", "spotify_id", "match_key")}),
        ("Ratings & cadence", {"fields": ("rating_average", "rating_count", "publish_frequency")}),
        ("Aggregates", {"fields": ("total_episodes", "last_published_at")}),
        ("Enrichment", {"fields": ("enrichment_status", "enrichment_sources", "enriched_at", "raw_metadata")}),
        ("Sync", {"fields": ("categories", "is_active", "episodes_synced_at", "feed_etag", "feed_last_modified", "created_at", "updated_at")}),
    )

    @admin.display(description="cover")
    def cover_preview(self, obj):
        if obj.image_url:
            return format_html('<img src="{}" style="height:90px;border-radius:8px" />', obj.image_url)
        return "—"

    @admin.action(description="Queue metadata enrichment")
    def action_enqueue_enrichment(self, request, queryset):
        from ingestion.tasks import enrich_podcast

        for pk in queryset.values_list("id", flat=True):
            enrich_podcast.delay(pk)
        self.message_user(request, f"Queued enrichment for {queryset.count()} podcast(s).")

    @admin.action(description="Queue episode sync")
    def action_enqueue_episode_sync(self, request, queryset):
        from ingestion.tasks import sync_podcast_episodes

        for pk in queryset.values_list("id", flat=True):
            sync_podcast_episodes.delay(pk)
        self.message_user(request, f"Queued episode sync for {queryset.count()} podcast(s).")


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ("title", "podcast", "published_at", "duration_seconds", "episode_number", "season_number")
    list_filter = ("explicit", "episode_type", "source", "published_at")
    search_fields = ("title", "guid", "podcast__title")
    autocomplete_fields = ("podcast",)
    date_hierarchy = "published_at"
    readonly_fields = ("created_at", "updated_at", "external_ids", "raw_metadata")
    list_select_related = ("podcast",)
