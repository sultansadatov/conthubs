"""URL routing for the v1 API (mounted at ``/api/v1/`` in myproject/urls.py)."""
from __future__ import annotations

from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    # --- TT §3.1  Chart API ------------------------------------------------
    path("charts", views.ChartView.as_view(), name="charts"),

    # --- TT §3.2  Podcast APIs ------------------------------------------
    path("podcasts", views.PodcastListView.as_view(), name="podcast-list"),
    path("podcasts/<str:pk>", views.PodcastDetailView.as_view(), name="podcast-detail"),
    path("podcasts/<str:pk>/episodes", views.PodcastEpisodesView.as_view(), name="podcast-episodes"),

    # --- supporting endpoints -------------------------------------------
    # category list backs the "?category=" filter of the podcast list (TT §3.2);
    # health is an operational liveness/freshness probe.
    path("categories", views.CategoryListView.as_view(), name="category-list"),
    path("health", views.HealthView.as_view(), name="health"),
]
