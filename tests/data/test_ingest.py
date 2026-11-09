"""Acceptance tests for crux.data.ingest."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from packaging.utils import parse_wheel_filename

from crux.data import store
from crux.data.ingest import ingest_project, pick_file

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ── helpers ──────────────────────────────────────────────────────────

async def _noop_sleep(_):
    pass


def _make_conn():
    return store.open_db(":memory:")


def _load_responses(*projects):
    """Build URL -> (status, body) from fixture dirs."""
    responses = {}
    for name in projects:
        fdir = FIXTURES / name
        simple = json.loads((fdir / "simple.json").read_text())
        responses[f"https://pypi.org/simple/{name}/"] = (
            200, json.dumps(simple).encode(),
        )
        for f in simple["files"]:
            if not f["filename"].endswith(".whl"):
                continue
            if not f.get("core-metadata") and \
               not f.get("data-dist-info-metadata"):
                continue
            try:
                _, ver, _, _ = parse_wheel_filename(f["filename"])
            except Exception:
                continue
            meta_path = fdir / f"{ver}.metadata"
            if meta_path.exists():
                responses[f["url"] + ".metadata"] = (
                    200, meta_path.read_bytes(),
                )
    return responses


def _make_handler(responses):
    call_count = [0]

    def handler(request):
        call_count[0] += 1
        url = str(request.url)
        if url in responses:
            s, b = responses[url]
            return httpx.Response(s, content=b)
        return httpx.Response(404)

    return handler, call_count


# ── 1. pick_file: table-driven ──────────────────────────────────────

@pytest.mark.parametrize("filenames, expected", [
    # sdist only
    (["pkg-1.0.0.tar.gz"], None),
    # manylinux2014
    (["pkg-1.0.0-cp312-cp312-manylinux2014_x86_64.whl"],
     "pkg-1.0.0-cp312-cp312-manylinux2014_x86_64.whl"),
    # macOS-only
    (["pkg-1.0.0-cp312-cp312-macosx_11_0_arm64.whl"], None),
    # py2.py3-none-any
    (["pkg-1.0.0-py2.py3-none-any.whl"],
     "pkg-1.0.0-py2.py3-none-any.whl"),
    # abi3 (built with cp39, stable ABI → works on 3.12)
    (["pkg-1.0.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"],
     "pkg-1.0.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
    # multiple candidates → py3-none-any preferred
    ([
        "pkg-1.0.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "pkg-1.0.0-py3-none-any.whl",
        "pkg-1.0.0.tar.gz",
    ], "pkg-1.0.0-py3-none-any.whl"),
    # windows-only wheel
    (["pkg-1.0.0-cp312-cp312-win_amd64.whl"], None),
], ids=[
    "sdist_only", "manylinux2014", "macos_only",
    "py2_py3_none_any", "abi3", "multiple_candidates",
    "windows_only",
])
def test_pick_file(filenames, expected):
    files = [{"filename": fn} for fn in filenames]
    result = pick_file(files)
    if expected is None:
        assert result is None
    else:
        assert result["filename"] == expected


# ── 2. Every reason code appears at least once ──────────────────────

def test_reason_codes_all_present():
    from crux.data import config
    expected_codes = {
        config.INVALID_VERSION, config.EPOCH, config.LOCAL_VERSION,
        config.YANKED, config.AFTER_CUTOFF, config.PY_VERSION,
        config.NO_WHEEL, config.NO_METADATA,
    }

    responses = _load_responses("test-alpha")
    handler, _ = _make_handler(responses)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            await ingest_project(
                client, "test-alpha", conn, _sleep=_noop_sleep,
            )
            rows = conn.execute(
                "SELECT reason, COUNT(*) FROM drops"
                " WHERE name = 'test-alpha' AND stage = 'ingest'"
                " GROUP BY reason"
            ).fetchall()
            found = {r: c for r, c in rows}
            for code in expected_codes:
                assert code in found, f"missing reason code: {code}"
                assert found[code] >= 1

    asyncio.run(_run())


# ── 3. Conservation: versions_seen == kept + drops ───────────────────

def test_conservation():
    responses = _load_responses("test-alpha")
    handler, _ = _make_handler(responses)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            result = await ingest_project(
                client, "test-alpha", conn, _sleep=_noop_sleep,
            )
            n_seen = result["n_seen"]
            n_kept = conn.execute(
                "SELECT COUNT(*) FROM files WHERE name = 'test-alpha'"
            ).fetchone()[0]
            n_dropped = conn.execute(
                "SELECT COUNT(*) FROM drops"
                " WHERE name = 'test-alpha' AND stage = 'ingest'"
            ).fetchone()[0]
            assert n_seen == n_kept + n_dropped, (
                f"conservation: {n_seen} != {n_kept} + {n_dropped}"
            )

    asyncio.run(_run())


# ── 4. Second run makes zero HTTP requests ───────────────────────────

def test_second_run_zero_http():
    responses = _load_responses("test-beta")
    handler, call_count = _make_handler(responses)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            await ingest_project(
                client, "test-beta", conn, _sleep=_noop_sleep,
            )
            after_first = call_count[0]
            assert after_first > 0

            await ingest_project(
                client, "test-beta", conn, _sleep=_noop_sleep,
            )
            assert call_count[0] == after_first, (
                "second run should make zero HTTP requests"
            )

    asyncio.run(_run())


# ── 5. Metadata fetches run concurrently ─────────────────────────────

def test_metadata_fetched_concurrently():
    responses = _load_responses("test-alpha")
    state = {"in_flight": 0, "max": 0}

    async def handler(request):
        url = str(request.url)
        state["in_flight"] += 1
        state["max"] = max(state["max"], state["in_flight"])
        if url.endswith(".metadata"):
            await asyncio.sleep(0.01)
        state["in_flight"] -= 1
        if url in responses:
            s, b = responses[url]
            return httpx.Response(s, content=b)
        return httpx.Response(404)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            conn = _make_conn()
            result = await ingest_project(
                client, "test-alpha", conn, _sleep=_noop_sleep,
            )
            assert result["n_kept"] == 3
        assert state["max"] >= 2, "metadata fetches ran sequentially"

    asyncio.run(_run())
