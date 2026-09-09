import time
from unittest import mock

import feedparser
from django.test import TestCase

from ingestion import episodes
from ingestion.episodes import _parse_duration, _struct_to_dt, sync_podcast_episodes
from podcasts.models import Episode
from podcasts.services import resolve_podcast

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Feed Show</title>
  <itunes:image href="https://img/feed.jpg"/>
  {items}
</channel></rss>"""

ITEM = """
  <item>
    <title>{title}</title>
    <guid isPermaLink="false">{guid}</guid>
    <pubDate>{pubdate}</pubDate>
    <description>{desc}</description>
    <itunes:duration>{duration}</itunes:duration>
    <itunes:episode>{number}</itunes:episode>
    <itunes:explicit>{explicit}</itunes:explicit>
    <enclosure url="https://m/{guid}.mp3" length="99" type="audio/mpeg"/>
  </item>"""


def build_rss(items):
    body = "".join(
        ITEM.format(
            title=i["title"], guid=i["guid"], pubdate=i["pubdate"], desc=i.get("desc", "d"),
            duration=i.get("duration", "10:00"), number=i.get("number", "1"),
            explicit=i.get("explicit", "no"),
        )
        for i in items
    )
    return RSS_TEMPLATE.format(items=body)


class _FakeResponse:
    def __init__(self, *, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


def patch_http(response):
    """Patch ingestion.episodes.HttpClient so _from_rss gets `response` back."""
    fake_client = mock.Mock()
    fake_client.get.return_value = response
    fake_client.close.return_value = None
    return mock.patch.object(episodes, "HttpClient", return_value=fake_client)


class DurationParsingTests(TestCase):
    def test_various_formats(self):
        self.assertEqual(_parse_duration("1620"), 1620)
        self.assertEqual(_parse_duration("27:00"), 1620)
        self.assertEqual(_parse_duration("01:27:00"), 5220)
        self.assertEqual(_parse_duration("3:05"), 185)
        self.assertIsNone(_parse_duration(""))
        self.assertIsNone(_parse_duration(None))
        self.assertIsNone(_parse_duration("garbage"))

    def test_struct_to_dt_is_aware(self):
        st = time.struct_time((2026, 3, 1, 12, 0, 0, 0, 0, 0))
        dt = _struct_to_dt(st)
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 3, 1))


class SyncFromRssTests(TestCase):
    def setUp(self):
        self.podcast = resolve_podcast(
            title="Feed Show", publisher="FS", rss_url="https://feeds.example/show.xml"
        ).podcast
        self.items = [
            {"guid": "g1", "title": "Ep One", "pubdate": "Mon, 03 Mar 2026 08:00:00 +0000",
             "duration": "30:00", "explicit": "yes"},
            {"guid": "g2", "title": "Ep Two", "pubdate": "Sun, 02 Mar 2026 08:00:00 +0000",
             "duration": "1200", "number": "2"},
        ]

    def test_sync_creates_then_upserts(self):
        resp = _FakeResponse(content=build_rss(self.items).encode(), headers={"ETag": '"abc"'})
        with patch_http(resp):
            r1 = sync_podcast_episodes(self.podcast, force=True)

        self.assertEqual(r1["created"], 2)
        self.assertEqual(r1["source"], "rss")
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 2)

        ep1 = Episode.objects.get(podcast=self.podcast, guid="g1")
        self.assertEqual(ep1.duration_seconds, 1800)
        self.assertEqual(ep1.audio_url, "https://m/g1.mp3")
        self.assertEqual(ep1.audio_type, "audio/mpeg")
        self.assertTrue(ep1.explicit)

        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.total_episodes, 2)
        self.assertEqual(self.podcast.feed_etag, '"abc"')
        self.assertIsNotNone(self.podcast.last_published_at)

        with patch_http(_FakeResponse(content=build_rss(self.items).encode())):
            r2 = sync_podcast_episodes(self.podcast, force=True)
        self.assertEqual(r2["created"], 0)
        self.assertEqual(r2["updated"], 2)
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 2)

    def test_new_item_appended_on_next_sync(self):
        with patch_http(_FakeResponse(content=build_rss(self.items).encode())):
            sync_podcast_episodes(self.podcast, force=True)
        more = self.items + [{"guid": "g3", "title": "Ep Three", "pubdate": "Tue, 04 Mar 2026 08:00:00 +0000"}]
        with patch_http(_FakeResponse(content=build_rss(more).encode())):
            r = sync_podcast_episodes(self.podcast, force=True)
        self.assertEqual(r["created"], 1)
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 3)

    def test_304_not_modified_is_a_noop(self):
        with patch_http(_FakeResponse(status_code=304)):
            r = sync_podcast_episodes(self.podcast)
        self.assertEqual(r["fetched"], 0)
        self.assertTrue(r["not_modified"])
        self.assertEqual(Episode.objects.filter(podcast=self.podcast).count(), 0)
        self.podcast.refresh_from_db()
        self.assertIsNotNone(self.podcast.episodes_synced_at)

    def test_conditional_headers_sent_when_known(self):
        self.podcast.feed_etag = '"cached"'
        self.podcast.save(update_fields=["feed_etag"])
        fake = mock.Mock()
        fake.get.return_value = _FakeResponse(status_code=304)
        fake.close.return_value = None
        with mock.patch.object(episodes, "HttpClient", return_value=fake):
            sync_podcast_episodes(self.podcast)
        sent_headers = fake.get.call_args.kwargs["headers"]
        self.assertEqual(sent_headers.get("If-None-Match"), '"cached"')

    def test_unparseable_feed_is_handled(self):
        with patch_http(_FakeResponse(content=b"<html>not a feed</html>")):
            r = sync_podcast_episodes(self.podcast, force=True)
        self.assertEqual(r["created"], 0)
        self.podcast.refresh_from_db()
        self.assertIsNotNone(self.podcast.episodes_synced_at)


class FeedparserContractTest(TestCase):
    """Guards the assumption that feedparser.parse accepts raw bytes."""

    def test_parse_accepts_bytes(self):
        d = feedparser.parse(build_rss([{"guid": "x", "title": "T",
                                         "pubdate": "Mon, 03 Mar 2026 08:00:00 +0000"}]).encode())
        self.assertEqual(len(d.entries), 1)
        self.assertEqual(d.entries[0].get("id"), "x")
