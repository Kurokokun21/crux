"""Acceptance tests for crux.data.graph (offline)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from crux.data import store
from crux.data.clean import _ensure_tables as _ensure_clean_tables
from crux.data.graph import Graph

TOY = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "toy_abc.json").read_text())


def _v(*s):
    return [Version(x) for x in s]


def test_toy_versions_newest_first():
    g = Graph.from_dict(TOY)
    assert g.versions("a") == _v("1.4", "1.3", "1.2", "1.1", "1.0")
    assert g.versions("B") == _v("1.3", "1.2", "1.1", "1.0")
    assert g.versions("c") == _v("3.2", "3.1", "3.0", "2.1", "2.0",
                                 "1.2", "1.1", "1.0")
    assert g.versions("nope") == []


def test_toy_deps_and_allowed_exact_sets():
    g = Graph.from_dict(TOY)
    assert g.deps("a", Version("1.4")) == [("c", SpecifierSet("<2.0"))]
    assert g.deps("b", "1.0") == [("c", SpecifierSet(">=3.1"))]
    assert g.deps("c", "3.2") == []
    assert g.allowed("c", SpecifierSet("<2.0")) == frozenset(
        _v("1.0", "1.1", "1.2"))
    assert g.allowed("c", SpecifierSet(">=3.1")) == frozenset(
        _v("3.1", "3.2"))
    assert g.dangling() == []


def test_version_objects_are_shared():
    g = Graph.from_dict(TOY)
    newest = g.versions("c")[0]
    (got,) = [v for v in g.allowed("c", SpecifierSet(">3.1"))]
    assert got is newest


def test_prerelease_default_behaviour():
    g = Graph.from_dict({"x": {"1.0": [], "2.0b1": []}})
    assert g.allowed("x", SpecifierSet(">=1")) == frozenset(_v("1.0"))
    assert g.allowed("x", SpecifierSet(">=2.0b1")) == frozenset(_v("2.0b1"))


def test_dangling_edge_kept_and_allowed_empty():
    g = Graph.from_dict({"p": {"1.0": [["ghost", ">=1"], ["q", ""]]},
                         "q": {"1.0": []}})
    assert g.deps("p", "1.0") == [("ghost", SpecifierSet(">=1")),
                                  ("q", SpecifierSet(""))]
    assert g.allowed("ghost", SpecifierSet(">=1")) == frozenset()
    assert g.dangling() == [("p", Version("1.0"), "ghost")]


def test_allowed_second_call_is_cache_hit():
    g = Graph.from_dict(TOY)
    first = g.allowed("c", SpecifierSet("<2.0"))
    assert g.allowed_misses == 1
    second = g.allowed("c", SpecifierSet("<2.0"))
    assert g.allowed_misses == 1
    assert second is first


def test_load_as_of_excludes_later_release(tmp_path):
    db = tmp_path / "t.db"
    conn = store.open_db(str(db))
    _ensure_clean_tables(conn)
    rel = "INSERT INTO releases VALUES (?, ?, ?, 0, NULL, 'f', 'u', 's')"
    conn.execute(rel, ("a", "1.0", "2020-01-01T00:00:00Z"))
    conn.execute(rel, ("a", "2.0", "2024-06-01T00:00:00.123456Z"))
    conn.execute(rel, ("c", "1.0", "2019-01-01T00:00:00Z"))
    conn.execute("INSERT INTO deps VALUES ('a', '1.0', 'c', '<2')")
    conn.execute("INSERT INTO deps VALUES ('a', '2.0', 'c', '>=1')")
    conn.commit()
    conn.close()

    g = Graph.load(str(db))
    assert g.versions("a") == _v("2.0", "1.0")

    g = Graph.load(str(db), as_of=datetime(2023, 1, 1, tzinfo=timezone.utc))
    assert g.versions("a") == _v("1.0")
    assert g.deps("a", "1.0") == [("c", SpecifierSet("<2"))]
    assert g.deps("a", "2.0") == []
    assert g.allowed("c", SpecifierSet("<2")) == frozenset(_v("1.0"))
