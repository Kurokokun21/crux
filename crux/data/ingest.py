"""Ingest a single project from PyPI into the local cache."""

import json
from datetime import datetime, timezone

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from crux.data import store
from crux.data.config import (
    AFTER_CUTOFF,
    EPOCH,
    INVALID_VERSION,
    LOCAL_VERSION,
    MAX_VERSIONS_PER_PROJECT,
    NO_METADATA,
    NO_WHEEL,
    PY_VERSION,
    SNAPSHOT_CUTOFF,
    TARGET_PYTHON,
    YANKED,
)
from crux.data.fetch import fetch

_SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"


# ── tables ───────────────────────────────────────────────────────────

def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            name            TEXT NOT NULL,
            version         TEXT NOT NULL,
            filename        TEXT NOT NULL,
            url             TEXT NOT NULL,
            sha256          TEXT NOT NULL,
            upload_time     TEXT,
            requires_python TEXT,
            PRIMARY KEY (name, version)
        );
        CREATE TABLE IF NOT EXISTS drops (
            stage   TEXT NOT NULL,
            reason  TEXT NOT NULL,
            name    TEXT NOT NULL,
            version TEXT NOT NULL,
            detail  TEXT
        );
    """)


# ── wheel compatibility (CPython 3.12, Linux x86-64) ────────────────

def _is_compatible(tag):
    """Check one packaging.tags.Tag against our target."""
    interp = tag.interpreter
    abi = tag.abi
    plat = tag.platform

    # interpreter
    interp_ok = False
    if interp in ("py3", "cp312"):
        interp_ok = True
    elif interp.startswith("py3") and interp[3:].isdigit():
        interp_ok = int(interp[3:]) <= 12
    elif abi == "abi3" and interp.startswith("cp3") and interp[3:].isdigit():
        interp_ok = int(interp[3:]) <= 12
    if not interp_ok:
        return False

    # abi
    if abi not in ("none", "abi3", "cp312"):
        return False

    # platform
    if plat == "any" or plat == "linux_x86_64":
        return True
    if "manylinux" in plat and "x86_64" in plat:
        return True
    if "musllinux" in plat and "x86_64" in plat:
        return True
    return False


def _tier(tags):
    """Best tier for a set of compatible tags (lower = better)."""
    best = 3
    for t in tags:
        if t.interpreter == "py3" and t.abi == "none" and t.platform == "any":
            return 1
        if (t.interpreter == "cp312" or t.abi == "abi3") and \
           "manylinux" in t.platform and "x86_64" in t.platform:
            best = min(best, 2)
    return best


def pick_file(files):
    """Pick the best compatible wheel from *files*.  Returns dict or None."""
    scored = []
    for f in files:
        fn = f["filename"]
        if not fn.endswith(".whl"):
            continue
        try:
            _, _, _, tags = parse_wheel_filename(fn)
        except Exception:
            continue
        compat = [t for t in tags if _is_compatible(t)]
        if not compat:
            continue
        scored.append((_tier(compat), fn, f))
    if not scored:
        return None
    scored.sort()
    return scored[0][2]


# ── helpers ──────────────────────────────────────────────────────────

def _extract_version(filename):
    """Return normalised version string from a wheel or sdist filename."""
    if filename.endswith(".whl"):
        try:
            _, ver, _, _ = parse_wheel_filename(filename)
            return str(ver)
        except Exception:
            return None
    for ext in (".tar.gz", ".tar.bz2", ".zip"):
        if filename.endswith(ext):
            stem = filename[: -len(ext)]
            dash = stem.rfind("-")
            if dash == -1:
                return None
            try:
                return str(Version(stem[dash + 1 :]))
            except InvalidVersion:
                return stem[dash + 1 :]
    return None


def _group_files(all_files, versions):
    """Group file dicts by version string."""
    vset = set(versions)
    groups = {v: [] for v in versions}
    for f in all_files:
        ver = _extract_version(f["filename"])
        if ver is not None and ver in vset:
            groups[ver].append(f)
    return groups


def _before_cutoff(file_dict):
    ut = file_dict.get("upload-time")
    if not ut:
        return True
    try:
        dt = datetime.fromisoformat(ut.replace("Z", "+00:00"))
        return dt <= SNAPSHOT_CUTOFF
    except (ValueError, TypeError):
        return True


def _rp_allows(file_dict):
    rp = file_dict.get("requires-python")
    if not rp:
        return True
    try:
        return TARGET_PYTHON in SpecifierSet(rp)
    except Exception:
        return True


# ── metadata fetch ───────────────────────────────────────────────────

async def _fetch_metadata(client, conn, name, version, picked, **kw):
    """Fetch and cache metadata.  Returns True on success."""
    has_cm = picked.get("core-metadata") not in (None, False) or \
             picked.get("data-dist-info-metadata") not in (None, False)

    if has_cm:
        meta_url = picked["url"] + ".metadata"
        status, _ = await fetch(client, meta_url, conn, **kw)
        if status == 200:
            return True

    json_url = f"https://pypi.org/pypi/{name}/{version}/json"
    status, body = await fetch(client, json_url, conn, **kw)
    if status == 200:
        try:
            data = json.loads(body)
            if "info" in data:
                data["info"].pop("description", None)
            store.put(conn, json_url, 200, json.dumps(data).encode())
        except (json.JSONDecodeError, KeyError):
            pass
        return True
    return False


# ── main entry point ─────────────────────────────────────────────────

async def ingest_project(client, name, conn, *, semaphore=None,
                         _sleep=None):
    """Ingest one project.  Returns {"name", "n_seen", "n_kept"}."""
    import asyncio
    if _sleep is None:
        _sleep = asyncio.sleep

    name = canonicalize_name(name)
    _ensure_tables(conn)

    # idempotent: clear previous run
    conn.execute(
        "DELETE FROM drops WHERE name = ? AND stage = 'ingest'", (name,))
    conn.execute("DELETE FROM files WHERE name = ?", (name,))
    conn.commit()

    # 1. fetch Simple API
    url = f"https://pypi.org/simple/{name}/"
    status, body = await fetch(
        client, url, conn, accept=_SIMPLE_ACCEPT,
        semaphore=semaphore, _sleep=_sleep,
    )
    if status != 200:
        return {"name": name, "n_seen": 0, "n_kept": 0}

    data = json.loads(body)
    versions = data.get("versions", [])
    by_ver = _group_files(data.get("files", []), versions)

    # 2-3. filter and pick
    kept = []           # (Version, ver_str, picked_file_dict)
    drops = []          # (reason, name, ver_str, detail)

    for ver_str in versions:
        # version-level checks
        try:
            ver = Version(ver_str)
        except InvalidVersion:
            drops.append((INVALID_VERSION, name, ver_str, ver_str))
            continue
        if ver.epoch > 0:
            drops.append((EPOCH, name, ver_str, f"epoch={ver.epoch}"))
            continue
        if ver.local is not None:
            drops.append((LOCAL_VERSION, name, ver_str, f"local={ver.local}"))
            continue

        # file-level filtering
        cands = by_ver.get(ver_str, [])

        non_yanked = [f for f in cands if not f.get("yanked")]
        if not non_yanked:
            drops.append((YANKED, name, ver_str, ""))
            continue
        cands = non_yanked

        within = [f for f in cands if _before_cutoff(f)]
        if not within:
            drops.append((AFTER_CUTOFF, name, ver_str, ""))
            continue
        cands = within

        rp_ok = [f for f in cands if _rp_allows(f)]
        if not rp_ok:
            rps = {f.get("requires-python", "") for f in cands}
            drops.append((PY_VERSION, name, ver_str, "; ".join(rps)))
            continue
        cands = rp_ok

        picked = pick_file(cands)
        if picked is None:
            drops.append((NO_WHEEL, name, ver_str, ""))
            continue

        kept.append((ver, ver_str, picked))

    # 5. MAX_VERSIONS_PER_PROJECT
    if MAX_VERSIONS_PER_PROJECT is not None:
        kept.sort(key=lambda x: x[0], reverse=True)
        kept = kept[: MAX_VERSIONS_PER_PROJECT]

    # 4. fetch metadata concurrently (bounded by the shared semaphore)
    if semaphore is None:
        semaphore = asyncio.Semaphore(10)
    oks = await asyncio.gather(*(
        _fetch_metadata(client, conn, name, ver_str, picked,
                        semaphore=semaphore, _sleep=_sleep)
        for _, ver_str, picked in kept
    ))
    final_kept = []
    for (ver, ver_str, picked), ok in zip(kept, oks):
        if not ok:
            drops.append((NO_METADATA, name, ver_str, picked["filename"]))
            continue
        sha = picked.get("hashes", {}).get("sha256", "")
        conn.execute(
            "INSERT OR REPLACE INTO files"
            " (name, version, filename, url, sha256, upload_time,"
            "  requires_python)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, ver_str, picked["filename"], picked["url"], sha,
             picked.get("upload-time"), picked.get("requires-python")),
        )
        final_kept.append(ver_str)

    # write drops
    if drops:
        conn.executemany(
            "INSERT INTO drops (stage, reason, name, version, detail)"
            " VALUES ('ingest', ?, ?, ?, ?)", drops,
        )
    conn.commit()

    return {"name": name, "n_seen": len(versions), "n_kept": len(final_kept)}
