"""Shared HTTP client for every outbound request the platform makes.

Features (all configured from ``settings.HTTP_CLIENT``):

* a single pooled :class:`requests.Session` with a descriptive User-Agent;
* exponential-backoff retries (via :mod:`tenacity`) on connection errors,
  429 and 5xx;
* polite per-host rate limiting (a minimum interval between requests to the
  same host);
* optional ``robots.txt`` enforcement for scraping targets.

Enrichment API clients pass ``respect_robots=False`` (they hit documented
JSON APIs, not crawlable pages); the chart scrapers keep it on.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urlparse

import requests
from django.conf import settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("ingestion.http")


class HttpError(Exception):
    def __init__(self, message, *, status=None, url=None, body=""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body[:2000]


class RetryableHttpError(HttpError):
    """429 / 5xx / transport error — worth retrying."""


class RobotsDisallowed(HttpError):
    """The target's robots.txt forbids this path for our User-Agent."""


@dataclass
class _HostThrottle:
    min_interval: float
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last: float = 0.0

    def wait(self):
        with self._lock:
            gap = time.monotonic() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        rate_limit_per_host: float | None = None,
        respect_robots: bool | None = None,
        extra_headers: dict | None = None,
    ):
        cfg = settings.HTTP_CLIENT
        self.user_agent = user_agent or cfg["USER_AGENT"]
        self.timeout = timeout or cfg["TIMEOUT"]
        self.max_retries = cfg["MAX_RETRIES"] if max_retries is None else max_retries
        self.backoff_factor = backoff_factor or cfg["BACKOFF_FACTOR"]
        rate = cfg["RATE_LIMIT_PER_HOST"] if rate_limit_per_host is None else rate_limit_per_host
        self._min_interval = 1.0 / rate if rate and rate > 0 else 0.0
        self.respect_robots = cfg["RESPECT_ROBOTS"] if respect_robots is None else respect_robots

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
        if extra_headers:
            self.session.headers.update(extra_headers)

        self._throttles: dict[str, _HostThrottle] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------
    def get(self, url, **kw) -> requests.Response:
        return self._request("GET", url, **kw)

    def post(self, url, **kw) -> requests.Response:
        return self._request("POST", url, **kw)

    def get_json(self, url, **kw):
        resp = self.get(url, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"non-JSON response from {url}", status=resp.status_code, url=url,
                            body=resp.text) from exc

    def get_text(self, url, **kw) -> str:
        return self.get(url, **kw).text

    def post_json(self, url, *, json=None, data=None, headers=None, **kw):
        resp = self.post(url, json=json, data=data, headers=headers, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"non-JSON response from {url}", status=resp.status_code, url=url,
                            body=resp.text) from exc

    # -- internals ----------------------------------------------------
    def _throttle_for(self, host: str) -> _HostThrottle:
        with self._lock:
            t = self._throttles.get(host)
            if t is None:
                t = _HostThrottle(self._min_interval)
                self._throttles[host] = t
            return t

    def _check_robots(self, url: str):
        if not self.respect_robots:
            return
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            rp = self._robots.get(origin, "missing")
        if rp == "missing":
            rp = robotparser.RobotFileParser()
            try:
                # fetch through our session so this has a timeout (stdlib
                # RobotFileParser.read() uses urlopen with no timeout)
                resp = self.session.get(f"{origin}/robots.txt", timeout=min(self.timeout, 10))
                if resp.status_code >= 400:
                    rp = None
                else:
                    rp.parse(resp.text.splitlines())
            except requests.RequestException:  # unreachable -> assume allowed
                rp = None
            with self._lock:
                self._robots[origin] = rp
        if rp is not None and not rp.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}", url=url)

    def _request(self, method, url, **kw) -> requests.Response:
        self._check_robots(url)
        self._throttle_for(urlparse(url).netloc).wait()
        kw.setdefault("timeout", self.timeout)

        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=self.backoff_factor, min=1, max=30),
            retry=retry_if_exception_type((RetryableHttpError, requests.RequestException)),
        )
        def _do():
            try:
                resp = self.session.request(method, url, **kw)
            except requests.RequestException as exc:
                log.warning("%s %s transport error: %s", method, url, exc)
                raise
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableHttpError(
                    f"{resp.status_code} from {url}", status=resp.status_code, url=url, body=resp.text
                )
            if resp.status_code >= 400:
                raise HttpError(
                    f"{resp.status_code} from {url}", status=resp.status_code, url=url, body=resp.text
                )
            return resp

        return _do()

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
