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
    Retrying,
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
    max_attempts:
        How many times a single request is attempted before the error is
        raised to the caller. Kept configurable because the Overpass mirror
        list wants to fail over to the next endpoint quickly rather than
        burn the full backoff budget on one saturated server.
    """

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        timeout: float = 180.0,
        cache_dir: Path | None = None,
        max_attempts: int = 4,
        max_retry_after: float = 120.0,
    ) -> None:
        self.min_interval = min_interval
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.max_retry_after = max_retry_after
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

    def _send_once(
        self, method: str, url: str, *, data: Any = None, params: Any = None
    ) -> httpx.Response:
        self._throttle()
        log.debug("%s %s", method, url)
        resp = self._client.request(method, url, data=data, params=params)
        # 4xx other than 429 will not succeed on retry; surface immediately.
        if resp.status_code == 429 or resp.status_code >= 500:
            if resp.status_code == 429:
                self._respect_retry_after(resp)
            resp.raise_for_status()
        return resp

    def _respect_retry_after(self, resp: httpx.Response) -> None:
        """Sleep for the server's ``Retry-After``, bounded by a local cap.

        PeeringDB answers a throttled request with a ``retry-after`` of up to
        an hour. Honouring that literally would stall a background build for
        longer than the run is worth, so the wait is capped; the request is
        retried anyway and the cap simply bounds the damage.
        """
        raw = resp.headers.get("retry-after")
        if not raw:
            return
        try:
            seconds = float(raw)
        except ValueError:
            return
        seconds = max(0.0, min(seconds, self.max_retry_after))
        if seconds <= 0:
            return
        log.warning(
            "throttled by %s; waiting %.0fs before retrying", resp.url.host, seconds
        )
        time.sleep(seconds)

    def _send(
        self, method: str, url: str, *, data: Any = None, params: Any = None
    ) -> httpx.Response:
        """Send with retry/backoff. The attempt budget is per-instance."""
        retrying = Retrying(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=2, min=2, max=45),
            reraise=True,
        )
        return retrying(self._send_once, method, url, data=data, params=params)

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

    def download(
        self,
        url: str,
        dest: Path,
        *,
        refresh: bool = False,
        resume: bool = True,
        max_attempts: int = 6,
    ) -> Path:
        """Stream a (potentially large) file to ``dest``, skipping if present.

        Supports resuming an interrupted transfer: the partial file is kept as
        ``<dest>.part`` and a ``Range`` request continues from where it
        stopped. The EPA NEI archive is ~157 MB and the host is slow enough
        that a single request can time out part-way through, so the transfer
        retries and resumes rather than starting over. If the server ignores
        the range header (or the partial file is longer than the remote copy)
        the transfer restarts from byte zero.
        """
        if dest.exists() and dest.stat().st_size > 0 and not refresh:
            log.info("already downloaded: %s", dest.name)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")

        for attempt in range(1, max_attempts + 1):
            offset = tmp.stat().st_size if (resume and not refresh and tmp.exists()) else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                self._throttle()
                # A 157 MB file from a slow government host needs a longer
                # read timeout than the default used for API calls.
                timeout = httpx.Timeout(600.0, connect=30.0, write=600.0, pool=30.0)
                with self._client.stream("GET", url, headers=headers, timeout=timeout) as resp:
                    resp.raise_for_status()
                    resumed = resp.status_code == 206 and offset > 0
                    if offset and not resumed:
                        # Server did not honour the range request: start over.
                        log.info("range request not honoured, restarting download")
                        offset = 0
                    mode = "ab" if resumed else "wb"
                    if resumed:
                        log.info(
                            "resuming %s at %.1f MB", dest.name, offset / 1e6
                        )
                    with tmp.open(mode) as fh:
                        for chunk in resp.iter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
                tmp.replace(dest)
                return dest
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                have = tmp.stat().st_size if tmp.exists() else 0
                if attempt >= max_attempts:
                    raise
                wait = min(2**attempt, 45)
                log.warning(
                    "download of %s interrupted (%s); %d bytes on disk, "
                    "resuming in %ds",
                    dest.name,
                    exc,
                    have,
                    wait,
                )
                time.sleep(wait)

        return dest
