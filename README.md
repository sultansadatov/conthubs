# Podcast Aggregation & API Platform

Back-end that **scrapes international podcast charts** (Spotify & Podchaser),
**enriches** every podcast with metadata from third-party APIs (Apple Podcasts,
PodcastIndex, Podchaser), stores the **full episode history**, keeps a
**day-by-day chart history**, and serves everything through a versioned REST API.

Django 5.2 project made of four apps — **`podcasts`**, **`charts`**,
**`ingestion`**, **`api`**.

> **No authentication.** The TT describes a public, read-only data service
> ("Toplanmış məlumatlar ... istifadəçilərə ... təqdim olunacaq") with no user
> or auth requirement, so the API is open (rate-limited) and there is no auth
> app / JWT. The Django admin uses the stock `auth.User` model for operational
> use only.

> Bu layihə `cont.-task.pdf` texniki tapşırığının tam icrasıdır — aşağıdakı
> *"Requirement traceability"* cədvəli hər bəndin harada həyata keçirildiyini göstərir.

---

## 1. Architecture at a glance

```
                    ┌─────────────── Celery beat (schedule in myproject/celery.py) ───────────────┐
                    │                                                                             │
        06:00 ▼                 06:30 ▼            */20 ▼             07:00 ▼           1st-of-month ▼
 scrape_spotify_charts   scrape_podchaser_charts  enrich_pending   sync_all_episodes  ensure_chart_partitions
        │                        │                     │                   │
        ▼                        ▼                     ▼                   ▼
 ingestion.scrapers      ingestion.scrapers    ingestion.enrichment  ingestion.episodes
  (Spotify JSON API)     (Podchaser GraphQL     Apple / PodcastIndex   RSS (feedparser) /
        │                 + HTML fallback)      / Podchaser  merge     PodcastIndex API
        ▼                        ▼                     │                   │
        └──────────► ingestion.pipeline ◄─────────────┘                   │
                     • bulk-resolve shows  ── 1 batched SELECT per slice, resolve_podcast() only for new ones
                     •   de-dupe by rss/apple/pi/pc/spotify id, then normalised title|publisher
                     • upsert_chart_entries()  ── ON CONFLICT on the 6-col composite PK
                     • backfill_rank_movement()  ── previous_rank / rank_change vs. yesterday
                     • upsert_episodes()  ── ON CONFLICT (podcast, guid)
                                   │
                                   ▼
                    PostgreSQL ──  podcasts_podcast, podcasts_episode
                               │   charts_chartentry  ← RANGE-partitioned by month on `date`
                               │       charts_chartentry_2024_01 … _2026_12 … _default
                               ▼
                    api  (DRF, /api/v1/)  +  Redis response cache
                     • GET /charts            • GET /podcasts
                     • GET /podcasts/{id}     • GET /podcasts/{id}/episodes  (cursor / offset)
```

### Apps

| App | Responsibility |
|-----|----------------|
| **`podcasts`** | Domain models `Category`, `Podcast`, `Episode`; write-services: `resolve_podcast` (de-dup), `upsert_episodes` (bulk UPSERT), `recompute_podcast_aggregates`, `infer_publish_frequency`. |
| **`charts`** | `ChartEntry` (composite PK, month-range-partitioned). Partition management (`partitions.py`, `manage_partitions` command, `ensure_chart_partitions` task). UPSERT + rank-movement + "latest date" services. |
| **`ingestion`** | HTTP client (retry / backoff / rate-limit / robots), Spotify & Podchaser scrapers, enrichment orchestrator, RSS/PodcastIndex episode sync, all Celery tasks, `ScrapeRun` audit log, management commands. |
| **`api`** | DRF serializers, filters, pagination (page-number / cursor / limit-offset), views & URLs for the 3 required APIs + discovery/health endpoints, uniform error envelope. |

---

## 2. Requirement traceability (`cont.-task.pdf`)

| TT § | Requirement | Where it lives |
|------|-------------|----------------|
| **2.1** | Daily scrape of Spotify Podcast Charts | `ingestion/clients/spotify.py` → real endpoint `GET podcastcharts.byspotify.com/api/charts/<category>?region=<cc>&limit=100`; task `ingestion.tasks.scrape_spotify_charts` (Celery beat 06:00). |
| **2.1** | Daily scrape of Podchaser Charts | `ingestion/clients/podchaser.py` (GraphQL API, key required) + `ingestion/scrapers/podchaser.py` HTML fallback; task `scrape_podchaser_charts` (06:30). |
| **2.1** | Grouped by **Country** and **Category** | Both scrapers iterate `settings.SPOTIFY_CHART_REGIONS × SPOTIFY_CHART_CATEGORIES` / `PODCHASER_CHART_COUNTRIES × PODCHASER_CHART_CATEGORIES`; stored on every `ChartEntry` row (`country`, `category`). |
| **2.1** | Periodic (once/day), keep charts **fresh** | `crontab(hour=6)` beat entries; charting podcasts' feeds also refreshed every 6 h (`sync_episodes_for_recent_charts`). |
| **2.2** | Enrichment via Apple Podcasts / PodcastIndex / Podchaser (one **or more**) | `ingestion/enrichment.py` orchestrator + `ingestion/clients/{apple,podcastindex,podchaser}.py`. Apple/iTunes needs no key (always on); the others are opt-in. Providers that fail or lack keys are skipped; `enrichment_sources` records who contributed. |
| **2.2** | Store name, description, publisher/author, cover image URL, categories, ratings, publish frequency | `podcasts.Podcast` fields: `title, description, publisher, author, image_url, categories (M2M), rating_average, rating_count, publish_frequency`. Raw provider payloads kept in `raw_metadata`. |
| **2.3** | Store podcast metadata **and all episodes** | `podcasts.Episode` (FK → `Podcast`), populated by `ingestion/episodes.py`. |
| **2.3** | Periodically fetch **new** episodes (RSS **or** API) | `sync_podcast_episodes` — feed body fetched through the shared `HttpClient` (timeout + retries + conditional GET via `If-None-Match` / `If-Modified-Since`), then parsed with `feedparser`; falls back to the PodcastIndex episodes API. Beat: daily full pass + 6-hourly pass for charting shows. |
| **3.1** | `GET /api/v1/charts` — `country, category, date (default latest), source` | `api/views.py::ChartView`, route `api/urls.py`. Also supports `type` (podcasts/episodes), `limit`, `offset`. |
| **3.1** | Response = rank-ordered list of podcasts / top episodes | `ChartEntrySerializer` — `rank, previous_rank, rank_change, is_new`, denormalised snapshot + resolved `podcast`/`episode` summary. Ordered by `rank`. |
| **3.2** | `GET /api/v1/podcasts` — paginated, **search**, **category filter** | `PodcastListView` + `api/filters.py::PodcastFilter` (`search`, `category` slug/id, `language`, `publish_frequency`, `min_rating`, `has_episodes`, `ordering`). Page-number pagination. |
| **3.2** | `GET /api/v1/podcasts/{id}` — full metadata + latest episodes, **cursor / offset pagination** | `PodcastDetailView` returns metadata + 10 recent episodes + `episodes_url`. `PodcastEpisodesView` (`/podcasts/{id}/episodes`) → **cursor pagination by default**, `?paginate=offset` for limit/offset. |
| **3 – scalability** | **Table partitioning** of daily chart records | `charts_chartentry` is `PARTITION BY RANGE (date)` with monthly partitions + a `DEFAULT` catch-all. Created in `charts/migrations/0001_initial.py`; rolled forward monthly by `charts.tasks.ensure_chart_partitions` / `manage.py manage_partitions`. |
| **3 – scalability** | **Indexing** of `country`, `category`, `date` | `chartentry_sccd_idx (source, country, category, date)` — the mandated composite index — plus `chartentry_lookup_idx`, `chartentry_date_idx`, FK+date indexes. Podcast search uses `pg_trgm` GIN indexes (`podcasts/migrations/0002_search_trgm.py`). Episodes: `(podcast, -published_at)`. |
| **3 – scalability** | **UPSERT** on episodes by GUID/URL, no duplicates | `podcasts/services.py::upsert_episodes` → `bulk_create(update_conflicts=True, unique_fields=["podcast", "guid"])`; `episode_guid()` guarantees a stable key (feed `<guid>` → audio URL → external id → deterministic hash). Charts UPSERT on the 6-col composite PK. Podcast de-dup in `resolve_podcast`. |
| **5 – acceptance** | Old data not deleted, history preserved | Nothing in the pipeline hard-deletes. Each day appends a new `date` slice to `charts_chartentry`; dead feeds are hidden with `is_active=False`, never removed. |
| **5 – acceptance** | Low latency + pagination on the 3 APIs | Redis response cache on `/charts` (10 min) + cached "latest date" resolver; a chart page is one bounded index range-scan (≤200 rows); `.only()` / `select_related` / `prefetch_related` on list & detail; trigram-indexed search; every list endpoint paginated; `/health` is O(1) (no `COUNT` on the partitioned table). |
| **5 – acceptance** | DB architecture avoiding duplicates & needless load | Composite-PK UPSERT (charts) + `(podcast, guid)` UPSERT (episodes); podcasts de-duped once per scrape **slice** via a single batched `SELECT` (a re-scrape of a 200-row chart is ~7 queries, not ~400); no per-row raw JSON; conditional GET so unchanged feeds cost one `304`. |
| **4 – tech** | Python, Celery, PostgreSQL | Django 5.2 / Celery 5.5 / PostgreSQL 17. Scraping: `requests` + `beautifulsoup4` + `lxml` + `feedparser`; retries via `tenacity`; cache via `django-redis`. |

---

## 3. Data model

### `podcasts.Podcast`
De-dup identifiers (each a **partial unique** constraint): `rss_url`, `apple_id`,
`podcastindex_id`, `podcastindex_guid`, `podchaser_id`, `spotify_id`; plus a
normalised `match_key = "<slug title>|<slug publisher>"` fallback.
Metadata: `title, description, publisher, author, image_url, website_url,
language, country, explicit, categories (M2M), rating_average, rating_count,
publish_frequency`. Bookkeeping: `enrichment_status`, `enrichment_sources`,
`raw_metadata` (per-provider payloads), `episodes_synced_at`, `feed_etag`,
`total_episodes`, `last_published_at`.

### `podcasts.Episode`
`UNIQUE (podcast, guid)` — the UPSERT key. `title, description, published_at,
duration_seconds, episode_number, season_number, episode_type, explicit,
audio_url, audio_type, audio_length_bytes, image_url, external_ids`.
Index `(podcast, -published_at)` for the "newest episodes of this podcast" query.

### `charts.ChartEntry`  (partitioned)
Natural composite PK **`(date, source, country, category, chart_type, rank)`** —
it is the uniqueness rule, it contains the partition key (a Postgres
requirement) and it is the `ON CONFLICT` target for the daily UPSERT.
`previous_rank`, `rank_change`, nullable FKs `podcast` / `episode`, and a
denormalised snapshot (`title, publisher, image_url, external_id`) so the Chart
API renders even before enrichment resolves the entity. Deliberately **no raw
JSON blob per row** — this table grows by thousands of rows/day forever, so a
near-duplicate payload per show per day is exactly the "lazımsız yük" the spec
warns against; the rich provider data lives once on `Podcast.raw_metadata`.

### `ingestion.ScrapeRun`
One row per scrape/enrichment/episode batch — `source, kind, status,
started_at, finished_at, rows_written, slices_ok/failed, stats, error` — so
"the system collects data daily" is auditable in the admin.

---

## 4. Running it

### 4.1 Docker (recommended)

```bash
cd bin/dev
cp ../../.env.example .env          # then edit: DB/Redis creds + optional API keys
docker compose up --build           # web :8000, postgres :5433, redis :6379, celery, celery-beat
```

`web` runs migrations on start (creating the partitioned chart table + the first
~3 years of monthly partitions). `celery-beat` then drives the daily schedule.
Create an admin login with `docker compose exec web python manage.py createsuperuser`.

### 4.2 Local (no Docker)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# point at a Postgres + Redis (or leave POSTGRES_DB/REDIS_HOST unset to use the
# SQLite + local-memory fallback — fine for a read-through, not for partitioning)
export POSTGRES_DB=core_db POSTGRES_USER=core_user POSTGRES_PASSWORD=... \
       POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433 \
       REDIS_HOST=127.0.0.1 REDIS_PORT=6379 REDIS_PASSWORD=...
python manage.py migrate
python manage.py createsuperuser        # optional — admin is for ops only
python manage.py runserver
# worker + beat in separate shells:
celery -A myproject.celery:app worker -Q default,scraping,enrichment,episodes,maintenance -l INFO
celery -A myproject.celery:app beat  --scheduler django_celery_beat.schedulers:DatabaseScheduler -l INFO
```

### 4.3 Seed data to explore the API

```bash
# Option A: one real scrape (no API keys needed) — ~200 US podcasts + a chart
python manage.py scrape_charts --source spotify --regions us --categories top-podcasts
python manage.py enrich_podcasts --limit 50
python manage.py sync_episodes  --limit 50

# Option B: curated, self-contained demo (12 well-known shows + 3 days of chart history)
python manage.py seed_demo
```

### 4.4 Management commands

| Command | Purpose |
|---------|---------|
| `scrape_charts [--source spotify\|podchaser\|all] [--regions] [--countries] [--categories] [--enqueue-followups]` | Run a chart scrape now (what the beat task does). |
| `enrich_podcasts [--limit N] [--ids 1,2] [--all] [--force] [--providers apple,podcastindex]` | Metadata enrichment. |
| `sync_episodes [--limit N] [--ids 1,2] [--all] [--force]` | Fetch new episodes from RSS / PodcastIndex. |
| `manage_partitions [--list] [--months-ahead N]` | Create/inspect monthly chart partitions. |
| `charts_summary [--days N]` | Row counts, date coverage, per-slice freshness. |
| `seed_demo [--no-episodes] [--days N]` | Curated demo dataset. |

---

## 5. API reference (`/api/v1`)

Interactive docs: **`/swagger/`** and **`/redoc/`**.

### `GET /api/v1/charts`  (TT §3.1)
| param | default | notes |
|-------|---------|-------|
| `source` | `spotify` | `spotify` \| `podchaser` |
| `country` | `us` | ISO-3166 alpha-2 |
| `category` | `top-podcasts` | slug, e.g. `top-podcasts`, `top-episodes`, `comedy` |
| `date` | latest available | `YYYY-MM-DD` — chart history is queryable per day |
| `type` | `podcasts` | `podcasts` \| `episodes` |
| `limit` / `offset` | `200` / `0` | |

```jsonc
{
  "source": "spotify", "country": "us", "category": "top-podcasts",
  "chart_type": "podcasts", "date": "2026-09-08", "is_latest": true, "count": 200,
  "results": [
    { "rank": 1, "previous_rank": 2, "rank_change": 1, "is_new": false,
      "title": "The Joe Rogan Experience", "publisher": "...", "image_url": "...",
      "external_id": "spotify:show:4rOoJ6...", "external_url": "https://open.spotify.com/show/...",
      "podcast": { "id": 4, "title": "...", "apple_id": "360084272", "enrichment_status": "done" },
      "episode": null }
  ]
}
```

### `GET /api/v1/podcasts`  (TT §3.2)
`?search=` (title / publisher / author — the trigram-indexed columns) ·
`?category=<slug|id>` ·
`?language=` · `?publish_frequency=` · `?min_rating=` · `?has_episodes=` ·
`?ordering=title|-rating_average|-total_episodes|-last_published_at` ·
`?page=` · `?page_size=` (≤100).
Envelope: `{count, num_pages, page, page_size, next, previous, results:[…]}`.

### `GET /api/v1/podcasts/{id}`  (TT §3.2)
Full metadata + `categories` + `external_ids` + 10 `recent_episodes` +
`episodes_url`. `?id_type=apple|podcastindex|podchaser|spotify` looks the podcast
up by an external id instead of the internal one.

### `GET /api/v1/podcasts/{id}/episodes`  (TT §3.2)
Newest first. **Cursor pagination by default** (`?cursor=`); `?paginate=offset`
for `limit`/`offset`; `?detail=1` for the full episode payload.

### Supporting
* `GET /api/v1/categories?source=` — the category taxonomy that backs the
  `?category=` filter of the podcast list.
* `GET /api/v1/health` — liveness + data-freshness probe (503 if chart data is
  more than 3 days stale).

---

## 6. Scraping notes

* **Spotify** — the chart site's own JSON endpoint
  (`/api/charts/<category>?region=<cc>&limit=100`) returns a rank-ordered array;
  rank is the array position. No key required. If it ever changes shape the
  client falls back to any chart JSON embedded in the page HTML.
* **Podchaser** — the public `/charts` page is fully client-rendered, so charts
  come from the **official GraphQL API** (`api.podchaser.com/graphql`, OAuth2
  client-credentials — free key). The GraphQL query is in
  `settings.PODCHASER_CHART_QUERY` so it can be tuned to their current schema
  without a code change. Without a key, Podchaser is skipped and only Spotify
  charts load.
* All outbound HTTP goes through `ingestion/clients/base.py::HttpClient`:
  descriptive User-Agent, `robots.txt` enforcement for scraping targets,
  per-host rate limiting, and exponential-backoff retries on 429 / 5xx.

---

## 7. Tests

```bash
python manage.py test podcasts charts ingestion api
```

~75 tests, run against real PostgreSQL: podcast de-dup & episode UPSERT
idempotency, chart UPSERT + partition routing + rank-movement, Spotify/Podchaser
scraper parsing (fixtures for JSON API, GraphQL, `__NEXT_DATA__` and DOM
fallbacks), enrichment merge rules + provider-crash isolation, RSS parsing
(duration formats, conditional GET), HTTP client retry/robots/rate-limit, and
every API endpoint (pagination, filters, cursor/offset, error envelope, cache).
