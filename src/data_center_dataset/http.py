"""Polite, cached HTTP access.

All network egress in this project funnels through :class:`CachedClient` so
that rate limiting, retry behaviour, and the identifying User-Agent are applied
uniformly. Responses are cached on disk keyed by request, which makes repeated
pipeline runs cheap and keeps us from hammering public endpoints.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import DATA_DIR, USER_AGENT

log = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / ".httpcache"

_RETRYABLE = (
    httpx.TransportError,
    httpx.HTTPStatusError,
    httpx.RemoteProtocolError,
)


def _key(method: str, url: str, payload: Any) -> str:
    blob = json.dumps(
        {"m": method.upper(), "u": url, "p": payload}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class CachedClient:
    """Disk-cached HTTP client with rate limiting and exponential-backoff retry.

    Parameters
    ----------
    min_interval:
        Minimum seconds between outbound requests from this client instance.
        Overpass in particular is a shared volunteer resource; be gentle.
    """

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        timeout: float = 180.0,
        cache_dir: Path | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            },
        )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CachedClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=45),
        reraise=True,
    )
    def _send(
        self, method: str, url: str, *, data: Any = None, params: Any = None
    ) -> httpx.Response:
        self._throttle()
        log.debug("%s %s", method, url)
        resp = self._client.request(method, url, data=data, params=params)
        # 4xx other than 429 will not succeed on retry; surface immediately.
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    # -- public API --------------------------------------------------------

    def fetch_bytes(
        self,
        url: str,
        *,
        method: str = "GET",
        data: Any = None,
        params: Any = None,
        refresh: bool = False,
        suffix: str = ".bin",
    ) -> bytes:
        """Return response body, using the on-disk cache when possible."""
        path = self.cache_dir / (_key(method, url, data or params) + suffix)
        if path.exists() and not refresh:
            return path.read_bytes()

        resp = self._send(method, url, data=data, params=params)
        resp.raise_for_status()
        body = resp.content
        path.write_bytes(body)
        return body

    def fetch_text(self, url: str, **kw: Any) -> str:
        kw.setdefault("suffix", ".txt")
        return self.fetch_bytes(url, **kw).decode("utf-8", errors="replace")

    def fetch_json(self, url: str, **kw: Any) -> Any:
        kw.setdefault("suffix", ".json")
        return json.loads(self.fetch_bytes(url, **kw))

    def download(self, url: str, dest: Path, *, refresh: bool = False) -> Path:
        """Stream a (potentially large) file to ``dest``, skipping if present."""
        if dest.exists() and dest.stat().st_size > 0 and not refresh:
            log.info("already downloaded: %s", dest.name)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        self._throttle()
        with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
        tmp.replace(dest)
        return dest
