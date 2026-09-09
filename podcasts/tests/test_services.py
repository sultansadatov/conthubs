from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from podcasts import services
from podcasts.constants import PublishFrequency
from podcasts.models import Episode, Podcast
from podcasts.services import (
    infer_publish_frequency,
    recompute_podcast_aggregates,
    resolve_podcast,
    upsert_episodes,
)


class ResolvePodcastTests(TestCase):
    def test_creates_when_nothing_matches(self):
        res = resolve_podcast(title="Brand New Show", publisher="NewCo", apple_id="42")
        self.assertTrue(res.created)
        self.assertEqual(res.podcast.apple_id, "42")
        self.assertEqual(res.podcast.match_key, "brand-new-show|newco")

    def test_matches_on_external_id_and_backfills_missing_ids(self):
        first = resolve_podcast(title="Show", publisher="Co", apple_id="100").podcast
        res = resolve_podcast(title="Different Title", publisher="Whatever", apple_id="100", spotify_id="sp1")
        self.assertFalse(res.created)
        self.assertEqual(res.podcast.pk, first.pk)
        self.assertEqual(res.matched_on, "apple_id")
        first.refresh_from_db()
        self.assertEqual(first.spotify_id, "sp1")  # back-filled

    def test_falls_back_to_match_key_then_backfills_id(self):
        first = resolve_podcast(title="The Match", publisher="KeyCo").podcast
        res = resolve_podcast(title="The Match", publisher="KeyCo", podchaser_id="pc9")
        self.assertFalse(res.created)
        self.assertEqual(res.matched_on, "match_key")
        first.refresh_from_db()
        self.assertEqual(first.podchaser_id, "pc9")

    def test_does_not_clobber_existing_ids(self):
        first = resolve_podcast(title="Keep", publisher="Co", apple_id="AAA").podcast
        resolve_podcast(title="Keep", publisher="Co", apple_id="BBB")  # different apple id, same match_key
        first.refresh_from_db()
        self.assertEqual(first.apple_id, "AAA")  # unchanged

    def test_partial_unique_allows_many_blank_ids(self):
        resolve_podcast(title="A", publisher="x")
        resolve_podcast(title="B", publisher="y")
        self.assertEqual(Podcast.objects.filter(apple_id="").count(), 2)

    def test_recovers_from_concurrent_insert(self):
        """A parallel scrape inserts the same podcast between our lookup and our
        save -> we must recover, not crash on the unique-constraint violation."""
        def racer(*_a, **_kw):
            Podcast.objects.get_or_create(
                apple_id="RACE", defaults={"title": "Winner", "match_key": "winner|key"}
            )
            return "loser|key"  # stands in for build_match_key(): won't match the racer

        with mock.patch.object(services, "build_match_key", side_effect=racer):
            res = resolve_podcast(title="Loser", publisher="P", apple_id="RACE")

        self.assertFalse(res.created)
        self.assertEqual(res.matched_on, "apple_id")
        self.assertEqual(res.podcast.title, "Winner")
        self.assertEqual(Podcast.objects.filter(apple_id="RACE").count(), 1)


class UpsertEpisodesTests(TestCase):
    def setUp(self):
        self.podcast = resolve_podcast(title="Feed Show", publisher="FS").podcast

    def test_idempotent(self):
        entries = [
            {"guid": "g1", "title": "One", "published_at": timezone.now()},
            {"guid": "g2", "title": "Two", "published_at": timezone.now()},
        ]
        r1 = upsert_episodes(self.podcast, entries)
        r2 = upsert_episodes(self.podcast, entries)
        self.assertEqual((r1.created, r1.updated), (2, 0))
        self.assertEqual((r2.created, r2.updated), (0, 2))
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 2)

    def test_updates_changed_fields(self):
        upsert_episodes(self.podcast, [{"guid": "g1", "title": "Old"}])
        upsert_episodes(self.podcast, [{"guid": "g1", "title": "New Title"}])
        self.assertEqual(Episode.objects.get(podcast=self.podcast, guid="g1").title, "New Title")

    def test_guid_fallback_is_stable(self):
        entry = {"title": "No GUID", "audio_url": "https://m/x.mp3"}
        upsert_episodes(self.podcast, [entry])
        upsert_episodes(self.podcast, [entry])
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 1)
        self.assertEqual(Episode.objects.get(podcast=self.podcast).guid, "https://m/x.mp3")

    def test_dedupes_within_one_batch(self):
        r = upsert_episodes(self.podcast, [{"guid": "same", "title": "a"}, {"guid": "same", "title": "b"}])
        self.assertEqual(r.skipped, 1)
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 1)

    def test_dirty_feed_integers_are_coerced(self):
        # negative itunes:season / out-of-range itunes:episode / ms-timestamp length
        r = upsert_episodes(self.podcast, [{
            "guid": "dirty", "title": "Dirty",
            "season_number": -15, "episode_number": 16_291_534_800_000,
            "duration_seconds": -1, "audio_length_bytes": 51_024_328,
        }])
        self.assertEqual(r.created, 1)
        ep = Episode.objects.get(podcast=self.podcast, guid="dirty")
        self.assertIsNone(ep.season_number)
        self.assertIsNone(ep.episode_number)
        self.assertIsNone(ep.duration_seconds)
        self.assertEqual(ep.audio_length_bytes, 51_024_328)


class AggregatesTests(TestCase):
    def test_infer_publish_frequency(self):
        now = timezone.now()
        weekly = [now - timedelta(days=7 * i) for i in range(6)]
        daily = [now - timedelta(days=i) for i in range(6)]
        monthly = [now - timedelta(days=30 * i) for i in range(6)]
        self.assertEqual(infer_publish_frequency(weekly), PublishFrequency.WEEKLY)
        self.assertEqual(infer_publish_frequency(daily), PublishFrequency.DAILY)
        self.assertEqual(infer_publish_frequency(monthly), PublishFrequency.MONTHLY)
        self.assertEqual(infer_publish_frequency([now]), PublishFrequency.UNKNOWN)

    def test_recompute_sets_counts_and_last_published(self):
        podcast = resolve_podcast(title="Agg", publisher="A").podcast
        now = timezone.now()
        upsert_episodes(
            podcast,
            [{"guid": f"g{i}", "title": str(i), "published_at": now - timedelta(days=7 * i)} for i in range(5)],
        )
        out = recompute_podcast_aggregates(podcast)
        podcast.refresh_from_db()
        self.assertEqual(podcast.total_episodes, 5)
        self.assertEqual(out["publish_frequency"], PublishFrequency.WEEKLY)
        self.assertAlmostEqual(podcast.last_published_at.timestamp(), now.timestamp(), delta=5)
