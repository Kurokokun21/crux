#!/usr/bin/env python3
"""Follow-up probes with retries to fill gaps in spike data."""
import httpx
import time
from packaging.specifiers import SpecifierSet
from collections import defaultdict

SIMPLE_HDR = {"Accept": "application/vnd.pypi.simple.v1+json"}
PROJECTS = ["requests", "numpy", "django", "six", "setuptools"]


def get(c, url, **kw):
    """GET with simple retry on transient errors."""
    for attempt in range(4):
        try:
            r = c.get(url, **kw)
            r.raise_for_status()
            return r
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            wait = 2 ** attempt
            print(f"  [retry {attempt+1}] {e.__class__.__name__}, waiting {wait}s")
            time.sleep(wait)
    return c.get(url, **kw)  # final attempt, let it raise


with httpx.Client(timeout=60, follow_redirects=True) as c:
    # ── 1. Top list (corrected) ───────────────────────────────────
    print("=== 1. TOP LIST (corrected) ===")
    r = get(c, "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json")
    data = r.json()
    print(f"Top-level keys: {list(data.keys())}")
    rows = data.get("rows", [])
    print(f"'rows' count: {len(rows)}")
    if rows:
        print(f"Row keys: {list(rows[0].keys())}")
        print(f"Sample row: {rows[0]}")
        first_5 = [row.get("project", row.get("name", "?")) for row in rows[:5]]
        print(f"First 5: {first_5}")
    # Also show other scalar keys
    for k, v in data.items():
        if not isinstance(v, (list, dict)):
            print(f"  {k}: {v}")
        elif isinstance(v, dict):
            print(f"  {k}: {v}")

    time.sleep(1)

    # ── 2. PEP 658 recent wheels (Requires-Dist check) ───────────
    print("\n=== 2. PEP 658 - RECENT WHEELS (Requires-Dist/Requires-Python) ===")
    for name in PROJECTS:
        r = get(c, f"https://pypi.org/simple/{name}/", headers=SIMPLE_HDR)
        data = r.json()
        files = data["files"]

        recent_wheels = [
            f for f in files
            if f["filename"].endswith(".whl")
            and (f.get("core-metadata") not in (None, False))
        ]
        if not recent_wheels:
            print(f"\n{name}: no wheels with core-metadata")
            continue

        # Check LAST 2 (newest) and FIRST 2 (oldest with CM)
        sample = recent_wheels[:2] + recent_wheels[-2:]
        print(f"\n{name} ({len(recent_wheels)} wheels with CM):")
        for f in sample:
            mr = get(c, f["url"] + ".metadata")
            text = mr.text
            has_rd = "Requires-Dist:" in text
            has_rp = "Requires-Python:" in text
            rd_count = sum(1 for l in text.splitlines() if l.startswith("Requires-Dist:"))
            print(f"  {f['filename'][:65]}")
            print(f"    RD={has_rd} (n={rd_count})  RP={has_rp}  size={len(mr.content)}B")
        time.sleep(0.5)

    time.sleep(1)

    # ── 3. numpy requires_dist: null in JSON but has deps? ───────
    print("\n=== 3. NUMPY requires_dist ===")
    r = get(c, "https://pypi.org/simple/numpy/", headers=SIMPLE_HDR)
    data = r.json()
    latest = data["versions"][-1]
    print(f"Latest numpy: {latest}")

    rj = get(c, f"https://pypi.org/pypi/numpy/{latest}/json")
    info = rj.json()["info"]
    print(f"JSON requires_dist: {info['requires_dist']!r}")

    latest_whl = [
        f for f in data["files"]
        if f["filename"].endswith(".whl")
        and f"-{latest}-" in f["filename"]
        and (f.get("core-metadata") not in (None, False))
    ]
    if latest_whl:
        mr = get(c, latest_whl[0]["url"] + ".metadata")
        rd_lines = [l for l in mr.text.splitlines() if l.startswith("Requires-Dist:")]
        print(f".metadata Requires-Dist count: {len(rd_lines)}")
        for l in rd_lines[:3]:
            print(f"  {l[:80]}")
    else:
        print("No matching wheel with CM for latest numpy")

    time.sleep(1)

    # ── 4. requires-python filtering for CPython 3.12 ────────────
    print("\n=== 4. REQUIRES-PYTHON FILTERING (CPython 3.12) ===")
    simple_cache = {}
    for name in PROJECTS:
        r = get(c, f"https://pypi.org/simple/{name}/", headers=SIMPLE_HDR)
        data = r.json()
        simple_cache[name] = data
        files = data["files"]
        total = len(files)
        excluded = 0
        no_rp = 0
        for f in files:
            rp = f.get("requires-python")
            if not rp:
                no_rp += 1
                continue
            try:
                spec = SpecifierSet(rp)
                if "3.12" not in spec:
                    excluded += 1
            except Exception:
                pass
        print(f"  {name:12s}: total={total:5d}  no_rp={no_rp:5d}  "
              f"excluded_by_rp={excluded:5d} ({excluded/total*100:.1f}%)")
        time.sleep(0.3)

    # ── 5. Version-level filtering ───────────────────────────────
    print("\n=== 5. VERSION-LEVEL FILTERING FOR 3.12 ===")
    for name in PROJECTS:
        data = simple_cache[name]
        files = data["files"]
        versions = data["versions"]
        n_versions = len(versions)

        # Group files by version
        ver_files = defaultdict(list)
        for f in files:
            fn = f["filename"]
            for v in versions:
                if f"-{v}-" in fn or f"-{v}." in fn or fn.startswith(f"{name}-{v}"):
                    ver_files[v].append(f)
                    break

        ver_yanked = 0
        ver_rp_excluded = 0
        ver_kept = 0

        for v in versions:
            vf = ver_files.get(v, [])
            if not vf:
                ver_kept += 1  # can't determine → keep
                continue
            # All yanked?
            non_yanked = [f for f in vf if not f.get("yanked")]
            if not non_yanked:
                ver_yanked += 1
                continue
            # All excluded by requires-python?
            any_supports_312 = False
            for f in non_yanked:
                rp = f.get("requires-python")
                if not rp:
                    any_supports_312 = True  # no constraint → assume yes
                    break
                try:
                    if "3.12" in SpecifierSet(rp):
                        any_supports_312 = True
                        break
                except Exception:
                    any_supports_312 = True
                    break
            if any_supports_312:
                ver_kept += 1
            else:
                ver_rp_excluded += 1

        print(f"  {name:12s}: versions={n_versions:5d}  yanked_only={ver_yanked:4d}  "
              f"rp_excluded={ver_rp_excluded:4d}  kept={ver_kept:4d}  "
              f"filter_pct={((ver_yanked + ver_rp_excluded)/n_versions*100):.1f}%")

print("\nDone.")
