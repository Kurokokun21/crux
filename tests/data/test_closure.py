"""Acceptance tests for batch ingest closure and resume."""

import asyncio
import json

import pytest

from crux.data import store
from crux.data.config import MARKER_ENV
from crux.data.ingest import _ensure_tables
from crux.data.run_ingest import run, _ensure_projects_table, _extract_dep_names


# ── helpers ──────────────────────────────────────────────────────────

async def _noop_sleep(_):
    pass


def _make_conn():
    conn = store.open_db(":memory:")
    _ensure_tables(conn)
    _ensure_projects_table(conn)
    return conn


def _populate_project(conn, name, deps, *, status_code=200):
    """Seed a project's files-table row and its PEP 658 metadata in cache.

    *deps* is a list of requirement strings (e.g. ["requests>=2", "six"]).
    If *status_code* is 404, simulate a missing project.
    """
    simple_url = f"https://pypi.org/simple/{name}/"

    if status_code == 404:
        store.put(conn, simple_url, 404, b"")
        return

    version = "1.0.0"
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    file_url = f"https://files.example.com/{filename}"

    # files table
    conn.execute(
        "INSERT OR REPLACE INTO files"
        " (name, version, filename, url, sha256, upload_time, requires_python)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, version, filename, file_url, "fake_sha", "2024-01-01T00:00:00Z", ">=3.8"),
    )

    # PEP 658 metadata in raw cache
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        "Requires-Python: >=3.8",
    ]
    for d in deps:
        lines.append(f"Requires-Dist: {d}")
    meta_body = "\n".join(lines).encode()
    meta_url = file_url + ".metadata"
    store.put(conn, meta_url, 200, meta_body)

    # Simple API in cache (so second run doesn't re-fetch)
    simple_data = {
        "meta": {"api-version": "1.1"},
        "name": name,
        "versions": [version],
        "files": [{
            "filename": filename,
            "url": file_url,
            "hashes": {"sha256": "fake_sha"},
            "requires-python": ">=3.8",
            "yanked": False,
            "core-metadata": True,
            "data-dist-info-metadata": True,
            "upload-time": "2024-01-01T00:00:00Z",
        }],
    }
    store.put(conn, simple_url, 200, json.dumps(simple_data).encode())
    conn.commit()


def _make_mock_ingest(graph, call_log):
    """Return a fake ingest_project that uses *graph* to populate tables.

    graph = {"a": ["b", "c"], "b": [], ...}
    call_log is a list — each call appends the project name.
    """
    async def _mock_ingest(client, name, conn, *, semaphore=None,
                           _sleep=None):
        call_log.append(name)
        if name in graph:
            _populate_project(conn, name, graph[name])
            return {"name": name, "n_seen": 1, "n_kept": 1}
        else:
            # simulate 404
            _populate_project(conn, name, [], status_code=404)
            return {"name": name, "n_seen": 0, "n_kept": 0}

    return _mock_ingest


# ── 1. Closure on a 5-node graph with a cycle ────────────────────────

def test_closure_cycle_terminates():
    """A→B→C→D→E→A: all 5 visited exactly once, no infinite loop."""
    graph = {
        "a": ["b"],
        "b": ["c"],
        "c": ["d"],
        "d": ["e"],
        "e": ["a"],  # cycle back
    }
    call_log = []

    async def _run():
        conn = _make_conn()
        mock = _make_mock_ingest(graph, call_log)
        await run(
            limit=1,
            conn=conn,
            client=None,
            _sleep=_noop_sleep,
            _fetch_top_list=lambda: ["a"],
            _ingest=mock,
        )
        # all 5 nodes visited
        projects = conn.execute(
            "SELECT name, status FROM projects ORDER BY name"
        ).fetchall()
        names = {r[0] for r in projects}
        assert names == {"a", "b", "c", "d", "e"}, f"got {names}"
        # each visited exactly once
        assert sorted(call_log) == ["a", "b", "c", "d", "e"]
        # all done
        for name, status in projects:
            assert status == "done", f"{name} has status {status}"

    asyncio.run(_run())


# ── 2. Resume after simulated crash repeats no work ──────────────────

def test_resume_skips_done():
    """Pre-mark 3 of 5 nodes as done.  Only the remaining 2 are ingested."""
    graph = {
        "a": ["b", "c"],
        "b": ["d"],
        "c": ["e"],
        "d": [],
        "e": [],
    }
    call_log = []

    async def _run():
        conn = _make_conn()

        # simulate "previous run" — mark a, b, d as done
        for name in ("a", "b", "d"):
            _populate_project(conn, name, graph[name])
            conn.execute(
                "INSERT OR REPLACE INTO projects"
                " (name, status, n_seen, n_kept) VALUES (?, 'done', 1, 1)",
                (name,),
            )
        conn.commit()

        mock = _make_mock_ingest(graph, call_log)
        await run(
            conn=conn,
            client=None,
            _sleep=_noop_sleep,
            _fetch_top_list=lambda: ["a"],
            _ingest=mock,
        )
        # only c and e should have been ingested
        assert sorted(call_log) == ["c", "e"], f"got {call_log}"

        # all 5 now done
        all_done = conn.execute(
            "SELECT COUNT(*) FROM projects WHERE status = 'done'"
        ).fetchone()[0]
        assert all_done == 5

    asyncio.run(_run())


# ── 3. 404 project recorded as not_found, run continues ─────────────

def test_404_recorded_as_not_found():
    graph = {
        "a": ["missing", "c"],
        "c": [],
        # "missing" not in graph → mock returns 404
    }
    call_log = []

    async def _run():
        conn = _make_conn()
        mock = _make_mock_ingest(graph, call_log)
        result = await run(
            conn=conn,
            client=None,
            _sleep=_noop_sleep,
            _fetch_top_list=lambda: ["a"],
            _ingest=mock,
        )
        # missing is not_found
        status = conn.execute(
            "SELECT status FROM projects WHERE name = 'missing'"
        ).fetchone()[0]
        assert status == "not_found"

        # c still processed
        status_c = conn.execute(
            "SELECT status FROM projects WHERE name = 'c'"
        ).fetchone()[0]
        assert status_c == "done"

        # overall stats
        assert result["not_found"] == 1
        assert result["done"] >= 2  # a and c

    asyncio.run(_run())


# ── 4. Extras-only dependency is NOT followed ────────────────────────

def test_extras_dep_not_followed():
    """A dep guarded by extra == "dev" should not be followed."""
    graph = {
        "a": ['real-dep', 'optional-dep; extra == "dev"'],
        "real-dep": [],
    }
    call_log = []

    async def _run():
        conn = _make_conn()
        mock = _make_mock_ingest(graph, call_log)
        await run(
            conn=conn,
            client=None,
            _sleep=_noop_sleep,
            _fetch_top_list=lambda: ["a"],
            _ingest=mock,
        )
        all_names = {r[0] for r in conn.execute(
            "SELECT name FROM projects"
        ).fetchall()}
        assert "real-dep" in all_names
        assert "optional-dep" not in all_names, (
            "extras-only dep should not be followed"
        )

    asyncio.run(_run())


# ── 5. A failing project records why it failed ───────────────────────

def test_error_detail_is_recorded():
    """status='error' is useless without the reason; store it."""

    async def _boom(client, name, conn, **kw):
        raise RuntimeError("simulated metadata explosion")

    async def _run():
        conn = _make_conn()
        result = await run(
            conn=conn,
            client=None,
            _sleep=_noop_sleep,
            _fetch_top_list=lambda: ["a"],
            _ingest=_boom,
        )
        status, detail = conn.execute(
            "SELECT status, detail FROM projects WHERE name = 'a'"
        ).fetchone()
        assert status == "error"
        assert "RuntimeError" in detail
        assert "simulated metadata explosion" in detail
        assert result["errors"] == 1

    asyncio.run(_run())


def test_detail_column_added_to_preexisting_db():
    """A database created before `detail` existed is migrated, not broken."""
    conn = store.open_db(":memory:")
    conn.executescript("""
        CREATE TABLE projects (
            name        TEXT PRIMARY KEY,
            status      TEXT,
            last_serial INTEGER,
            n_seen      INTEGER,
            n_kept      INTEGER
        );
    """)
    conn.execute("INSERT INTO projects VALUES ('old', 'done', NULL, 1, 1)")
    conn.commit()
    _ensure_projects_table(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    assert "detail" in cols
    # the existing row survives
    assert conn.execute(
        "SELECT status FROM projects WHERE name = 'old'"
    ).fetchone()[0] == "done"
