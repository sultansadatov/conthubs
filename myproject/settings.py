"""
Django settings for the Podcast Aggregation & API Platform.

The platform is a public, read-only data service (TT: "Toplanmış məlumatlar
xüsusi API-lər vasitəsilə istifadəçilərə ... təqdim olunacaq") — there is no
user-authentication requirement, so no auth app / JWT is configured. The Django
admin uses the stock ``auth.User`` model purely for operational visibility.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-*$2cyj8x&e+tjbp0ukm+su#hs86e6-jv@s*ack)khwxf@+=!3y",
)
DEBUG = os.environ.get("DEBUG", "True") != "False"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


def _env_list(name, default=""):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
#  Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "jazzmin",  # admin-panel theme — must precede django.contrib.admin

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",

    # third party
    "corsheaders",
    "django_filters",
    "rest_framework",
    "drf_yasg",
    "django_celery_results",
    "django_celery_beat",

    # podcast aggregation platform
    "podcasts",
    "charts",
    "ingestion",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Django Debug Toolbar — dev only
INTERNAL_IPS = ["127.0.0.1", "localhost"]
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: True}

ROOT_URLCONF = "myproject.urls"
WSGI_APPLICATION = "myproject.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
#  Database (TT §4: PostgreSQL)
# ---------------------------------------------------------------------------
# PostgreSQL is required in production — table partitioning, JSONB, GIN/trigram
# search and native UPSERT all depend on it. When POSTGRES_DB is unset we fall
# back to a local SQLite file so the project can be inspected / unit-tested
# without a running Postgres.
if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB"),
            "USER": os.environ.get("POSTGRES_USER"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
            "HOST": os.environ.get("POSTGRES_HOST"),
            "PORT": os.environ.get("POSTGRES_PORT"),
            "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"application_name": "podcast-platform"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
#  Static / media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "static")
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# ---------------------------------------------------------------------------
#  i18n / tz
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Baku"
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------------------
#  CORS — the API is consumed by a separate front-end / mobile app (TT §1)
# ---------------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = _env_bool("CORS_ALLOW_ALL_ORIGINS", True)
CORS_ALLOWED_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS")
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------------------
#  Cache (TT §4: "keşləmə vasitələri ... namizədin seçiminə uyğun") — Redis
# ---------------------------------------------------------------------------
if os.getenv("REDIS_HOST"):
    _redis_auth = f":{os.getenv('REDIS_PASSWORD')}@" if os.getenv("REDIS_PASSWORD") else ""
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": f"redis://{_redis_auth}{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT', '6379')}/1",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,  # a cache outage must never take the API down
            },
            "KEY_PREFIX": "podcast",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "podcast-locmem",
        }
    }
DJANGO_REDIS_IGNORE_EXCEPTIONS = True
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True

# ---------------------------------------------------------------------------
#  Django REST Framework — public, read-only API (TT §3)
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # No end-user auth is required by the spec; SessionAuthentication is kept
    # only so the browsable API works for a logged-in admin.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.DefaultPageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("THROTTLE_ANON", "120/min"),
        "user": os.environ.get("THROTTLE_USER", "600/min"),
    },
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
    "COERCE_DECIMAL_TO_STRING": False,
}

SWAGGER_SETTINGS = {
    "USE_SESSION_AUTH": False,
    "SECURITY_DEFINITIONS": {},
    "SUPPORTED_SUBMIT_METHODS": ["get"],
}

# ---------------------------------------------------------------------------
#  Admin theme (jazzmin) — operational UI only
# ---------------------------------------------------------------------------
JAZZMIN_SETTINGS = {
    "site_title": "Podcast Platform",
    "site_header": "Podcast Platform",
    "site_brand": "Podcast Platform",
    "welcome_sign": "Podcast Aggregation & API Platform — Ops",
    "copyright": "",
    "hide_recent_actions": False,
    "order_with_respect_to": ["ingestion", "charts", "podcasts", "auth"],
    "icons": {
        "podcasts.Podcast": "fas fa-podcast",
        "podcasts.Episode": "fas fa-headphones",
        "podcasts.Category": "fas fa-tags",
        "ingestion.ScrapeRun": "fas fa-cloud-download-alt",
        "django_celery_beat.PeriodicTask": "fas fa-clock",
        "django_celery_results.TaskResult": "fas fa-tasks",
    },
    "topmenu_links": [
        {"name": "API docs", "url": "/swagger/", "new_window": True},
    ],
}
JAZZMIN_UI_TWEAKS = {"theme": "flatly"}

# Email — nothing in the platform sends mail; console backend keeps `check` quiet.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# =============================================================================
#  PODCAST DATA AGGREGATION & API PLATFORM
#  (see README.md for how every requirement of the Technical Task maps here)
# =============================================================================

# ---------------------------------------------------------------------------
#  Celery — task queue / periodic scraping (TT §4: "Task Növbəsi -> Celery")
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL",
    f"redis://{(':' + os.getenv('REDIS_PASSWORD') + '@') if os.getenv('REDIS_PASSWORD') else ''}"
    f"{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0",
)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "django-db")
CELERY_CACHE_BACKEND = "default"
CELERY_RESULT_EXTENDED = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 200
CELERY_TASK_TIME_LIMIT = 60 * 20
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 18
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_DEFAULT_RETRY_DELAY = 60
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 60 * 60}
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# ---------------------------------------------------------------------------
#  Scraping sources  (TT §2.1) — kept in settings so ops can widen the daily
#  crawl (more countries / categories) without a code change.
# ---------------------------------------------------------------------------

# --- Spotify Podcast Charts (https://podcastcharts.byspotify.com) ----------
# The chart site (a Next.js SSG app) fetches its data from this JSON endpoint:
#     GET /api/charts/<category>?region=<cc>&limit=100
# Response: a rank-ordered JSON array of {showUri, showName, showPublisher,
# showImageUrl, showDescription, chartRankMove, [episodeUri, episodeName, ...]}.
SPOTIFY_CHART_BASE_URL = os.environ.get(
    "SPOTIFY_CHART_BASE_URL", "https://podcastcharts.byspotify.com"
)
SPOTIFY_CHART_API_URL = os.environ.get(
    "SPOTIFY_CHART_API_URL", "https://podcastcharts.byspotify.com/api/charts/{category}"
)
SPOTIFY_CHART_LIMIT = int(os.environ.get("SPOTIFY_CHART_LIMIT", "100"))
SPOTIFY_CHART_REGIONS = _env_list(
    "SPOTIFY_CHART_REGIONS",
    "us,gb,de,fr,es,it,br,au,ca,in,mx,se,ie,nl,jp",
)
# category slugs to crawl per region (the site's own slugs)
SPOTIFY_CHART_CATEGORIES = _env_list(
    "SPOTIFY_CHART_CATEGORIES",
    "top-podcasts,top-episodes,comedy,news,true-crime,business,society-culture,"
    "sports,education,technology,health-fitness,arts,tv-film",
)
# which of those slugs are *episode* charts (everything else is a podcast chart)
SPOTIFY_EPISODE_CATEGORIES = set(_env_list("SPOTIFY_EPISODE_CATEGORIES", "top-episodes"))

# --- Podchaser Charts -------------------------------------------------------
# Podchaser's /charts page is fully client-rendered, so charts are read from
# the official GraphQL API (https://api-docs.podchaser.com) using OAuth2
# client-credentials. Set PODCHASER_API_KEY / PODCHASER_API_SECRET (free signup)
# to enable Podchaser as a chart source; without them only Spotify charts load.
PODCHASER_CHARTS_URL = os.environ.get(
    "PODCHASER_CHARTS_URL", "https://www.podchaser.com/charts"
)
PODCHASER_API_URL = os.environ.get("PODCHASER_API_URL", "https://api.podchaser.com/graphql")
PODCHASER_API_KEY = os.environ.get("PODCHASER_API_KEY", "")
PODCHASER_API_SECRET = os.environ.get("PODCHASER_API_SECRET", "")
PODCHASER_CHART_LIMIT = int(os.environ.get("PODCHASER_CHART_LIMIT", "100"))
PODCHASER_CHART_COUNTRIES = _env_list("PODCHASER_CHART_COUNTRIES", "us,gb,de,ca,au")
# Podchaser chart categories (their slugs); "" / "all" = the overall Top chart.
PODCHASER_CHART_CATEGORIES = _env_list(
    "PODCHASER_CHART_CATEGORIES",
    "all,comedy,news,true-crime,business,society-culture,technology,sports",
)
# GraphQL query used to pull a chart. Overridable via env so it can be adjusted
# to Podchaser's current schema without a code change. Variables: $first (int),
# $category (string slug or null), $country (ISO-2 upper or null).
PODCHASER_CHART_QUERY = os.environ.get("PODCHASER_CHART_QUERY", "").strip() or """
query ChartTop($first: Int!, $category: String, $country: String) {
  charts(
    first: $first
    filters: { type: PODCHASER, categorySlug: $category, countryCode: $country }
  ) {
    data {
      rank
      podcast {
        id
        title
        description
        webUrl
        rssUrl
        imageUrl
        applePodcastsId
        ratingAverage
        author { name }
      }
    }
  }
}
""".strip()

# ---------------------------------------------------------------------------
#  Enrichment providers  (TT §2.2) — every provider is optional; the pipeline
#  degrades gracefully and records which sources actually contributed.
# ---------------------------------------------------------------------------
APPLE_PODCASTS_LOOKUP_URL = os.environ.get(
    "APPLE_PODCASTS_LOOKUP_URL", "https://itunes.apple.com/lookup"
)
APPLE_PODCASTS_SEARCH_URL = os.environ.get(
    "APPLE_PODCASTS_SEARCH_URL", "https://itunes.apple.com/search"
)
APPLE_PODCASTS_STOREFRONT = os.environ.get("APPLE_PODCASTS_STOREFRONT", "us")

PODCASTINDEX_API_URL = os.environ.get(
    "PODCASTINDEX_API_URL", "https://api.podcastindex.org/api/1.0"
)
PODCASTINDEX_API_KEY = os.environ.get("PODCASTINDEX_API_KEY", "")
PODCASTINDEX_API_SECRET = os.environ.get("PODCASTINDEX_API_SECRET", "")

# Ordered list of providers the enrichment orchestrator will try.
ENRICHMENT_PROVIDER_ORDER = _env_list(
    "ENRICHMENT_PROVIDER_ORDER", "apple,podcastindex,podchaser"
)

# ---------------------------------------------------------------------------
#  HTTP client behaviour for all outbound scraping / API calls
# ---------------------------------------------------------------------------
HTTP_CLIENT = {
    "USER_AGENT": os.environ.get(
        "SCRAPER_USER_AGENT",
        "PodcastAggregatorBot/1.0 (+https://example.com/bot; contact=ops@example.com)",
    ),
    "TIMEOUT": float(os.environ.get("SCRAPER_TIMEOUT", "20")),
    "MAX_RETRIES": int(os.environ.get("SCRAPER_MAX_RETRIES", "4")),
    "BACKOFF_FACTOR": float(os.environ.get("SCRAPER_BACKOFF_FACTOR", "1.5")),
    "RATE_LIMIT_PER_HOST": float(os.environ.get("SCRAPER_RATE_LIMIT_PER_HOST", "3")),  # req/sec
    "RESPECT_ROBOTS": _env_bool("SCRAPER_RESPECT_ROBOTS", True),
}

# ---------------------------------------------------------------------------
#  Ingestion pipeline tuning
# ---------------------------------------------------------------------------
INGESTION = {
    # episodes are re-checked at most this often (hours) unless forced
    "EPISODE_REFRESH_MIN_INTERVAL_HOURS": int(
        os.environ.get("EPISODE_REFRESH_MIN_INTERVAL_HOURS", "12")
    ),
    # how many episodes the RSS/API sync keeps per podcast on first import (0 = all)
    "EPISODE_INITIAL_IMPORT_LIMIT": int(os.environ.get("EPISODE_INITIAL_IMPORT_LIMIT", "0")),
    # re-enrich a podcast if its metadata is older than this many days
    "ENRICHMENT_TTL_DAYS": int(os.environ.get("ENRICHMENT_TTL_DAYS", "30")),
    # fan-out batch sizes for the daily beat tasks
    "ENRICH_BATCH_SIZE": int(os.environ.get("ENRICH_BATCH_SIZE", "200")),
    "EPISODE_SYNC_BATCH_SIZE": int(os.environ.get("EPISODE_SYNC_BATCH_SIZE", "300")),
}

# ---------------------------------------------------------------------------
#  Chart / API behaviour
# ---------------------------------------------------------------------------
CHARTS = {
    "DEFAULT_SOURCE": "spotify",
    "DEFAULT_COUNTRY": os.environ.get("CHART_DEFAULT_COUNTRY", "us"),
    "DEFAULT_CATEGORY": os.environ.get("CHART_DEFAULT_CATEGORY", "top-podcasts"),
    "DEFAULT_TYPE": "podcasts",
    "MAX_ROWS": int(os.environ.get("CHART_MAX_ROWS", "200")),
    # cache TTLs (seconds)
    "LATEST_DATE_CACHE_TTL": int(os.environ.get("CHART_LATEST_DATE_CACHE_TTL", "300")),
    "RESPONSE_CACHE_TTL": int(os.environ.get("CHART_RESPONSE_CACHE_TTL", "600")),
    # months of chart partitions to keep pre-created ahead of "now"
    "PARTITION_AHEAD_MONTHS": int(os.environ.get("CHART_PARTITION_AHEAD_MONTHS", "3")),
    "PARTITION_START": os.environ.get("CHART_PARTITION_START", "2024-01-01"),
}

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}:{lineno} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "ingestion": {"level": os.environ.get("INGESTION_LOG_LEVEL", "INFO"), "handlers": ["console"], "propagate": False},
        "charts": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "api": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}

# When running the test suite, use an in-memory broker and eager tasks.
import sys as _sys

if "test" in _sys.argv:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["root"]["level"] = "CRITICAL"
    # the debug toolbar refuses to run under tests
    INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "debug_toolbar"]
    MIDDLEWARE = [m for m in MIDDLEWARE if "debug_toolbar" not in m]
