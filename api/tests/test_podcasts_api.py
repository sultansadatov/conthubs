from django.test import TestCase
from rest_framework.test import APIClient

from podcasts.models import Category
from podcasts.services import attach_categories

from .factories import make_podcast


class PodcastListAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.radio = make_podcast("Radiolab", apple_id="10", publisher="WNYC", episodes=3, language="en")
        self.daily = make_podcast("The Daily", apple_id="11", publisher="NYT", episodes=5, language="en")
        self.aze = make_podcast("Azeri Cast", apple_id="12", publisher="Baku FM", language="az")
        attach_categories(self.radio, [("apple", "Science")])
        attach_categories(self.daily, [("apple", "News")])

    def test_pagination_envelope(self):
        body = self.client.get("/api/v1/podcasts", {"page_size": 2}).json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["num_pages"], 2)
        self.assertEqual(body["page_size"], 2)
        self.assertEqual(len(body["results"]), 2)
        self.assertIsNotNone(body["next"])

    def test_search(self):
        body = self.client.get("/api/v1/podcasts", {"search": "radio"}).json()
        self.assertEqual([p["title"] for p in body["results"]], ["Radiolab"])
        body = self.client.get("/api/v1/podcasts", {"search": "nyt"}).json()  # publisher hit
        self.assertEqual([p["title"] for p in body["results"]], ["The Daily"])

    def test_filter_by_category_slug_and_id(self):
        science = Category.objects.get(slug="science")
        by_slug = self.client.get("/api/v1/podcasts", {"category": "science"}).json()
        by_id = self.client.get("/api/v1/podcasts", {"category": str(science.id)}).json()
        self.assertEqual([p["title"] for p in by_slug["results"]], ["Radiolab"])
        self.assertEqual([p["title"] for p in by_id["results"]], ["Radiolab"])

    def test_filter_by_language_and_has_episodes(self):
        az = self.client.get("/api/v1/podcasts", {"language": "az"}).json()
        self.assertEqual([p["title"] for p in az["results"]], ["Azeri Cast"])
        with_eps = self.client.get("/api/v1/podcasts", {"has_episodes": "true"}).json()
        self.assertEqual(with_eps["count"], 2)

    def test_ordering(self):
        body = self.client.get("/api/v1/podcasts", {"ordering": "-total_episodes"}).json()
        self.assertEqual([p["title"] for p in body["results"]][0], "The Daily")


class PodcastDetailAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.podcast = make_podcast("Detail Cast", apple_id="900", publisher="DC", episodes=25)

    def test_detail_by_pk(self):
        body = self.client.get(f"/api/v1/podcasts/{self.podcast.pk}").json()
        self.assertEqual(body["title"], "Detail Cast")
        self.assertEqual(body["external_ids"]["apple_id"], "900")
        self.assertEqual(len(body["recent_episodes"]), 10)  # first page only
        self.assertTrue(body["episodes_url"].endswith(f"/api/v1/podcasts/{self.podcast.pk}/episodes"))
        self.assertEqual(body["total_episodes"], 25)

    def test_detail_by_external_id(self):
        body = self.client.get("/api/v1/podcasts/900", {"id_type": "apple"}).json()
        self.assertEqual(body["id"], self.podcast.pk)

    def test_missing_returns_404(self):
        self.assertEqual(self.client.get("/api/v1/podcasts/99999").status_code, 404)


class PodcastEpisodesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.podcast = make_podcast("Ep Cast", apple_id="800", episodes=30)

    def test_cursor_pagination_default(self):
        body = self.client.get(f"/api/v1/podcasts/{self.podcast.pk}/episodes", {"page_size": 10}).json()
        self.assertEqual(len(body["results"]), 10)
        self.assertIn("cursor=", body["next"])
        # newest first
        dates = [row["published_at"] for row in body["results"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_offset_pagination_opt_in(self):
        body = self.client.get(
            f"/api/v1/podcasts/{self.podcast.pk}/episodes", {"paginate": "offset", "limit": 5, "offset": 5}
        ).json()
        self.assertEqual(body["count"], 30)
        self.assertEqual(len(body["results"]), 5)

    def test_detail_flag_returns_full_payload(self):
        body = self.client.get(
            f"/api/v1/podcasts/{self.podcast.pk}/episodes", {"detail": "1", "page_size": 1}
        ).json()
        self.assertIn("description", body["results"][0])
        self.assertIn("audio_type", body["results"][0])

    def test_unknown_podcast_returns_404(self):
        self.assertEqual(
            self.client.get("/api/v1/podcasts/123456/episodes").status_code, 404
        )
