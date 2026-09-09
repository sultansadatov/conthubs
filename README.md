# Podcast Aggregation & API Platform

A Django/Celery/PostgreSQL back-end that:

1. **scrapes** international podcast charts every day (Spotify + Podchaser),
2. **enriches** every podcast with metadata from third-party APIs
   (Apple Podcasts, PodcastIndex, Podchaser),
3. stores the **full episode history** of every podcast,
4. keeps a **day-by-day chart history** that is never deleted, and
5. serves all of it through a versioned, cache-backed **REST API**.

Full implementation of `cont.-task.pdf`. Section **[§3. Requirement traceability](#3-requirement-traceability-cont-taskpdf)**
maps every line of the spec to the code that satisfies it.

> **Auth:** none. The spec describes a public, read-only data service, so the API
> is open (rate-limited). The Django admin uses the stock `auth.User` model for
> operations only.

> **Bu layihə lokalda `bin/dev/` qovluğundan Docker Compose ilə işə düşür** — aşağıdakı *Quick start*-a bax.

---

## 0. Quick start (local dev — Docker)

Everything runs from **`bin/dev/`**. `bin/dev/.env` is already committed with
working Postgres/Redis credentials for the local stack; open it to add optional
API keys (see [§5](#5-configuration-bindevenv)).

```bash
cd bin/dev

# build + start the whole stack (add `sudo` if your Docker needs it)
docker compose up --build -d

#   web         → http://localhost:8000       (Django dev server, auto-reloads on code changes)
#   postgres    → localhost:5433              (db: core_db)
#   redis       → localhost:6379
#   celery      → worker (queues: scraping / enrichment / episodes / maintenance)
#   celery-beat → scheduler (daily scrape @ 06:00 / 06:30 Asia/Baku)

# create an admin login
docker compose exec web python manage.py createsuperuser
```

The `web` container runs `migrate` on start — this creates the
**range-partitioned** chart table and ~3 years of monthly partitions.
The source tree is bind-mounted into the containers, so editing a `.py` file
reloads the dev server; only a dependency change needs `docker compose build`.

### Get real data in (no API keys needed)

```bash
cd bin/dev

# 1) scrape live Spotify charts  (--regions / --categories are optional; omit for the full configured set)
docker compose exec web python manage.py scrape_charts --source spotify --regions us,gb --categories top-podcasts,comedy

# 2) enrich with Apple Podcasts metadata (feed url, cover, publisher, genres)
docker compose exec web python manage.py enrich_podcasts --limit 150

# 3) pull episodes from each podcast's RSS feed
docker compose exec web python manage.py sync_episodes --limit 150

# check what landed
docker compose exec web python manage.py charts_summary
```

…then browse:

| URL | |
|-----|-----|
| <http://localhost:8000/swagger/> | interactive API docs |
| <http://localhost:8000/api/v1/charts?country=us&category=top-podcasts> | the chart |
| <http://localhost:8000/api/v1/podcasts?search=joe> | search |
| <http://localhost:8000/api/v1/podcasts/1> | podcast detail + recent episodes |
| <http://localhost:8000/admin/> → **Scrape runs** | audit trail of every scrape |

> Or seed a curated, self-contained dataset in one shot:
> `docker compose exec web python manage.py seed_demo`
> (12 well-known shows, enriched, with episodes + 3 days of synthetic chart history).

---

## 1. Project layout

```
myproject/            Django project — settings, celery app + beat schedule, root urls
├── podcasts/         domain: Category / Podcast / Episode + write-services (de-dup, UPSERT, aggregates)
├── charts/           ChartEntry (composite PK, month-partitioned) + partition mgmt + chart services
├── ingestion/        scrapers, enrichment, episode sync, Celery tasks, ScrapeRun audit, mgmt commands
│   ├── clients/      HttpClient (retry/backoff/rate-limit/robots) + Spotify/Apple/PodcastIndex/Podchaser
│   ├── scrapers/     raw payload → normalised ChartScrapeResult
│   └── management/   scrape_charts · enrich_podcasts · sync_episodes · manage_partitions · charts_summary · seed_demo
└── api/              DRF serializers, filters, pagination, views, urls, error envelope

bin/
├── dev/              LOCAL DEV — docker-compose.yml + Dockerfile + celery.dockerfile + .env  ← run from here
└── prod/             production compose (uses the repo-root Dockerfile + celery.dockerfile)

requirements.txt      app dependencies (prod server `uwsgi` is installed by the root Dockerfile only)
.env.example          annotated template for a fresh environment
```

---

## 2. Architecture

```
              Celery beat  (schedule: myproject/celery.py, times in Asia/Baku)
   06:00 │ 06:30 │        every 20m │        07:00 │  every 6h  │  daily 00:10
   spotify│podchaser│  enrich_pending │ sync_all_eps │ sync recent │ ensure_partitions
      └────┴──── scrape_*_charts ─────┴──────────────┴─────────────┴──────────────┐
                     │                                                            │
                     ▼                                                            ▼
        ingestion.scrapers                              ingestion.enrichment / ingestion.episodes
     Spotify JSON API  ·  Podchaser GraphQL (+HTML fallback)     Apple / PodcastIndex / Podchaser  ·  RSS via HttpClient
                     │                                                            │
                     ▼                                                            │
               ingestion.pipeline                                                │
   • bulk-resolve shows ─ 1 batched SELECT per slice; resolve_podcast() only for new shows
   •   de-dup: rss/apple/pi/pc/spotify id → normalised "title|publisher"
   • upsert_chart_entries()   ─ INSERT … ON CONFLICT on the 6-col composite PK
   • backfill_rank_movement() ─ previous_rank / rank_change vs. yesterday's snapshot
   • upsert_episodes()        ─ INSERT … ON CONFLICT (podcast, guid)
                     │
                     ▼
        PostgreSQL 17   podcasts_podcast · podcasts_episode
                        charts_chartentry   ← PARTITION BY RANGE (date)
                            charts_chartentry_2024_01 … _2027_xx … _default
                     │
                     ▼
        api  (DRF /api/v1/)  +  Redis response cache  ──►  front-end / mobile
        GET /charts   ·   GET /podcasts   ·   GET /podcasts/{id}   ·   GET /podcasts/{id}/episodes
```

Each pipeline stage is its own Celery task on its own queue, so a slow feed
crawl never blocks a chart scrape, and every failure is isolated and retried
individually. Every scrape/enrich/episode batch writes a **`ScrapeRun`** row
(status, rows written, errors) — visible in the admin, so "the system collects
data daily" is auditable.

---

## 3. Requirement traceability (`cont.-task.pdf`)

| TT § | Requirement | Where it lives |
|------|-------------|----------------|
| **2.1** | Daily scrape of Spotify Podcast Charts | `ingestion/clients/spotify.py` → live endpoint `GET podcastcharts.byspotify.com/api/charts/<category>?region=<cc>&limit=100`; task `ingestion.tasks.scrape_spotify_charts` (beat 06:00). |
| **2.1** | Daily scrape of Podchaser Charts | `ingestion/clients/podchaser.py` (official GraphQL API, key required) + `ingestion/scrapers/podchaser.py` HTML fallback; task `scrape_podchaser_charts` (beat 06:30). |
| **2.1** | Grouped by **Country** and **Category** | Scrapers iterate `SPOTIFY_CHART_REGIONS × SPOTIFY_CHART_CATEGORIES` / `PODCHASER_CHART_COUNTRIES × PODCHASER_CHART_CATEGORIES`; both stored on every `ChartEntry`. |
| **2.1** | Once per day, keep charts **fresh** | `crontab(hour=6)` beat entries; charting podcasts' feeds also refreshed every 6 h (`sync_episodes_for_recent_charts`). |
| **2.2** | Enrichment via Apple Podcasts / PodcastIndex / Podchaser (one **or more**) | `ingestion/enrichment.py` orchestrator. Apple/iTunes needs no key (always on); PodcastIndex & Podchaser are opt-in. Providers that lack keys or fail are skipped; `enrichment_sources` records who contributed; each provider's raw payload is kept in `Podcast.raw_metadata[provider]`. Fresh metadata (< 30 d) short-circuits the API calls. |
| **2.2** | Store name, description, publisher/author, cover URL, categories, ratings, publish frequency | `Podcast` fields `title / description / publisher / author / image_url / categories (M2M) / rating_average / rating_count / publish_frequency`. `publish_frequency` is inferred from the median gap between the last 12 episodes. |
| **2.3** | Store podcast metadata **and all episodes** | `podcasts.Episode` (FK → `Podcast`), populated by `ingestion/episodes.py`. |
| **2.3** | Periodically fetch **new** episodes (RSS **or** API) | `sync_podcast_episodes` — feed body fetched via the shared `HttpClient` (timeout + retries + conditional GET `If-None-Match`/`If-Modified-Since`), then parsed with `feedparser`; falls back to the PodcastIndex episodes API. Beat: daily full pass + 6-hourly pass for charting shows. Dirty feed values (negative `itunes:season`, out-of-range ints) are coerced; a single bad episode is skipped, not the batch. |
| **3.1** | `GET /api/v1/charts` — `country, category, date (default latest), source` | `api/views.py::ChartView`. Also `type` (podcasts/episodes), `limit`, `offset`. |
| **3.1** | Response = rank-ordered list of podcasts / top episodes | `ChartEntrySerializer` — `rank, previous_rank, rank_change, is_new` + denormalised snapshot + resolved `podcast`/`episode` summary, ordered by `rank`. |
| **3.2** | `GET /api/v1/podcasts` — paginated, **search**, **category filter** | `PodcastListView` + `api/filters.py::PodcastFilter` (`search`, `category` slug/id, `language`, `publish_frequency`, `min_rating`, `has_episodes`, `ordering`). Page-number pagination. |
| **3.2** | `GET /api/v1/podcasts/{id}` — full metadata + latest episodes, **cursor / offset pagination** | `PodcastDetailView` → metadata + 10 recent episodes + `episodes_url`. `PodcastEpisodesView` (`/podcasts/{id}/episodes`) → **cursor** by default, `?paginate=offset` for limit/offset. |
| **3 – scale** | **Table partitioning** of daily chart records | `charts_chartentry` is `PARTITION BY RANGE (date)` — monthly partitions + a `DEFAULT` catch-all so an INSERT can never fail. Built in `charts/migrations/0001_initial.py` (raw SQL — Django's schema editor can't express `PARTITION BY`); rolled forward daily by `charts.tasks.ensure_chart_partitions` / `manage.py manage_partitions`. |
| **3 – scale** | **Indexing** of `country`, `category`, `date` | `chartentry_sccd_idx (source, country, category, date)` — the mandated index — plus a covering `(…, chart_type, date, rank)` index the Chart API planner uses, `(date)`, and FK+date indexes. Podcast search uses `pg_trgm` GIN indexes (migration `0002`). Episodes: `(podcast, published_at DESC NULLS LAST, id DESC)`. |
| **3 – scale** | **UPSERT** on episodes by GUID/URL, no duplicates | `upsert_episodes` → `bulk_create(update_conflicts=True, unique_fields=["podcast","guid"])`; `episode_guid()` guarantees a stable key (feed `<guid>` → audio URL → external id → deterministic hash). Charts UPSERT on the 6-col composite PK. Podcast de-dup in `resolve_podcast`. |
| **3 – scale** | Avoid **needless load** | No per-row raw JSON on `ChartEntry`; podcasts de-duped once per scrape **slice** via one batched `SELECT` (a re-scrape of a 200-row chart is ~7 queries, not ~400); unchanged feeds cost a single `304`; `/health` never `COUNT`s the partitioned table. |
| **5 – accept.** | Old data not deleted, history preserved | Nothing hard-deletes. Each day appends a new `date` slice; dead feeds are hidden with `is_active=False`, never removed. |
| **5 – accept.** | Low latency + pagination | Redis response cache on `/charts` (10 min) + cached "latest date" resolver; a chart page is one bounded index range-scan; `.only()` / `select_related` / `prefetch_related`; trigram search; every list endpoint paginated. |
| **4 – tech** | Python, Celery, PostgreSQL | Django 5.2 · Celery 5.5 · PostgreSQL 17. Scraping: `requests` + `beautifulsoup4` + `lxml` + `feedparser`; retries via `tenacity`; cache via `django-redis`; docs via `drf-yasg`; admin theme `jazzmin`. |

---

## 4. Data model

### `podcasts.Podcast`
De-dup identifiers, each a **partial unique** constraint: `rss_url`, `apple_id`,
`podcastindex_id`, `podcastindex_guid`, `podchaser_id`, `spotify_id`; plus a
normalised `match_key = "<slug title>|<slug publisher>"` fallback.
Metadata: `title, description, publisher, author, image_url, website_url,
language, country, explicit, categories (M2M), rating_average, rating_count,
publish_frequency`. Bookkeeping: `enrichment_status / enrichment_sources /
raw_metadata` (per-provider payloads), `episodes_synced_at / feed_etag`,
`total_episodes / last_published_at` (denormalised, kept fresh by signals + a
nightly reconcile task).

### `podcasts.Episode`
`UNIQUE (podcast, guid)` — the UPSERT key. `title, description, summary,
published_at, duration_seconds, episode_number, season_number, episode_type,
explicit, audio_url, audio_type, audio_length_bytes, image_url, external_ids`.
Index `(podcast, published_at DESC NULLS LAST, id DESC)` for "newest episodes of
this podcast".

### `charts.ChartEntry`  (partitioned)
Natural composite PK **`(date, source, country, category, chart_type, rank)`** —
it is the uniqueness rule, it contains the partition key (a Postgres
requirement) and it is the `ON CONFLICT` target for the daily UPSERT.
`previous_rank`, `rank_change`, nullable FKs `podcast` / `episode`, and a
denormalised snapshot (`title, publisher, image_url, external_id`) so the Chart
API renders even before enrichment resolves the entity. **No raw JSON per row** —
this table grows by thousands of rows/day forever; the rich provider data lives
once on `Podcast.raw_metadata`.

### `ingestion.ScrapeRun`
One row per scrape/enrichment/episode batch — `source, kind, status, started_at,
finished_at, rows_written, slices_ok/failed, stats, error`.

---

## 5. API reference (`/api/v1`)

Interactive docs: **`/swagger/`** and **`/redoc/`**. All endpoints are `GET`,
public, JSON, rate-limited (`120/min` anon).

### `GET /charts`  — TT §3.1
| param | default | notes |
|-------|---------|-------|
| `source` | `spotify` | `spotify` \| `podchaser` |
| `country` | `us` | ISO-3166 alpha-2 |
| `category` | `top-podcasts` | slug: `top-podcasts`, `top-episodes`, `comedy`, `news`, … |
| `date` | latest available | `YYYY-MM-DD` — history is queryable per day |
| `type` | `podcasts` | `podcasts` \| `episodes` |
| `limit` / `offset` | `200` / `0` | |

```jsonc
{
  "source": "spotify", "country": "us", "category": "top-podcasts",
  "chart_type": "podcasts", "date": "2026-09-09", "is_latest": true, "count": 200,
  "results": [
    { "rank": 1, "previous_rank": 2, "rank_change": 1, "is_new": false,
      "title": "The Joe Rogan Experience", "publisher": "...", "image_url": "...",
      "external_id": "spotify:show:4rOoJ6...", "external_url": "https://open.spotify.com/show/...",
      "podcast": { "id": 4, "title": "...", "apple_id": "360084272", "enrichment_status": "done" },
      "episode": null }
  ]
}
```

### `GET /podcasts`  — TT §3.2
`?search=` (title / publisher / author — trigram-indexed) · `?category=<slug|id>` ·
`?language=` · `?publish_frequency=` · `?min_rating=` · `?has_episodes=` ·
`?ordering=title|-rating_average|-total_episodes|-last_published_at` ·
`?page=` · `?page_size=` (≤100).
Envelope: `{count, num_pages, page, page_size, next, previous, results:[…]}`.

### `GET /podcasts/{id}`  — TT §3.2
Full metadata + `categories` + `external_ids` + 10 `recent_episodes` +
`episodes_url`. `?id_type=apple|podcastindex|podchaser|spotify` looks the podcast
up by an external id instead of the internal one.

### `GET /podcasts/{id}/episodes`  — TT §3.2
Newest first. **Cursor pagination by default** (`?cursor=`); `?paginate=offset`
for `limit`/`offset`; `?detail=1` for the full episode payload.

### Supporting
* `GET /categories?source=` — taxonomy backing the `?category=` filter.
* `GET /health` — liveness + freshness probe (`503` if chart data > 3 days stale). O(1).

---

## 6. Configuration (`bin/dev/.env`)

`bin/dev/.env` ships with a working local Postgres/Redis setup. Everything below
is optional and only widens what the platform can do:

```ini
# --- enrichment providers (all optional; Apple/iTunes always works with no key) ---
PODCASTINDEX_API_KEY=            # free: https://api.podcastindex.org
PODCASTINDEX_API_SECRET=
PODCHASER_API_KEY=               # free: https://www.podchaser.com/profile/settings/api
PODCHASER_API_SECRET=            #  ↑ also required to use Podchaser as a CHART source

# --- how wide the daily crawl is (comma lists) ---
SPOTIFY_CHART_REGIONS=us,gb,de,fr,es,it,br,au,ca,in,mx,se,ie,nl,jp
SPOTIFY_CHART_CATEGORIES=top-podcasts,top-episodes,comedy,news,true-crime,business,society-culture,sports,education,technology
PODCHASER_CHART_COUNTRIES=us,gb,de,ca,au
PODCHASER_CHART_CATEGORIES=all,comedy,news,true-crime,business,society-culture,technology,sports

# --- knobs ---
CHART_PARTITION_AHEAD_MONTHS=3   # how many months of partitions to keep pre-created
ENRICHMENT_TTL_DAYS=30           # re-enrich a podcast only after its metadata is this old
EPISODE_REFRESH_MIN_INTERVAL_HOURS=12
THROTTLE_ANON=120/min
```

After editing `.env`: `docker compose up -d` (re-reads it). See `.env.example`
for the full annotated list.

---

## 7. Management commands

Run inside the container: `docker compose exec web python manage.py <cmd>`.

| Command | Purpose |
|---------|---------|
| `scrape_charts [--source spotify\|podchaser\|all] [--regions ...] [--countries ...] [--categories ...] [--enqueue-followups]` | Run a chart scrape now (what the beat task does). Omit list args for the full configured set. |
| `enrich_podcasts [--limit N] [--ids 1,2] [--all] [--force] [--providers apple,podcastindex]` | Metadata enrichment. |
| `sync_episodes [--limit N] [--ids 1,2] [--all] [--force]` | Fetch new episodes from RSS / PodcastIndex. |
| `manage_partitions [--list] [--months-ahead N]` | Create / inspect the monthly chart partitions. |
| `charts_summary [--days N]` | Row counts, date coverage, per-slice freshness. |
| `seed_demo [--no-episodes] [--days N]` | Curated, self-contained demo dataset. |

The **daily schedule** (`myproject/celery.py`) already runs `scrape_charts` at
06:00 / 06:30, `enrich_pending` every 20 min, `sync_all_episodes` at 07:00, and
`ensure_chart_partitions` at 00:10 — no manual intervention needed once
`celery-beat` is up. Trigger one now without waiting:

```bash
docker compose exec web python manage.py shell -c \
 "from ingestion.tasks import scrape_spotify_charts; print(scrape_spotify_charts.delay())"
```

---

## 8. Tests

```bash
docker compose exec web python manage.py test podcasts charts ingestion api
# or locally against the dev Postgres:  python manage.py test ...
```

**80 tests**, run against real PostgreSQL: podcast de-dup (id match, `match_key`
fallback, id back-fill, concurrent-insert recovery) · episode UPSERT idempotency
+ dirty-feed-integer coercion + row-by-row fallback · chart UPSERT + partition
routing + deterministic rank-movement + latest-date cache invalidation ·
Spotify/Podchaser scraper parsing (fixtures for the JSON API, GraphQL,
`__NEXT_DATA__` and DOM fallbacks) · enrichment merge rules + provider-crash
isolation + freshness short-circuit · RSS parsing (duration formats, conditional
GET, 304) · `HttpClient` retry / robots / rate-limit · every API endpoint
(pagination, filters, cursor/offset, error envelope, response cache).

---

## 9. Production

`bin/prod/docker-compose.yml` builds from the repo-root `Dockerfile`
(installs `uwsgi`) + `celery.dockerfile`, and expects a `bin/prod/.env`
(copy `.env.example`, set real `POSTGRES_*` / `REDIS_*` / `DJANGO_SECRET_KEY`,
`DEBUG=False`). Services: `app` (uwsgi, runs `migrate` + `collectstatic` on
start), `postgres`, `redis`, `celery`, `celery-beat`. It joins an external
`nginx-proxy` network for TLS termination.

---

## 10. Notes on the scrapers

* **Spotify** — the chart site's own JSON endpoint returns a rank-ordered array
  (rank = array position); no key required. If it ever changes shape the client
  falls back to any chart JSON embedded in the page HTML.
* **Podchaser** — the public `/charts` page is fully client-rendered, so charts
  come from the **official GraphQL API** (OAuth2 client-credentials, free key).
  The query lives in `settings.PODCHASER_CHART_QUERY` so it can be tuned to their
  current schema without a code change. No key ⇒ Podchaser is skipped and only
  Spotify charts load (Apple-based enrichment still works fully).
* All outbound HTTP goes through `ingestion/clients/base.py::HttpClient`:
  descriptive User-Agent, `robots.txt` enforcement (fetched with a timeout),
  per-host rate limiting, exponential-backoff retries on 429 / 5xx.
