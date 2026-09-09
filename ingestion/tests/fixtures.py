"""Inline fixtures for scraper/enrichment tests (kept small & representative)."""
from __future__ import annotations

# --- Spotify chart JSON API (rank = array position; no explicit rank field) ----
SPOTIFY_TOP_JSON = [
    {
        "chartRankMove": "UNCHANGED",
        "showUri": "spotify:show:aaa111",
        "showName": "The Example Daily",
        "showPublisher": "Example News",
        "showImageUrl": "https://img.example/aaa.jpg",
        "showDescription": "A daily news podcast.",
    },
    {
        "chartRankMove": "UP",
        "showUri": "spotify:show:bbb222",
        "showName": "Comedy Hour",
        "showPublisher": "LOL Media",
        "showImageUrl": "https://img.example/bbb.jpg",
    },
    {
        "chartRankMove": "NEW",
        "showUri": "spotify:show:ccc333",
        "showName": "Fresh Voices",
        "showPublisher": "Indie Co",
    },
]

# --- Spotify HTML fallback (only the __NEXT_DATA__ blob matters) ---------------
SPOTIFY_HTML = """<!doctype html><html><head><title>charts</title></head><body>
<div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"region":"us","chartEntries":[
  {"chartRankMove":"UNCHANGED","showUri":"spotify:show:zzz","showName":"HTML Show One","showPublisher":"Pub One","showImageUrl":"https://img/x1.jpg"},
  {"chartRankMove":"DOWN","showUri":"spotify:show:yyy","showName":"HTML Show Two","showPublisher":"Pub Two"}
]}},"page":"/[region]/[chart]"}
</script></body></html>"""

# --- Spotify top episodes JSON ---------------------------------------------
SPOTIFY_EPISODES_JSON = [
    {
        "chartRankMove": "UP",
        "episodeUri": "spotify:episode:ep111",
        "episodeName": "Big Interview",
        "episodeImageUrl": "https://img.example/ep111.jpg",
        "showUri": "spotify:show:aaa111",
        "showName": "The Example Daily",
        "showPublisher": "Example News",
        "showImageUrl": "https://img.example/aaa.jpg",
    },
]

# --- Podchaser GraphQL charts response (data payload) ----------------------
PODCHASER_GQL_DATA = {
    "charts": {
        "data": [
            {"rank": 1, "podcast": {
                "id": "12345", "title": "Podchaser Number One",
                "description": "PC1 desc", "webUrl": "https://www.podchaser.com/podcasts/p1-12345",
                "rssUrl": "https://feeds.example/pc1.xml", "imageUrl": "https://pc.img/1.jpg",
                "applePodcastsId": "555001", "ratingAverage": 4.6, "author": {"name": "PC Publisher"}}},
            {"rank": 2, "podcast": {
                "id": "12346", "title": "Podchaser Number Two",
                "imageUrl": "https://pc.img/2.jpg", "author": {"name": "Another Pub"},
                "webUrl": "https://www.podchaser.com/podcasts/p2-12346"}},
        ]
    }
}

# --- Podchaser charts page (Next.js data) --------------------------------
PODCHASER_HTML = """<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"charts":{"country":"US","results":[
  {"__typename":"Podcast","id":"12345","title":"Podchaser Number One","imageUrl":"https://pc.img/1.jpg",
   "author":{"name":"PC Publisher"},"webUrl":"/podcasts/podchaser-number-one-12345","applePodcastsId":"555001","rssUrl":"https://feeds.example/pc1.xml"},
  {"__typename":"Podcast","id":"12346","title":"Podchaser Number Two","imageUrl":"https://pc.img/2.jpg",
   "author":{"name":"Another Pub"},"webUrl":"/podcasts/podchaser-number-two-12346"}
]}}}}
</script></body></html>"""

PODCHASER_DOM_HTML = """<!doctype html><html><body>
<div class="chart-list">
  <ul>
    <li><a href="/podcasts/dom-show-one-1"><img src="https://d/1.jpg" alt="Dom Show One"/>Dom Show One</a></li>
    <li><a href="/podcasts/dom-show-two-2"><img src="https://d/2.jpg" alt="Dom Show Two"/>Dom Show Two</a></li>
    <li><a href="/podcasts/dom-show-one-1">Dom Show One</a></li>
  </ul>
</div></body></html>"""

# --- iTunes lookup result --------------------------------------------
APPLE_LOOKUP = {
    "resultCount": 1,
    "results": [
        {
            "collectionId": 1200361736,
            "collectionName": "The Example Daily",
            "artistName": "Example News",
            "feedUrl": "https://feeds.example/daily.xml",
            "artworkUrl600": "https://itunes.img/daily600.jpg",
            "artworkUrl100": "https://itunes.img/daily100.jpg",
            "primaryGenreName": "News",
            "genres": ["News", "Daily News", "Podcasts"],
            "trackCount": 1500,
            "country": "USA",
            "contentAdvisoryRating": "Clean",
        }
    ],
}

# --- PodcastIndex feed result -------------------------------------
PODCASTINDEX_FEED = {
    "status": "true",
    "feed": {
        "id": 920666,
        "podcastGuid": "9b024349-ccf0-5f69-a609-6b82873eab3c",
        "title": "The Example Daily",
        "url": "https://feeds.example/daily.xml",
        "originalUrl": "https://feeds.example/daily.xml",
        "link": "https://example.com/daily",
        "description": "In-depth reporting, every weekday.",
        "author": "Example News",
        "ownerName": "Example News Org",
        "image": "https://pi.img/daily.jpg",
        "artwork": "https://pi.img/daily-art.jpg",
        "language": "en-us",
        "explicit": 0,
        "episodeCount": 1500,
        "itunesId": 1200361736,
        "type": 0,
        "categories": {"9": "News", "10": "Politics"},
    },
}

PODCASTINDEX_EPISODES = {
    "status": "true",
    "items": [
        {
            "id": 111,
            "title": "Monday Briefing",
            "description": "What you need to know.",
            "guid": "pi-guid-111",
            "datePublished": 1_726_000_000,
            "enclosureUrl": "https://media.example/111.mp3",
            "enclosureType": "audio/mpeg",
            "enclosureLength": 24_000_000,
            "duration": 1620,
            "episode": 501,
            "season": 3,
            "episodeType": "full",
            "explicit": 0,
            "image": "https://pi.img/ep111.jpg",
        },
        {
            "id": 112,
            "title": "Tuesday Briefing",
            "guid": "pi-guid-112",
            "datePublished": 1_726_086_400,
            "enclosureUrl": "https://media.example/112.mp3",
            "enclosureType": "audio/mpeg",
            "duration": 1500,
        },
    ],
}
