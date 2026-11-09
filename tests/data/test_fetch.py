"""Acceptance tests for crux.data.store and crux.data.fetch."""

import asyncio

import httpx
import pytest

from crux.data import store
from crux.data.fetch import fetch


# ── helpers ──────────────────────────────────────────────────────────

async def _noop_sleep(_delay):
    """Instant sleep for fast tests."""
    pass


def _make_conn():
    return store.open_db(":memory:")


# ── 1. Cache hit: second call makes zero HTTP requests ───────────────

def test_cache_hit_zero_http_on_second_call():
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=b"hello")

    async def _run():
        nonlocal call_count
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            r1 = await fetch(client, "http://test/a", conn, _sleep=_noop_sleep)
            assert r1 == (200, b"hello")
            assert call_count == 1

            r2 = await fetch(client, "http://test/a", conn, _sleep=_noop_sleep)
            assert r2 == (200, b"hello")
            assert call_count == 1  # no new HTTP request

    asyncio.run(_run())


# ── 2. 429 then 200: returns and caches the 200 ─────────────────────

def test_429_then_200_caches_200():
    responses = iter([
        httpx.Response(429),
        httpx.Response(200, content=b"ok"),
    ])

    def handler(request):
        return next(responses)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            status, body = await fetch(
                client, "http://test/b", conn, _sleep=_noop_sleep,
            )
            assert status == 200
            assert body == b"ok"
            # verify cached
            cached = store.get(conn, "http://test/b")
            assert cached == (200, b"ok")

    asyncio.run(_run())


# ── 3. 404 is cached and returns (404, b"") ──────────────────────────

def test_404_cached_returns_empty_body():
    def handler(request):
        return httpx.Response(404, content=b"not found page")

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            status, body = await fetch(
                client, "http://test/c", conn, _sleep=_noop_sleep,
            )
            assert status == 404
            assert body == b""
            # verify cached
            cached = store.get(conn, "http://test/c")
            assert cached == (404, b"")

    asyncio.run(_run())


# ── 4. Five 500s raise and cache nothing ─────────────────────────────

def test_five_500s_raise_no_cache():
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(500)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            with pytest.raises(httpx.HTTPStatusError):
                await fetch(
                    client, "http://test/d", conn, _sleep=_noop_sleep,
                )
            assert call_count == 5
            assert store.get(conn, "http://test/d") is None

    asyncio.run(_run())


# ── 5. Retry-After is honoured ───────────────────────────────────────

def test_retry_after_honoured():
    sleep_durations = []

    async def tracking_sleep(duration):
        sleep_durations.append(duration)

    responses = iter([
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, content=b"got it"),
    ])

    def handler(request):
        return next(responses)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            status, body = await fetch(
                client, "http://test/e", conn, _sleep=tracking_sleep,
            )
            assert status == 200
            assert len(sleep_durations) == 1
            assert sleep_durations[0] == 7.0

    asyncio.run(_run())


# ── 6. 20 concurrent fetches never exceed the semaphore limit ────────

def test_concurrent_fetches_respect_semaphore():
    max_concurrent = 0
    current = 0

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal max_concurrent, current
            current += 1
            if current > max_concurrent:
                max_concurrent = current
            await asyncio.sleep(0.02)  # hold the slot briefly
            current -= 1
            return httpx.Response(200, content=b"x")

    async def _run():
        transport = SlowTransport()
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            sem = asyncio.Semaphore(10)
            urls = [f"http://test/sem/{i}" for i in range(20)]
            tasks = [
                fetch(client, url, conn, semaphore=sem, _sleep=_noop_sleep)
                for url in urls
            ]
            results = await asyncio.gather(*tasks)
            assert all(s == 200 for s, _ in results)

        assert max_concurrent <= 10

    asyncio.run(_run())


# ── 7. Body round-trips byte-identical through compression ───────────

def test_body_roundtrip_byte_identical():
    conn = _make_conn()
    body = bytes(range(256)) * 10  # every byte value, repeated
    store.put(conn, "http://test/rt", 200, body)
    status, got = store.get(conn, "http://test/rt")
    assert status == 200
    assert got == body
