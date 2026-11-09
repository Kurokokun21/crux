"""Batch ingest with dependency closure and resume.

CLI: python -m crux.data.run_ingest --limit N
"""

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from crux.data import store
from crux.data.config import (
    DB_PATH,
    MARKER_ENV,
    SNAPSHOT_DATE,
    USER_AGENT,
)
from crux.data.fetch import fetch
from crux.data.ingest import ingest_project

TOP_LIST_URL = (
    "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
)
SNAPSHOTS_DIR = Path("snapshots")
_TOP_N = 5000


# ── tables ───────────────────────────────────────────────────────────

def _ensure_projects_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            name        TEXT PRIMARY KEY,
            status      TEXT,
            last_serial INTEGER,
            n_seen      INTEGER,
            n_kept      INTEGER
        );
    """)


# ── top list ─────────────────────────────────────────────────────────

async def _download_top_list(client, conn, *, semaphore=None, _sleep=None):
    """Download the top-packages list and return the rows."""
    import asyncio as _aio
    if _sleep is None:
        _sleep = _aio.sleep
    status, body = await fetch(
        client, TOP_LIST_URL, conn,
        semaphore=semaphore, _sleep=_sleep,
    )
    if status != 200:
        raise RuntimeError(f"top-list fetch failed: {status}")
    return json.loads(body)


def _load_or_save_top_list(names):
    """Save to / load from snapshots/top5000-<date>.json."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"top5000-{SNAPSHOT_DATE}.json"
    if path.exists():
        return json.loads(path.read_text())
    path.write_text(json.dumps(names, indent=2))
    return names


# ── closure: extract dependency names from cached metadata ───────────

def _extract_dep_names(conn, name):
    """Return set of canonical dep names from stored metadata."""
    rows = conn.execute(
        "SELECT version, url FROM files WHERE name = ?", (name,)
    ).fetchall()

    dep_names = set()
    for version, file_url in rows:
        reqs = _reqs_from_pep658(conn, file_url)
        if reqs is None:
            reqs = _reqs_from_json(conn, name, version)
        if reqs is None:
            continue
        for req_str in reqs:
            try:
                req = Requirement(req_str)
            except InvalidRequirement:
                continue
            if req.url:
                continue
            if req.marker is not None:
                if not req.marker.evaluate(MARKER_ENV):
                    continue
            dep_names.add(canonicalize_name(req.name))
    return dep_names


def _reqs_from_pep658(conn, file_url):
    """Parse Requires-Dist lines from cached PEP 658 .metadata."""
    meta_url = file_url + ".metadata"
    cached = store.get(conn, meta_url)
    if cached is None or cached[0] != 200:
        return None
    text = cached[1].decode("utf-8", errors="replace")
    reqs = []
    for line in text.splitlines():
        if line.startswith("Requires-Dist:"):
            reqs.append(line[len("Requires-Dist:"):].strip())
    return reqs if reqs or "Requires-Dist" not in text else reqs


def _reqs_from_json(conn, name, version):
    """Parse requires_dist from cached release JSON."""
    json_url = f"https://pypi.org/pypi/{name}/{version}/json"
    cached = store.get(conn, json_url)
    if cached is None or cached[0] != 200:
        return None
    try:
        data = json.loads(cached[1])
        rd = data.get("info", {}).get("requires_dist")
        return rd if rd is not None else []
    except (json.JSONDecodeError, KeyError):
        return None


# ── progress ─────────────────────────────────────────────────────────

class _Stats:
    def __init__(self):
        self.done = 0
        self.errors = 0
        self.not_found = 0
        self.t0 = time.monotonic()

    def report(self, queue_size):
        elapsed = time.monotonic() - self.t0
        print(
            f"[progress] done={self.done}  queue={queue_size}  "
            f"not_found={self.not_found}  errors={self.errors}  "
            f"elapsed={elapsed:.1f}s"
        )


# ── main ─────────────────────────────────────────────────────────────

async def run(*, limit=None, conn=None, client=None, semaphore=None,
              _sleep=None, _fetch_top_list=None, _ingest=None):
    """Run batch ingest with closure.

    All deps are injectable for testing.
    """
    import asyncio as _aio
    if _sleep is None:
        _sleep = _aio.sleep
    if _ingest is None:
        _ingest = ingest_project

    own_conn = conn is None
    if own_conn:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        conn = store.open_db(DB_PATH)

    _ensure_projects_table(conn)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30)

    if semaphore is None:
        semaphore = _aio.Semaphore(10)

    try:
        # ── seed list ────────────────────────────────────────────
        if _fetch_top_list is not None:
            names = _fetch_top_list()
        else:
            data = await _download_top_list(
                client, conn, semaphore=semaphore, _sleep=_sleep,
            )
            raw = [r["project"] for r in data["rows"]][:_TOP_N]
            names = [canonicalize_name(n) for n in raw]
            names = _load_or_save_top_list(names)

        if limit is not None:
            seeds = names[:limit]
        else:
            seeds = names

        # ── queue ────────────────────────────────────────────────
        seen = set()
        queue = deque()

        # skip already-done projects
        done_rows = conn.execute(
            "SELECT name FROM projects WHERE status = 'done'"
        ).fetchall()
        done_set = {r[0] for r in done_rows}

        for n in seeds:
            if n not in seen:
                seen.add(n)
                if n not in done_set:
                    queue.append(n)
                else:
                    # still need to discover deps of done projects
                    deps = _extract_dep_names(conn, n)
                    for d in deps:
                        if d not in seen:
                            seen.add(d)
                            if d not in done_set:
                                queue.append(d)

        stats = _Stats()

        # ── process loop ─────────────────────────────────────────
        while queue:
            name = queue.popleft()

            # might have been done via closure from another path
            existing = conn.execute(
                "SELECT status FROM projects WHERE name = ?", (name,)
            ).fetchone()
            if existing and existing[0] == "done":
                continue

            try:
                result = await _ingest(
                    client, name, conn,
                    semaphore=semaphore, _sleep=_sleep,
                )

                # detect 404
                cached = store.get(conn, f"https://pypi.org/simple/{name}/")
                if cached and cached[0] == 404:
                    status = "not_found"
                    stats.not_found += 1
                else:
                    status = "done"
                    stats.done += 1

                conn.execute(
                    "INSERT OR REPLACE INTO projects"
                    " (name, status, n_seen, n_kept)"
                    " VALUES (?, ?, ?, ?)",
                    (name, status, result["n_seen"], result["n_kept"]),
                )
                conn.commit()

                # closure: discover dep names
                if status == "done":
                    deps = _extract_dep_names(conn, name)
                    for d in deps:
                        if d not in seen:
                            seen.add(d)
                            queue.append(d)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                stats.errors += 1
                conn.execute(
                    "INSERT OR REPLACE INTO projects"
                    " (name, status, n_seen, n_kept)"
                    " VALUES (?, ?, ?, ?)",
                    (name, "error", 0, 0),
                )
                conn.commit()

            if (stats.done + stats.errors + stats.not_found) % 100 == 0:
                stats.report(len(queue))

        stats.report(0)
        return {
            "done": stats.done,
            "not_found": stats.not_found,
            "errors": stats.errors,
        }

    except KeyboardInterrupt:
        print("\nInterrupted — progress saved, safe to resume.")
        conn.commit()
    finally:
        if own_client:
            await client.aclose()
        if own_conn:
            conn.close()


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch-ingest PyPI metadata with closure.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="process only the first N top-list names (closure still runs)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(limit=args.limit))
    except KeyboardInterrupt:
        pass
