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


# ── candidates(): ordered, memoized, accepts a plain string ──────────

def test_candidates_newest_first_and_matches_allowed():
    g = Graph.from_dict(TOY)
    spec = SpecifierSet("<2.0")
    assert g.candidates("c", spec) == tuple(_v("1.2", "1.1", "1.0"))
    assert frozenset(g.candidates("c", spec)) == g.allowed("c", spec)
    # order agrees with versions(), i.e. newest first
    newest = [v for v in g.versions("c") if v in g.allowed("c", spec)]
    assert list(g.candidates("c", spec)) == newest


def test_candidates_accepts_string_specifier():
    g = Graph.from_dict(TOY)
    assert g.candidates("c", "<2.0") == g.candidates("c", SpecifierSet("<2.0"))
    # str(SpecifierSet) is sorted, so spelling order does not split the cache
    assert g.candidates("c", ">=1.0,<2.0") == g.candidates("c", "<2.0,>=1.0")
    assert g.allowed("c", ">=3.1") == g.allowed("c", SpecifierSet(">=3.1"))


def test_candidates_shares_the_allowed_cache():
    g = Graph.from_dict(TOY)
    g.candidates("c", SpecifierSet("<2.0"))
    misses = g.allowed_misses
    assert misses == 1
    g.candidates("c", SpecifierSet("<2.0"))
    g.allowed("c", SpecifierSet("<2.0"))
    assert g.allowed_misses == misses


def test_candidates_empty_for_unknown_package():
    g = Graph.from_dict(TOY)
    assert g.candidates("nosuchpkg", SpecifierSet("")) == ()


# ── dependents(): the reverse index ──────────────────────────────────

def test_dependents_is_the_reverse_of_deps():
    g = Graph.from_dict(TOY)
    got = g.dependents("c")
    # every a-version and every b-version depends on c
    assert len(got) == len(g.versions("a")) + len(g.versions("b"))
    for pkg, version, spec in got:
        assert (("c", spec)) in g.deps(pkg, version)
    assert g.dependents("nosuchpkg") == ()


def test_dependents_cached_after_first_call():
    g = Graph.from_dict(TOY)
    assert g._reverse is None
    first = g.dependents("c")
    assert g._reverse is not None
    assert g.dependents("c") is first


# ── version-string collisions are recorded, not silent ───────────────

def test_pep440_equal_version_strings_are_recorded():
    g = Graph.from_dict(TOY)
    assert g.collisions == []
    # "1.0" and "1.0.0" are equal under PEP 440 but distinct strings
    collide = Graph.from_dict({
        "a": {"1.0": [["c", "<2.0"]], "1.0.0": [["c", ">=3.1"]]},
        "c": {"1.0": [], "3.1": []},
    })
    assert collide.collisions == [("a", "1.0.0")]
    # and the package is not left with a duplicate version entry
    assert collide.versions("a") == _v("1.0")


# ── a database that was never cleaned fails with a usable message ────

def test_load_unprepared_db_explains_itself(tmp_path):
    db = tmp_path / "empty.db"
    store.open_db(str(db)).close()
    try:
        Graph.load(str(db))
    except RuntimeError as exc:
        assert "crux.data.clean" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
