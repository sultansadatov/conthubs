"""Initial migration for the chart-history table.

Django's schema editor cannot emit ``PARTITION BY RANGE`` so we keep the model
in migration *state* via ``CreateModel`` but build the real table with raw SQL:

* on **PostgreSQL** -> a range-partitioned table (monthly partitions + a DEFAULT
  catch-all) with the composite PK and every index from the model's Meta
  (TT §3: Table Partitioning + mandatory ``(source, country, category, date)``
  index);
* on any **other backend** (SQLite in the test suite / quick local runs) ->
  a plain table created from the historical model, so everything still works.
"""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


PARENT_TABLE = "charts_chartentry"

CREATE_PARTITIONED_TABLE = f"""
CREATE TABLE "{PARENT_TABLE}" (
    "date"          date          NOT NULL,
    "source"        varchar(20)   NOT NULL,
    "country"       varchar(8)    NOT NULL,
    "category"      varchar(120)  NOT NULL,
    "chart_type"    varchar(20)   NOT NULL DEFAULT 'podcasts',
    "rank"          smallint      NOT NULL CHECK ("rank" >= 0),
    "previous_rank" smallint      NULL     CHECK ("previous_rank" >= 0),
    "rank_change"   smallint      NULL,
    "title"         varchar(500)  NOT NULL DEFAULT '',
    "publisher"     varchar(300)  NOT NULL DEFAULT '',
    "image_url"     varchar(1000) NOT NULL DEFAULT '',
    "external_url"  varchar(1000) NOT NULL DEFAULT '',
    "external_id"   varchar(200)  NOT NULL DEFAULT '',
    "scraped_at"    timestamptz   NOT NULL DEFAULT now(),
    "podcast_id"    bigint        NULL,
    "episode_id"    bigint        NULL,
    PRIMARY KEY ("date", "source", "country", "category", "chart_type", "rank")
) PARTITION BY RANGE ("date");
"""

FK_STATEMENTS = [
    f'ALTER TABLE "{PARENT_TABLE}" ADD CONSTRAINT "chartentry_podcast_fk" '
    f'FOREIGN KEY ("podcast_id") REFERENCES "podcasts_podcast" ("id") '
    f"ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;",
    f'ALTER TABLE "{PARENT_TABLE}" ADD CONSTRAINT "chartentry_episode_fk" '
    f'FOREIGN KEY ("episode_id") REFERENCES "podcasts_episode" ("id") '
    f"ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;",
]

INDEX_STATEMENTS = [
    f'CREATE INDEX "chartentry_sccd_idx" ON "{PARENT_TABLE}" ("source", "country", "category", "date");',
    f'CREATE INDEX "chartentry_lookup_idx" ON "{PARENT_TABLE}" ("source", "country", "category", "chart_type", "date", "rank");',
    f'CREATE INDEX "chartentry_date_idx" ON "{PARENT_TABLE}" ("date");',
    f'CREATE INDEX "chartentry_podcast_date_idx" ON "{PARENT_TABLE}" ("podcast_id", "date");',
    f'CREATE INDEX "chartentry_episode_date_idx" ON "{PARENT_TABLE}" ("episode_id", "date");',
]


def create_chart_table(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        schema_editor.create_model(apps.get_model("charts", "ChartEntry"))
        return

    schema_editor.execute(CREATE_PARTITIONED_TABLE)
    for stmt in FK_STATEMENTS + INDEX_STATEMENTS:
        schema_editor.execute(stmt)

    # monthly partitions + DEFAULT catch-all
    from charts.partitions import ensure_partitions

    ensure_partitions(using=connection)


def drop_chart_table(apps, schema_editor):
    schema_editor.execute(f'DROP TABLE IF EXISTS "{PARENT_TABLE}" CASCADE;')


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("podcasts", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ChartEntry",
                    fields=[
                        ("pk", models.CompositePrimaryKey("date", "source", "country", "category", "chart_type", "rank", blank=True, editable=False, primary_key=True, serialize=False)),
                        ("date", models.DateField(help_text="chart snapshot date (partition key)")),
                        ("source", models.CharField(choices=[("spotify", "Spotify"), ("podchaser", "Podchaser"), ("apple", "Apple Podcasts"), ("podcastindex", "PodcastIndex"), ("rss", "RSS feed"), ("internal", "Internal")], max_length=20)),
                        ("country", models.CharField(help_text="ISO-3166 alpha-2, lower-case", max_length=8)),
                        ("category", models.CharField(help_text="category slug, never empty (e.g. 'top-podcasts', 'comedy')", max_length=120)),
                        ("chart_type", models.CharField(choices=[("podcasts", "Podcasts"), ("episodes", "Episodes")], default="podcasts", max_length=20)),
                        ("rank", models.PositiveSmallIntegerField()),
                        ("previous_rank", models.PositiveSmallIntegerField(blank=True, null=True)),
                        ("rank_change", models.SmallIntegerField(blank=True, help_text="previous_rank - rank; >0 means moved up", null=True)),
                        ("title", models.CharField(blank=True, default="", max_length=500)),
                        ("publisher", models.CharField(blank=True, default="", max_length=300)),
                        ("image_url", models.URLField(blank=True, default="", max_length=1000)),
                        ("external_url", models.URLField(blank=True, default="", max_length=1000)),
                        ("external_id", models.CharField(blank=True, default="", max_length=200)),
                        ("scraped_at", models.DateTimeField(default=django.utils.timezone.now)),
                        ("episode", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chart_entries", to="podcasts.episode")),
                        ("podcast", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chart_entries", to="podcasts.podcast")),
                    ],
                    options={
                        "verbose_name": "chart entry",
                        "verbose_name_plural": "chart entries",
                        "ordering": ["-date", "source", "country", "category", "chart_type", "rank"],
                        "indexes": [
                            models.Index(fields=["source", "country", "category", "date"], name="chartentry_sccd_idx"),
                            models.Index(fields=["source", "country", "category", "chart_type", "date", "rank"], name="chartentry_lookup_idx"),
                            models.Index(fields=["date"], name="chartentry_date_idx"),
                            models.Index(fields=["podcast", "date"], name="chartentry_podcast_date_idx"),
                            models.Index(fields=["episode", "date"], name="chartentry_episode_date_idx"),
                        ],
                    },
                ),
            ],
            database_operations=[
                migrations.RunPython(create_chart_table, drop_chart_table),
            ],
        ),
    ]
