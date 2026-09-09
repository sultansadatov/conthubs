"""Root URL configuration.

The platform exposes one public API under ``/api/v1/`` (see ``api/urls.py``),
plus Swagger / ReDoc docs and the Django admin (operational use only).
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

schema_view = get_schema_view(
    openapi.Info(
        title="Podcast Aggregation & API Platform",
        default_version="v1",
        description=(
            "Aggregates podcast charts from Spotify & Podchaser, enriches them via "
            "Apple Podcasts / PodcastIndex / Podchaser, stores full episode history "
            "and serves it through the Chart / Podcast-List / Podcast-Detail APIs."
        ),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Podcast aggregation platform API (TT §3)
    path("api/v1/", include("api.urls")),

    # API documentation
    path("swagger/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
    path("redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
]

if settings.DEBUG:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "Podcast Platform — Admin"
admin.site.site_title = "Podcast Platform"
admin.site.index_title = "Operations"
