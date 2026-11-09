"""Acceptance tests for crux.data.clean (offline, in-memory DB)."""

import json

from crux.data import store
from crux.data.clean import clean
from crux.data.ingest import _ensure_tables

BASE = "https://files.example/"

# name, version, metadata text (None = not cached), json body (None = not cached)
RELEASES = [
    # 3 lines: 2 kept, 1 extra_dep
    ("a", "1.0", "Metadata-Version: 2.1\nName: a\nVersion: 1.0\n"
     "Requires-Dist: b>=1\nRequires-Dist: C_D<2\n"
     "Requires-Dist: pytest; extra == \"test\"\n", None),
    # zero dependencies, prerelease
    ("a", "2.0b1", "Metadata-Version: 2.1\nName: a\nVersion: 2.0b1\n", None),
    # 4 lines: 1 kept, marker_false, url_dep, bad_requirement
    ("b", "1.0", "Metadata-Version: 2.1\nName: b\nVersion: 1.0\n"
     "Requires-Python: >=3.8\n"
     "Requires-Dist: six\n"
     "Requires-Dist: pywin32; sys_platform == \"win32\"\n"
     "Requires-Dist: foo @ https://x.example/foo.whl\n"
     "Requires-Dist: !!bad!!\n", None),
    # release-level: py_version (lines must not be counted)
    ("b", "0.9", "Metadata-Version: 2.1\nName: b\nVersion: 0.9\n"
     "Requires-Python: <3\nRequires-Dist: six\n", None),
    # release-level: no Metadata-Version header -> no_metadata
    ("b", "0.8", "Name: b\nVersion: 0.8\n", None),
    # release-level: nothing cached -> no_metadata
    ("c-d", "1.0", None, None),
    # JSON fallback, 1 kept line
    ("c-d", "2.0", None, {"info": {"requires_python": ">=3.7",
                                   "requires_dist": ["six>=1"]}}),
    # JSON fallback, requires_dist null -> zero deps
    ("six", "1.16.0", None, {"info": {"requires_python": None,
                                      "requires_dist": None}}),
]
N_FILES = len(RELEASES)
N_LINES_SEEN = 3 + 0 + 4 + 1 + 0   # lines of releases that pass release checks


def _make_db():
    conn = store.open_db(":memory:")
    _ensure_tables(conn)
    for name, ver, meta, js in RELEASES:
        fn = f"{name}-{ver}-py3-none-any.whl"
        url = BASE + fn
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, ver, fn, url, "abc", "2020-01-01T00:00:00Z", None),
        )
        if meta is not None:
            store.put(conn, url + ".metadata", 200, meta.encode())
        if js is not None:
            store.put(conn, f"https://pypi.org/pypi/{name}/{ver}/json",
                      200, json.dumps(js).encode())
    conn.execute("INSERT INTO drops VALUES ('ingest', 'yanked', 'a', '0.1', '')")
    conn.commit()
    return conn


def _dump(conn):
    return {
        t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(),
                  key=repr)
        for t in ("releases", "deps", "drops")
    }


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def test_running_twice_gives_identical_tables():
    conn = _make_db()
    clean(conn)
    first = _dump(conn)
    clean(conn)
    assert _dump(conn) == first
    # ingest drops are kept
    assert ("ingest", "yanked", "a", "0.1", "") in first["drops"]


def test_release_conservation():
    conn = _make_db()
    clean(conn)
    n_rel = _count(conn, "SELECT COUNT(*) FROM releases")
    n_drop = _count(conn, "SELECT COUNT(*) FROM drops WHERE stage = 'clean'")
    assert n_rel + n_drop == N_FILES
    reasons = sorted(r for (r,) in conn.execute(
        "SELECT reason FROM drops WHERE stage = 'clean'"))
    assert reasons == ["no_metadata", "no_metadata", "py_version"]


def test_line_conservation():
    conn = _make_db()
    clean(conn)
    n_deps = _count(conn, "SELECT COUNT(*) FROM deps")
    n_edge = _count(conn, "SELECT COUNT(*) FROM drops WHERE stage = 'edge'")
    assert n_deps + n_edge == N_LINES_SEEN
    reasons = sorted(r for (r,) in conn.execute(
        "SELECT reason FROM drops WHERE stage = 'edge'"))
    assert reasons == ["bad_requirement", "extra_dep", "marker_false",
                       "url_dep"]


def test_no_dependency_release_present():
    conn = _make_db()
    clean(conn)
    for name, ver in (("a", "2.0b1"), ("six", "1.16.0")):
        assert _count(conn, "SELECT COUNT(*) FROM releases"
                      " WHERE name = ? AND version = ?", (name, ver)) == 1
        assert _count(conn, "SELECT COUNT(*) FROM deps"
                      " WHERE name = ? AND version = ?", (name, ver)) == 0


def test_release_and_dep_values():
    conn = _make_db()
    clean(conn)
    rel = conn.execute(
        "SELECT name, version, upload_time, is_prerelease, requires_python,"
        " filename, url, sha256 FROM releases WHERE name='a' AND version='2.0b1'"
    ).fetchone()
    assert rel == ("a", "2.0b1", "2020-01-01T00:00:00Z", 1, None,
                   "a-2.0b1-py3-none-any.whl",
                   BASE + "a-2.0b1-py3-none-any.whl", "abc")
    deps = sorted(conn.execute(
        "SELECT dep_name, specifier FROM deps WHERE name='a' AND version='1.0'"
    ).fetchall())
    assert deps == [("b", ">=1"), ("c-d", "<2")]
    assert conn.execute(
        "SELECT requires_python FROM releases WHERE name='c-d'").fetchone() \
        == (">=3.7",)
