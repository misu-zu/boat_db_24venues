"""Polite single-worker HTTP client.

- one session, one worker, no parallel requests
- >= 3 s between requests
- explicit User-Agent
- timeouts: connect 5 s / read 15 s
- 403 / 429 raise AccessBlockedError so the caller can stop for the day
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import requests

from . import config
from .timeutil import now_jst

log = logging.getLogger(__name__)


class AccessBlockedError(Exception):
    """Raised on HTTP 403/429 — automatic collection must stop for the day."""

    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")


@dataclass
class FetchResult:
    url: str
    requested_at: datetime
    fetched_at: datetime | None
    http_status: int | None
    elapsed_ms: int | None
    body: bytes | None
    error: str | None     # None on success


class PoliteClient:
    def __init__(self, min_interval_sec: float = config.MIN_REQUEST_INTERVAL_SEC):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = config.USER_AGENT
        self._min_interval = min_interval_sec
        self._last_request_monotonic: float | None = None

    def _throttle(self) -> None:
        if self._last_request_monotonic is None:
            return
        wait = self._min_interval - (time.monotonic() - self._last_request_monotonic)
        if wait > 0:
            time.sleep(wait)

    def fetch(self, url: str) -> FetchResult:
        """One HTTP GET. Raises AccessBlockedError on 403/429."""
        self._throttle()
        requested_at = now_jst()
        self._last_request_monotonic = time.monotonic()
        start = time.monotonic()
        try:
            resp = self._session.get(
                url,
                timeout=(config.CONNECT_TIMEOUT_SEC, config.READ_TIMEOUT_SEC),
            )
        except requests.RequestException as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            log.error("network error for %s: %s", url, exc)
            return FetchResult(
                url=url, requested_at=requested_at, fetched_at=None,
                http_status=None, elapsed_ms=elapsed, body=None,
                error=f"network_error: {type(exc).__name__}: {exc}",
            )
        elapsed = int((time.monotonic() - start) * 1000)
        fetched_at = now_jst()

        if resp.status_code in config.ABORT_DAY_STATUSES:
            log.critical("HTTP %s from %s — aborting day", resp.status_code, url)
            raise AccessBlockedError(resp.status_code, url)

        if resp.status_code != 200:
            return FetchResult(
                url=url, requested_at=requested_at, fetched_at=fetched_at,
                http_status=resp.status_code, elapsed_ms=elapsed,
                body=resp.content, error=f"http_error: status {resp.status_code}",
            )
        return FetchResult(
            url=url, requested_at=requested_at, fetched_at=fetched_at,
            http_status=resp.status_code, elapsed_ms=elapsed,
            body=resp.content, error=None,
        )

    def close(self) -> None:
        self._session.close()
