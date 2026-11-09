"""SQLite cache for raw HTTP responses. Body is zlib-compressed."""

import sqlite3
import zlib
from datetime import datetime, timezone

_CREATE = """\
CREATE TABLE IF NOT EXISTS raw (
    url        TEXT PRIMARY KEY,
    status     INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    body       BLOB NOT NULL
)
"""


def open_db(path):
    """Open (or create) the cache database.  Returns a connection."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE)
    conn.commit()
    return conn


def get(conn, url):
    """Return (status, body) or None.  Body is decompressed."""
    row = conn.execute(
        "SELECT status, body FROM raw WHERE url = ?", (url,)
    ).fetchone()
    if row is None:
        return None
    status, compressed = row
    return status, zlib.decompress(compressed)


def put(conn, url, status, body):
    """Insert or replace a cached response.  Body is compressed."""
    compressed = zlib.compress(body)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO raw (url, status, fetched_at, body)"
        " VALUES (?, ?, ?, ?)",
        (url, status, now, compressed),
    )
    conn.commit()
