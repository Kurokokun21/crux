"""QA checks over the cleaned database.

Each check is one SQL query.  Four small Python functions are registered on
the connection for logic SQL cannot express: canon(), version_ok(), py_ok()
and n_lines().

CLI: python -m crux.data.qa
"""

import sqlite3

from packaging.utils import canonicalize_name

from crux.data import store
from crux.data.clean import _load_metadata
from crux.data.config import DB_PATH
from crux.data.parse import parse_version, python_ok

_NAMES = """
    SELECT 'files' AS t, name AS n FROM files
    UNION ALL SELECT 'releases', name FROM releases
    UNION ALL SELECT 'projects', name FROM projects
    UNION ALL SELECT 'deps.name', name FROM deps
    UNION ALL SELECT 'deps.dep_name', dep_name FROM deps
"""

_CHECKS = [
    ("error", "non_canonical",
     f"SELECT DISTINCT t || ': ' || n FROM ({_NAMES})"
     " WHERE n != canon(n)"),
    ("error", "bad_version",
     "SELECT name || ' ' || version FROM releases WHERE NOT version_ok(version)"),
    ("error", "orphan_dep",
     "SELECT d.name || ' ' || d.version || ' -> ' || d.dep_name FROM deps d"
     " WHERE NOT EXISTS (SELECT 1 FROM releases r"
     "   WHERE r.name = d.name AND r.version = d.version)"),
    ("error", "py_excluded",
     "SELECT name || ' ' || version || ' ' || requires_python FROM releases"
     " WHERE NOT py_ok(requires_python)"),
    ("error", "duplicate",
     "SELECT 'releases: ' || name || ' ' || version FROM releases"
     " GROUP BY name, version HAVING COUNT(*) > 1"
     " UNION ALL SELECT 'files: ' || name || ' ' || version FROM files"
     " GROUP BY name, version HAVING COUNT(*) > 1"),
    # P3: per project, versions seen == kept files + ingest drops
    ("error", "conservation_ingest",
     "SELECT name || ' seen=' || n_seen || ' kept=' || n_kept"
     " || ' files=' || nf || ' drops=' || nd FROM ("
     " SELECT p.name, p.n_seen, p.n_kept,"
     "  (SELECT COUNT(*) FROM files f WHERE f.name = p.name) AS nf,"
     "  (SELECT COUNT(*) FROM drops d"
     "    WHERE d.stage = 'ingest' AND d.name = p.name) AS nd"
     " FROM projects p WHERE p.status = 'done')"
     " WHERE n_seen != nf + nd OR n_kept != nf"),
    # P6: every files row is exactly one of: a release, a clean drop
    ("error", "conservation_release",
     "SELECT f.name || ' ' || f.version FROM files f WHERE"
     " (SELECT COUNT(*) FROM releases r"
     "   WHERE r.name = f.name AND r.version = f.version)"
     " + (SELECT COUNT(*) FROM drops d WHERE d.stage = 'clean'"
     "   AND d.name = f.name AND d.version = f.version) != 1"),
    # P6: per release, Requires-Dist lines == deps rows + edge drops
    ("error", "conservation_lines",
     "SELECT r.name || ' ' || r.version FROM releases r WHERE"
     " n_lines(r.name, r.version, r.url) !="
     " (SELECT COUNT(*) FROM deps d"
     "   WHERE d.name = r.name AND d.version = r.version)"
     " + (SELECT COUNT(*) FROM drops d WHERE d.stage = 'edge'"
     "   AND d.name = r.name AND d.version = r.version)"),
    # Two distinct version STRINGS that are equal under PEP 440 (e.g. "1.0"
    # and "1.0.0") are separate rows here, because `releases` is keyed on the
    # string -- but graph.py keys on the Version object, so they would
    # collapse into one node with merged dependency edges.
    ("error", "version_collision",
     "SELECT name || ': ' || GROUP_CONCAT(version) FROM releases"
     " GROUP BY name, norm_version(version) HAVING COUNT(*) > 1"),
    ("warning", "self_dep",
     "SELECT DISTINCT name || ' ' || version FROM deps WHERE dep_name = name"),
]


def _version_ok(v):
    return not isinstance(parse_version(v), str)


def _py_ok(spec):
    return python_ok(spec) is True


def _n_lines(conn, name, version, url):
    """Requires-Dist line count, or -1 if the metadata is not cached.

    Returning -1 makes conservation_lines report the release instead of
    letting the ValueError surface as an opaque "user-defined function
    raised exception" that aborts every remaining check.
    """
    try:
        return len(_load_metadata(conn, name, version, url)[1])
    except (ValueError, KeyError, TypeError):
        return -1


def _norm_version(v):
    """A key that is equal exactly when two version strings are PEP 440-equal.

    str(Version(...)) will not do: it keeps the release segment as written,
    so str(Version("2.0.0")) == "2.0.0" != "2.0" even though the two compare
    equal.  Trailing zeros in the release segment are therefore stripped.
    Unparseable versions key on themselves (bad_version reports those).
    """
    parsed = parse_version(v)
    if isinstance(parsed, str):
        return v
    rel = list(parsed.release)
    while len(rel) > 1 and rel[-1] == 0:
        rel.pop()
    key = f"{parsed.epoch}!" if parsed.epoch else ""
    key += ".".join(str(n) for n in rel)
    if parsed.pre is not None:
        key += "".join(str(x) for x in parsed.pre)
    if parsed.post is not None:
        key += f".post{parsed.post}"
    if parsed.dev is not None:
        key += f".dev{parsed.dev}"
    if parsed.local:
        key += f"+{parsed.local}"
    return key


def check(db_path):
    """Return a list of (severity, check, detail) violations."""
    conn = sqlite3.connect(db_path)
    conn.create_function("canon", 1, canonicalize_name, deterministic=True)
    conn.create_function("version_ok", 1, _version_ok, deterministic=True)
    conn.create_function("py_ok", 1, _py_ok, deterministic=True)
    conn.create_function(
        "norm_version", 1, _norm_version, deterministic=True)
    conn.create_function("n_lines", 3, lambda n, v, u: _n_lines(conn, n, v, u))
    try:
        return [(sev, name, detail)
                for sev, name, sql in _CHECKS
                for (detail,) in conn.execute(sql)]
    finally:
        conn.close()


if __name__ == "__main__":
    from collections import Counter

    out = check(DB_PATH)
    for (sev, name), n in sorted(Counter((s, c) for s, c, _ in out).items()):
        print(f"{sev:8s} {name:22s} {n}")
    for sev, name, detail in out[:20]:
        print(f"  {sev} {name}: {detail}")
    n_err = sum(1 for s, _, _ in out if s == "error")
    print(f"errors {n_err}  warnings {len(out) - n_err}")
