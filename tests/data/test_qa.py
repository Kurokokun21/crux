"""Acceptance tests for crux.data.qa (offline, temp-file DB)."""

import pytest

from crux.data import store
from crux.data.clean import clean
from crux.data.ingest import _ensure_tables
from crux.data.qa import check
from crux.data.run_ingest import _ensure_projects_table

BASE = "https://files.example/"
META = {
    ("a", "1.0"): "Requires-Dist: b>=1\nRequires-Dist: pytest; extra == 'x'\n",
    ("a", "2.0"): "",
    ("b", "1.0"): "Requires-Python: >=3.8\n",
}


def _make_db(tmp_path):
    path = str(tmp_path / "qa.db")
    conn = store.open_db(path)
    _ensure_tables(conn)
    _ensure_projects_table(conn)
    for (name, ver), extra in META.items():
        fn = f"{name}-{ver}-py3-none-any.whl"
        conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, 's', NULL, NULL)",
                     (name, ver, fn, BASE + fn))
        store.put(conn, BASE + fn + ".metadata", 200,
                  f"Metadata-Version: 2.1\nName: {name}\n{extra}".encode())
    conn.execute("INSERT INTO drops VALUES ('ingest', 'yanked', 'a', '0.1', '')")
    conn.executemany("INSERT INTO projects VALUES (?, 'done', NULL, ?, ?)",
                     [("a", 3, 2), ("b", 1, 1)])
    conn.commit()
    clean(conn)
    return path, conn


def _errors(path):
    return {c for sev, c, _ in check(path) if sev == "error"}


def test_clean_db_has_no_violations(tmp_path):
    path, _ = _make_db(tmp_path)
    assert check(path) == []


@pytest.mark.parametrize("sql, expected", [
    ("UPDATE deps SET dep_name = 'B_x'", "non_canonical"),
    ("UPDATE releases SET name = 'A' WHERE version = '2.0'", "non_canonical"),
    ("UPDATE releases SET version = '1!2.0' WHERE name = 'a' AND version = '2.0'",
     "bad_version"),
    ("UPDATE releases SET version = '2.0+loc' WHERE name = 'a' AND version = '2.0'",
     "bad_version"),
    ("UPDATE releases SET version = 'junk' WHERE name = 'a' AND version = '2.0'",
     "bad_version"),
    ("INSERT INTO deps VALUES ('zz', '9.9', 'b', '')", "orphan_dep"),
    ("UPDATE releases SET requires_python = '<3' WHERE name = 'b'",
     "py_excluded"),
    ("UPDATE projects SET n_seen = 4 WHERE name = 'a'", "conservation_ingest"),
    ("DELETE FROM releases WHERE name = 'a' AND version = '2.0'",
     "conservation_release"),
    ("DELETE FROM deps WHERE name = 'a'", "conservation_lines"),
])
def test_each_violation_detected(tmp_path, sql, expected):
    path, conn = _make_db(tmp_path)
    conn.execute(sql)
    conn.commit()
    assert expected in _errors(path)


def test_duplicate_release_detected(tmp_path):
    path, conn = _make_db(tmp_path)
    # releases has a PK; rebuild without it to simulate a corrupt table
    conn.executescript("""
        CREATE TABLE r2 AS SELECT * FROM releases;
        DROP TABLE releases;
        ALTER TABLE r2 RENAME TO releases;
        INSERT INTO releases SELECT * FROM releases WHERE name = 'b';
    """)
    assert "duplicate" in _errors(path)


def test_self_dependency_is_warning_only(tmp_path):
    path, conn = _make_db(tmp_path)
    conn.execute("UPDATE deps SET dep_name = 'a' WHERE name = 'a'")
    conn.commit()
    out = check(path)
    assert ("warning", "self_dep", "a 1.0") in out
    assert all(sev == "warning" for sev, _, _ in out)
