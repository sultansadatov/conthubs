from unittest import mock

from django.test import TestCase

from ingestion import enrichment
from podcasts.constants import EnrichmentStatus
from podcasts.models import Category
from podcasts.services import resolve_podcast

from .fixtures import APPLE_LOOKUP, PODCASTINDEX_FEED


_DEFAULT = object()


class FakeApple:
    name = "apple"

    def __init__(self, record=_DEFAULT):
        self._record = APPLE_LOOKUP["results"][0] if record is _DEFAULT else record

    def lookup(self, apple_id, country=None):
        return self._record

    def best_match(self, *, title, author="", feed_url=""):
        return self._record

    def close(self):
        pass


class FakePI:
    name = "podcastindex"

    def __init__(self, feed=None, enabled=True, boom=False):
        self._feed = feed
        self.enabled = enabled
        self._boom = boom

    def _maybe_boom(self):
        if self._boom:
            raise RuntimeError("podcastindex is down")

    def by_itunes_id(self, _):
        self._maybe_boom()
        return self._feed

    def by_feed_url(self, _):
        self._maybe_boom()
        return self._feed

    def by_guid(self, _):
        return None

    def search(self, _term):
        return []

    def close(self):
        pass


class FakePodchaser:
    name = "podchaser"

    def __init__(self, meta=None, enabled=True):
        self._meta = meta
        self.enabled = enabled

    def podcast_metadata(self, **kw):
        return self._meta

    def close(self):
        pass


class EnrichmentTests(TestCase):
    def setUp(self):
        self.podcast = resolve_podcast(title="The Example Daily", publisher="", apple_id="1200361736").podcast

    def _run(self, apple=None, pi=None, podchaser=None, **kw):
        with mock.patch.object(enrichment, "ApplePodcastsClient", return_value=apple or FakeApple()), \
             mock.patch.object(enrichment, "PodcastIndexClient", return_value=pi or FakePI(enabled=False)), \
             mock.patch.object(enrichment, "PodchaserClient", return_value=podchaser or FakePodchaser(enabled=False)):
            return enrichment.enrich_podcast(self.podcast, **kw)

    def test_apple_only_fills_core_fields(self):
        res = self._run()
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.publisher, "Example News")
        self.assertEqual(self.podcast.rss_url, "https://feeds.example/daily.xml")
        self.assertEqual(self.podcast.image_url, "https://itunes.img/daily600.jpg")
        self.assertEqual(res["sources"], ["apple"])
        # Apple gives identity + cover + genres (categories) -> DONE even without a description
        self.assertEqual(self.podcast.enrichment_status, EnrichmentStatus.DONE)
        self.assertEqual(self.podcast.description, "")
        self.assertTrue(Category.objects.filter(source="apple", name="News").exists())

    def test_partial_when_provider_gives_only_thin_data(self):
        thin = {"collectionId": 1, "collectionName": "X"}  # no artwork, no publisher, no genres
        res = self._run(apple=FakeApple(record=thin))
        self.assertEqual(res["status"], EnrichmentStatus.PARTIAL)

    def test_podcastindex_adds_description_promotes_to_done(self):
        self._run(pi=FakePI(feed=PODCASTINDEX_FEED["feed"]))
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.description, "In-depth reporting, every weekday.")
        self.assertEqual(self.podcast.podcastindex_id, "920666")
        self.assertEqual(self.podcast.language, "en-us")
        self.assertEqual(self.podcast.enrichment_status, EnrichmentStatus.DONE)

    def test_ratings_come_from_podchaser_and_refresh(self):
        pc = FakePodchaser(meta={"id": "999", "ratingAverage": 4.55, "ratingCount": 1200, "categories": []})
        self._run(pi=FakePI(feed=PODCASTINDEX_FEED["feed"]), podchaser=pc)
        self.podcast.refresh_from_db()
        self.assertEqual(float(self.podcast.rating_average), 4.55)
        self.assertEqual(self.podcast.rating_count, 1200)
        self.assertEqual(self.podcast.podchaser_id, "999")

    def test_fill_if_empty_does_not_overwrite_existing(self):
        self.podcast.description = "hand written"
        self.podcast.save(update_fields=["description"])
        self._run(pi=FakePI(feed=PODCASTINDEX_FEED["feed"]))
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.description, "hand written")

    def test_provider_crash_is_isolated(self):
        res = self._run(pi=FakePI(boom=True))
        self.podcast.refresh_from_db()
        self.assertEqual(res["sources"], ["apple"])  # apple still applied
        self.assertNotIn("podcastindex", res["sources"])

    def test_no_data_marks_failed(self):
        res = self._run(apple=FakeApple(record=None))
        self.assertEqual(res["status"], EnrichmentStatus.FAILED)
