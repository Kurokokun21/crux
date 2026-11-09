"""Async HTTP fetch with caching, retries, and concurrency limiting."""

import asyncio
import random

import httpx

from crux.data import store
from crux.data.config import USER_AGENT

_MAX_ATTEMPTS = 5
_CACHEABLE = {200, 404}
_RETRYABLE = {429} | set(range(500, 600))


async def fetch(client, url, conn, *, accept=None, semaphore=None,
                _sleep=asyncio.sleep):
    """Fetch *url* with caching, retries, and concurrency limiting.

    Returns (status, body).  Caches 200 and 404 only.
    Retries up to 5 times on 429, 5xx, and timeouts with exponential
    backoff + jitter.  Honours Retry-After header.
    """
    # ── cache hit (no semaphore, no network) ─────────────────────
    cached = store.get(conn, url)
    if cached is not None:
        return cached

    if semaphore is None:
        semaphore = asyncio.Semaphore(10)

    headers = {"User-Agent": USER_AGENT}
    if accept is not None:
        headers["Accept"] = accept

    # ── retry loop ───────────────────────────────────────────────
    last_exc = None
    for attempt in range(_MAX_ATTEMPTS):
        # --- make the request under the semaphore ---
        try:
            async with semaphore:
                resp = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                await _sleep(_backoff(attempt))
            continue

        status = resp.status_code

        # --- retryable status ---
        if status in _RETRYABLE:
            last_exc = httpx.HTTPStatusError(
                f"Server error {status}",
                request=resp.request,
                response=resp,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                await _sleep(_retry_delay(resp, attempt))
            continue

        # --- success or non-retryable status ---
        body = resp.content if status == 200 else b""
        if status in _CACHEABLE:
            store.put(conn, url, status, body)
        return status, body

    # all attempts exhausted
    raise last_exc  # type: ignore[misc]


def _backoff(attempt):
    """Exponential backoff with jitter."""
    base = min(2 ** attempt, 60)
    return base + random.uniform(0, base * 0.5)


def _retry_delay(resp, attempt):
    """Use Retry-After header if present, else fall back to backoff."""
    ra = resp.headers.get("Retry-After")
    if ra is not None:
        try:
            return float(ra)
        except ValueError:
            pass
    return _backoff(attempt)
