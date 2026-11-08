#!/usr/bin/env python3
"""Spike: probe PyPI APIs to inform Crux data-layer design."""

import asyncio
import json
import statistics
import time

import httpx

PROJECTS = ["requests", "numpy", "django", "six", "setuptools"]
SIMPLE_HDR = {"Accept": "application/vnd.pypi.simple.v1+json"}
TIMEOUT = 30


# ── helpers ──────────────────────────────────────────────────────────
def heading(n, title):
    print(f"\n{'='*64}\n  {n}. {title}\n{'='*64}")


# ── 1. Top list ──────────────────────────────────────────────────────
def probe_top_list(c):
    heading(1, "TOP-PYPI-PACKAGES LIST")
    url = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
    r = c.get(url)
    r.raise_for_status()
    data = r.json()

    top_keys = list(data.keys())
    print(f"Top-level keys: {top_keys}")

    # Find the list — could be "rows" or "projects"
    rows_key = None
    rows = []
    for k, v in data.items():
        if isinstance(v, list) and len(v) > 0:
            rows_key = k
            rows = v
            break
    print(f"Rows key: {rows_key!r}")
    print(f"Row count: {len(rows)}")
    if rows and isinstance(rows[0], dict):
        print(f"One row keys: {list(rows[0].keys())}")
        print(f"One row sample: {rows[0]}")
    first_5 = []
    for row in rows[:5]:
        if isinstance(row, dict):
            name = row.get("project") or row.get("name") or row.get("package") or "?"
            first_5.append(name)
    print(f"First 5 names: {first_5}")
    return data


# ── 2. Simple API ────────────────────────────────────────────────────
def probe_simple(c, name):
    url = f"https://pypi.org/simple/{name}/"
    r = c.get(url, headers=SIMPLE_HDR)
    r.raise_for_status()
    data = r.json()

    files = data.get("files", [])
    versions = data.get("versions", [])

    wheels = [f for f in files if f["filename"].endswith(".whl")]
    sdists = [f for f in files if f["filename"].endswith((".tar.gz", ".zip", ".tar.bz2"))]
    other = len(files) - len(wheels) - len(sdists)
    yanked = [f for f in files if f.get("yanked")]

    # core-metadata: check both PEP-714 key and legacy key
    def has_cm(f):
        cm = f.get("core-metadata")
        if cm and cm is not False:
            return True
        dm = f.get("data-dist-info-metadata")
        if dm and dm is not False:
            return True
        return False

    wheels_cm = [f for f in wheels if has_cm(f)]
    sdists_cm = [f for f in sdists if has_cm(f)]

    return {
        "resp_keys": list(data.keys()),
        "file_keys": list(files[0].keys()) if files else [],
        "versions": versions,
        "n_versions": len(versions),
        "n_files": len(files),
        "n_wheels": len(wheels),
        "n_sdists": len(sdists),
        "n_other": other,
        "n_yanked": len(yanked),
        "n_wheels_cm": len(wheels_cm),
        "n_sdists_cm": len(sdists_cm),
        "resp_kb": len(r.content) / 1024,
        "files": files,
        "sample_file": files[0] if files else None,
    }


def run_simple(c):
    heading(2, "SIMPLE API")
    results = {}
    for name in PROJECTS:
        sr = probe_simple(c, name)
        results[name] = sr
        print(f"\n--- {name} ---")
        print(f"  Response keys: {sr['resp_keys']}")
        print(f"  File-object keys: {sr['file_keys']}")
        if sr["sample_file"]:
            print(f"  Sample file: {json.dumps(sr['sample_file'], indent=4)}")
        print(f"  Versions: {sr['n_versions']}")
        print(f"  Files: {sr['n_files']}  (wheels {sr['n_wheels']}, "
              f"sdists {sr['n_sdists']}, other {sr['n_other']})")
        print(f"  Yanked files: {sr['n_yanked']}")
        print(f"  Wheels with core-metadata: {sr['n_wheels_cm']} / {sr['n_wheels']}")
        pct_w = (sr["n_wheels_cm"] / sr["n_wheels"] * 100) if sr["n_wheels"] else 0
        print(f"  Wheels core-metadata %: {pct_w:.1f}%")
        print(f"  Sdists with core-metadata: {sr['n_sdists_cm']} / {sr['n_sdists']}")
        pct_s = (sr["n_sdists_cm"] / sr["n_sdists"] * 100) if sr["n_sdists"] else 0
        print(f"  Sdists core-metadata %: {pct_s:.1f}%")
        print(f"  Response size: {sr['resp_kb']:.1f} KB")
    return results


# ── 3. PEP 658 metadata ─────────────────────────────────────────────
def probe_pep658(c, simple_results):
    heading(3, "PEP 658 .metadata FILES")

    for name in PROJECTS:
        sr = simple_results[name]
        files = sr["files"]

        def has_cm(f):
            return (f.get("core-metadata") not in (None, False)) or \
                   (f.get("data-dist-info-metadata") not in (None, False))

        with_cm = [f for f in files if has_cm(f)]
        if not with_cm:
            print(f"\n{name}: 0 files have core-metadata — skipping")
            continue

        print(f"\n--- {name}: {len(with_cm)}/{len(files)} files have core-metadata ---")

        # Sample up to 5 (mix of wheel and sdist)
        wheels_cm = [f for f in with_cm if f["filename"].endswith(".whl")]
        sdists_cm = [f for f in with_cm if not f["filename"].endswith(".whl")]
        sample = wheels_cm[:3] + sdists_cm[:2]

        for f in sample:
            meta_url = f["url"] + ".metadata"
            try:
                r = c.get(meta_url)
                r.raise_for_status()
                text = r.text
                has_rd = "Requires-Dist:" in text
                has_rp = "Requires-Python:" in text
                print(f"  {f['filename'][:60]}")
                print(f"    size={len(r.content)} bytes  "
                      f"Requires-Dist={has_rd}  Requires-Python={has_rp}")
            except Exception as e:
                print(f"  {f['filename'][:60]}: FETCH ERROR {e}")


# ── 4. Release JSON ─────────────────────────────────────────────────
def probe_release_json(c, simple_results):
    heading(4, "RELEASE JSON (/pypi/<name>/<ver>/json)")

    for name in PROJECTS:
        sr = simple_results[name]
        versions = sr["versions"]
        if not versions:
            print(f"\n{name}: no versions")
            continue

        latest = versions[-1]
        url = f"https://pypi.org/pypi/{name}/{latest}/json"
        r = c.get(url)
        r.raise_for_status()
        data = r.json()
        json_kb = len(r.content) / 1024

        info = data.get("info", {})
        rd = info.get("requires_dist")

        print(f"\n--- {name} {latest} ---")
        print(f"  JSON size: {json_kb:.1f} KB")
        print(f"  requires_dist type: {type(rd).__name__}")
        print(f"  requires_dist length: {len(rd) if rd is not None else 'null'}")
        if name == "six":
            print(f"  six requires_dist value: {rd!r}")

        # Compare .metadata size
        def has_cm(f):
            return (f.get("core-metadata") not in (None, False)) or \
                   (f.get("data-dist-info-metadata") not in (None, False))

        latest_files_cm = [
            f for f in sr["files"]
            if has_cm(f) and latest.replace(".", "") in f["filename"].replace(".", "")
        ]
        # Simpler: just try wheels matching the version
        latest_wheels = [
            f for f in sr["files"]
            if has_cm(f) and f["filename"].endswith(".whl")
            and f"-{latest}-" in f["filename"]
        ]
        if latest_wheels:
            mf = latest_wheels[0]
            try:
                mr = c.get(mf["url"] + ".metadata")
                mr.raise_for_status()
                meta_kb = len(mr.content) / 1024
                print(f"  .metadata size: {meta_kb:.1f} KB")
                print(f"  JSON / .metadata ratio: {json_kb / meta_kb:.1f}x")
            except Exception as e:
                print(f"  .metadata fetch error: {e}")
        else:
            print(f"  (no matching wheel with core-metadata for version {latest})")

    # Check whether requires_dist is ever null for a package that has deps
    print(f"\n--- Null requires_dist check ---")
    # Check older versions of requests
    name = "requests"
    old_versions_to_check = simple_results[name]["versions"][:10]
    for ver in old_versions_to_check:
        try:
            url = f"https://pypi.org/pypi/{name}/{ver}/json"
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
            rd = data.get("info", {}).get("requires_dist")
            if rd is None:
                print(f"  requests {ver}: requires_dist is null!")
            else:
                print(f"  requests {ver}: requires_dist has {len(rd)} entries")
        except Exception as e:
            print(f"  requests {ver}: error {e}")


# ── 5. Timing ────────────────────────────────────────────────────────
def probe_timing(c):
    heading(5, "TIMING")

    # Sequential
    times = []
    for name in PROJECTS:
        url = f"https://pypi.org/simple/{name}/"
        t0 = time.monotonic()
        r = c.get(url, headers=SIMPLE_HDR)
        t1 = time.monotonic()
        times.append(t1 - t0)
        print(f"  {name}: {t1-t0:.3f}s  (status {r.status_code})")

    print(f"\n  Median: {statistics.median(times):.3f}s")
    print(f"  Mean:   {statistics.mean(times):.3f}s")
    print(f"  Min:    {min(times):.3f}s")
    print(f"  Max:    {max(times):.3f}s")

    return times


async def probe_concurrent():
    print("\n  --- 10 concurrent requests ---")
    urls = [f"https://pypi.org/simple/{p}/" for p in PROJECTS * 2]

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as ac:
        t0 = time.monotonic()
        tasks = [ac.get(url, headers=SIMPLE_HDR) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        t1 = time.monotonic()

    codes = []
    for r in responses:
        if isinstance(r, Exception):
            codes.append(f"ERR:{type(r).__name__}")
        else:
            codes.append(r.status_code)

    print(f"  Total wall time: {t1 - t0:.3f}s")
    print(f"  Status codes: {codes}")
    got_429 = any(c == 429 for c in codes if isinstance(c, int))
    print(f"  Got 429s: {got_429}")
    return got_429


# ── 6. Projection ───────────────────────────────────────────────────
def projection(simple_results):
    heading(6, "PROJECTION FOR 5,000 PROJECTS")

    print("\nPer-project stats:")
    total_ver = 0
    total_files = 0
    total_yanked = 0
    total_wheels_cm = 0
    total_wheels = 0

    for name in PROJECTS:
        sr = simple_results[name]
        total_ver += sr["n_versions"]
        total_files += sr["n_files"]
        total_yanked += sr["n_yanked"]
        total_wheels_cm += sr["n_wheels_cm"]
        total_wheels += sr["n_wheels"]
        print(f"  {name:12s}: versions={sr['n_versions']:5d}  "
              f"files={sr['n_files']:5d}  yanked={sr['n_yanked']:4d}  "
              f"wheels_with_cm={sr['n_wheels_cm']:5d}")

    n = len(PROJECTS)
    avg_ver = total_ver / n
    avg_files = total_files / n
    avg_yanked = total_yanked / n
    avg_wheels = total_wheels / n
    avg_cm = total_wheels_cm / n

    print(f"\nAverages (n={n}):")
    print(f"  versions/project:  {avg_ver:.0f}")
    print(f"  files/project:     {avg_files:.0f}")
    print(f"  yanked/project:    {avg_yanked:.0f}")
    print(f"  wheels/project:    {avg_wheels:.0f}")
    print(f"  wheels_w_cm/proj:  {avg_cm:.0f}")

    P = 5000
    print(f"\n=== Estimate for {P} projects ===")

    # WITHOUT prefiltering
    simple_req = P
    # If we fetch release JSON per version:
    est_ver = int(avg_ver * P)
    est_files = int(avg_files * P)
    total_no_filter = simple_req + est_ver
    print(f"\nWITHOUT prefiltering:")
    print(f"  Simple API requests:              {simple_req:>10,}")
    print(f"  Estimated total versions:         {est_ver:>10,}")
    print(f"  Release-JSON (1 per version):     {est_ver:>10,}")
    print(f"  TOTAL requests:                   {total_no_filter:>10,}")
    print(f"  Arithmetic: {P} + {P}*{avg_ver:.0f} = {total_no_filter:,}")

    # WITH prefiltering via Simple API
    # Assume ~10% of files are yanked, ~30% fail requires-python >=3.12
    # (rough; we'll refine with real data below)
    # Actually, compute from our data
    if total_files > 0:
        yanked_pct = total_yanked / total_files * 100
    else:
        yanked_pct = 0

    # Check requires-python filtering
    rp_excludes = 0
    rp_total = 0
    for name in PROJECTS:
        sr = simple_results[name]
        for f in sr["files"]:
            rp = f.get("requires-python")
            if rp:
                rp_total += 1
                # Very rough: if requires-python doesn't include 3.12
                # For a proper check we'd use packaging.specifiers
                # Here just check if it starts with >=3.x where x > 12
                # or ==2.x, etc. We'll record the raw number.
            # Can't reliably filter without `packaging` — just count presence
    print(f"\n  Yanked % (observed):      {yanked_pct:.1f}%")
    print(f"  Files with requires-python: {rp_total}/{total_files}")

    # WITH prefiltering: use Simple API to exclude yanked + requires-python mismatch
    # Assume conservatively 20% of versions are filterable
    filter_pct = yanked_pct + 15  # yanked + rp-excluded (conservative estimate)
    kept_ver = int(avg_ver * (1 - filter_pct / 100))
    est_ver_filtered = kept_ver * P
    total_filtered = simple_req + est_ver_filtered
    print(f"\nWITH prefiltering (yanked + requires-python excludes ~{filter_pct:.0f}%):")
    print(f"  Simple API requests:              {simple_req:>10,}")
    print(f"  Versions kept per project:        {kept_ver:>10}")
    print(f"  Metadata fetches (PEP 658):       {est_ver_filtered:>10,}")
    print(f"  TOTAL requests:                   {total_filtered:>10,}")
    print(f"  Arithmetic: {P} + {P}*{kept_ver} = {total_filtered:,}")

    # PEP 658 alternative: fetch .metadata instead of release JSON
    if total_wheels > 0:
        cm_pct = total_wheels_cm / total_wheels * 100
    else:
        cm_pct = 0
    print(f"\n  Wheels with PEP 658 metadata: {cm_pct:.1f}%")
    print(f"  → PEP 658 avoids release-JSON for {cm_pct:.0f}% of wheels")


# ── main ─────────────────────────────────────────────────────────────
def main():
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as c:
        probe_top_list(c)
        simple_results = run_simple(c)
        probe_pep658(c, simple_results)
        probe_release_json(c, simple_results)
        times = probe_timing(c)

    got_429 = asyncio.run(probe_concurrent())
    projection(simple_results)


if __name__ == "__main__":
    main()
