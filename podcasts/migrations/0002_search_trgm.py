"""PostgreSQL trigram indexes for fast `?search=` on the podcast list (TT §3.2).

``CreateExtension`` / the raw ``CREATE INDEX`` are no-ops on non-PostgreSQL
backends, so this migration is safe for the SQLite test/dev fallback.
"""
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

_TRGM_INDEXES = [
    ('podcast_title_trgm', 'title'),
    ('podcast_publisher_trgm', 'publisher'),
    ('podcast_author_trgm', 'author'),
]


def create_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for name, column in _TRGM_INDEXES:
        schema_editor.execute(
            f'CREATE INDEX IF NOT EXISTS "{name}" ON "podcasts_podcast" '
            f'USING gin ("{column}" gin_trgm_ops);'
        )


def drop_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for name, _ in _TRGM_INDEXES:
        schema_editor.execute(f'DROP INDEX IF EXISTS "{name}";')


class Migration(migrations.Migration):

    dependencies = [
        ("podcasts", "0001_initial"),
    ]

    operations = [
        TrigramExtension(),
        migrations.RunPython(create_trgm_indexes, drop_trgm_indexes),
    ]
