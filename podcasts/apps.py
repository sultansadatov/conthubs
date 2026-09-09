from django.apps import AppConfig


class PodcastsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "podcasts"
    verbose_name = "Podcasts & Episodes"

    def ready(self):
        from . import signals  # noqa: F401
