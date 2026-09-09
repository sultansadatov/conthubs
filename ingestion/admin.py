from django.contrib import admin

from .models import ScrapeRun


@admin.register(ScrapeRun)
class ScrapeRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "kind",
        "source",
        "status",
        "rows_written",
        "slices_ok",
        "slices_failed",
        "duration_seconds",
    )
    list_filter = ("kind", "source", "status", "started_at")
    readonly_fields = [f.name for f in ScrapeRun._meta.fields] + ["duration_seconds"]
    date_hierarchy = "started_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
