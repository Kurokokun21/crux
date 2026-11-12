"""Clean: cached raw metadata -> releases / deps tables.

CLI: python -m crux.data.clean
"""

import json

from packaging.version import Version

from crux.data import store
from crux.data.config import BAD_REQUIREMENT, DB_PATH, NO_METADATA, PY_VERSION
from crux.data.parse import parse_dep, parse_metadata, python_ok


def _ensure_tables(conn):
    conn.executescript("""
        DROP TABLE IF EXISTS releases;
        DROP TABLE IF EXISTS deps;
        CREATE TABLE releases (
            name            TEXT NOT NULL,
            version         TEXT NOT NULL,
            upload_time     TEXT,
            is_prerelease   INTEGER NOT NULL,
            requires_python TEXT,
            filename        TEXT NOT NULL,
            url             TEXT NOT NULL,
            sha256          TEXT NOT NULL,
            PRIMARY KEY (name, version)
        );
        CREATE TABLE deps (
            name      TEXT NOT NULL,
            version   TEXT NOT NULL,
            dep_name  TEXT NOT NULL,
            specifier TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS drops (
            stage   TEXT NOT NULL,
            reason  TEXT NOT NULL,
            name    TEXT NOT NULL,
            version TEXT NOT NULL,
            detail  TEXT
        );
        DELETE FROM drops WHERE stage IN ('clean', 'edge');

        -- Indexes. `deps` has no primary key by design (one row per kept
        -- Requires-Dist line), and the QA conservation checks look rows up by
        -- (name, version), so without these the correlated subqueries in qa.py
        -- scan the whole table once per release -- quadratic in database size.
        -- deps(dep_name) is for the solver's reverse lookups.
        CREATE INDEX IF NOT EXISTS idx_deps_name_version ON deps (name, version);
        CREATE INDEX IF NOT EXISTS idx_deps_dep_name     ON deps (dep_name);
        CREATE INDEX IF NOT EXISTS idx_drops_lookup      ON drops (stage, name, version);
    """)


def _load_metadata(conn, name, version, file_url):
    """Return (requires_python, [lines]); raise ValueError if unavailable."""
    cached = store.get(conn, file_url + ".metadata")
    if cached is not None and cached[0] == 200:
        return parse_metadata(cached[1].decode("utf-8", errors="replace"))
    cached = store.get(conn, f"https://pypi.org/pypi/{name}/{version}/json")
    if cached is not None and cached[0] == 200:
        info = json.loads(cached[1])["info"]
        return info.get("requires_python"), info.get("requires_dist") or []
    raise ValueError("no cached metadata")


def clean(conn):
    """Rebuild releases + deps from files + raw.  Returns drop counts."""
    _ensure_tables(conn)
    files = conn.execute(
        "SELECT name, version, filename, url, sha256, upload_time"
        " FROM files ORDER BY name, version"
    ).fetchall()

    for name, version, filename, url, sha256, upload_time in files:
        try:
            rp, lines = _load_metadata(conn, name, version, url)
        except (ValueError, KeyError, TypeError) as e:
            _drop(conn, "clean", NO_METADATA, name, version, str(e))
            continue

        ok = python_ok(rp)
        if ok is not True:
            reason = PY_VERSION if ok is False else BAD_REQUIREMENT
            _drop(conn, "clean", reason, name, version, rp)
            continue

        conn.execute(
            "INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, version, upload_time, int(Version(version).is_prerelease),
             rp, filename, url, sha256),
        )
        for line in lines:
            res = parse_dep(line)
            if res[0] == "ok":
                conn.execute("INSERT INTO deps VALUES (?, ?, ?, ?)",
                             (name, version, res[1], str(res[2])))
            else:
                _drop(conn, "edge", res[1], name, version, line)

    conn.commit()
    return conn.execute(
        "SELECT stage, reason, COUNT(*) FROM drops"
        " GROUP BY stage, reason ORDER BY stage, reason"
    ).fetchall()


def _drop(conn, stage, reason, name, version, detail):
    conn.execute("INSERT INTO drops VALUES (?, ?, ?, ?, ?)",
                 (stage, reason, name, version, detail))


if __name__ == "__main__":
    conn = store.open_db(DB_PATH)
    counts = clean(conn)
    for table in ("files", "releases", "deps"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:10s} {n}")
    for stage, reason, n in counts:
        print(f"{stage:8s} {reason:16s} {n}")
