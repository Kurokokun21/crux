# Progress

## P0: Spike (2026-11-04)

**What exists:** `scripts/spike.py`, `scripts/spike2.py` (throwaway), `docs/SPIKE.md`.
**Findings:** Simple API (PEP 691) gives versions, files, requires-python, yanked, core-metadata per project in a single request. 100% of wheels have PEP 658 metadata, 0% of sdists. Old .metadata files lack Requires-Dist — fallback to JSON needed for pre-~2017 wheels. `requires_dist=null` in JSON is ambiguous (unknown, not "zero deps"). Prefiltering by yanked/RP saves only ~1.4% of versions.
**Decision:** Fetch strategy = Simple API → filter → PEP 658 .metadata (1 wheel/version) → JSON fallback for old versions.
**Limits:** Sample of 5 projects; averages skewed by numpy (4K+ files) and setuptools (627 versions). Real distribution of top-5000 may differ.

## P1: Scaffold, config, scope doc (2026-11-04)

**What exists:** `pyproject.toml`, `.gitignore`, `crux/data/config.py`, `docs/SCOPE.md`, `tests/data/test_config.py`.
**Config holds:** SNAPSHOT_DATE/CUTOFF, USER_AGENT, DB_PATH, TARGET_PYTHON, MARKER_ENV (static CPython 3.12 Linux x86-64), 12 reason-code constants.
**Decisions:** `packaging` pinned to 26.0. `platform_release`/`platform_version` set to empty string (host-dependent, rarely used in markers). `python_full_version` = "3.12.0".
**Tests:** 5 acceptance tests — MARKER_ENV completeness, no host values, three marker evaluations. All pass.

## P2: Cached async fetch (2026-11-04)

**What exists:** `crux/data/store.py`, `crux/data/fetch.py`, `tests/data/test_fetch.py`.
**store.py:** SQLite WAL-mode table `raw(url PK, status, fetched_at, body BLOB)`. Body zlib-compressed. Functions: `open_db`, `get`, `put`.
**fetch.py:** `async fetch(client, url, conn)` — cache-first, retries 5× on 429/5xx/timeout with exp backoff+jitter, honours Retry-After, semaphore concurrency limit (default 10), sends USER_AGENT. Caches 200 and 404 only.
**Decisions:** 5 total attempts (not 1+5). Semaphore passed by caller for sharing. `_sleep` injectable for tests. 404 stored as `b""`.
**Tests:** 7 acceptance tests (MockTransport, no network). 12 total tests pass.

## P3: Ingest one project (2026-11-04)

**What exists:** `crux/data/ingest.py`, `tests/data/test_ingest.py`, `tests/fixtures/{test-alpha,test-beta}/`.
**Tables:** `files(name, version, filename, url, sha256, upload_time, requires_python)` PK `(name, version)`. `drops(stage, reason, name, version, detail)`.
**pick_file:** 3-tier wheel scoring — tier 1 py3-none-any, tier 2 cp312/abi3 manylinux x86_64, tier 3 other compatible. Uses `parse_wheel_filename` + `_is_compatible` per tag. Returns best file dict or None.
**ingest_project:** canonicalize → fetch Simple API → group files by version → filter (INVALID_VERSION/EPOCH/LOCAL_VERSION → YANKED/AFTER_CUTOFF/PY_VERSION → NO_WHEEL) → MAX_VERSIONS trim → fetch metadata PEP 658-first w/ JSON fallback (NO_METADATA) → store files + drops. Idempotent (clears before re-inserting).
**Fixtures:** test-alpha (11 versions: 3 kept + 8 dropped, covers all 8 reason codes). test-beta (1 version, for cache test).
**Tests:** 7 pick_file parametrized (sdist, manylinux2014, macOS, py2.py3, abi3, multiple, windows), reason-code coverage, conservation, second-run-zero-HTTP. 10 tests pass.

## P4: Batch ingest with closure and resume (2026-11-04)

**What exists:** `crux/data/run_ingest.py`, `tests/data/test_closure.py`.
**CLI:** `python -m crux.data.run_ingest --limit N`.
**Table:** `projects(name PK, status, last_serial, n_seen, n_kept)`. Status: done/not_found/error.
**Flow:** Download top-5000 list → save to `snapshots/` → queue seeds → process loop (ingest + closure) → progress every 100 → Ctrl-C safe.
**Closure:** For each kept version, read Requires-Dist from PEP 658 cache (fallback JSON). Parse with `Requirement`, skip if marker False under MARKER_ENV (catches `extra == "..."` since `extra=""`) or if `req.url` set. Canonicalize dep names, queue unseen. Repeat until empty.
**Decisions:** All deps injectable (`_ingest`, `_fetch_top_list`, `conn`, `_sleep`) for offline tests. Sequential project processing, concurrent HTTP within each via shared semaphore. Resume skips status='done' projects but still discovers their deps for closure.
**Tests:** 4 acceptance tests — cycle (5-node A→B→C→D→E→A, each visited once), resume (3 pre-done, only 2 ingested), 404 (recorded not_found, run continues), extras-only (not followed). 26 total tests pass.

## P5: Pure parsing (2026-11-08)

**What exists:** `crux/data/parse.py`, `tests/data/test_parse.py`.
**parse_metadata(text):** Email parser → (requires_python, [Requires-Dist...]). Raises ValueError on missing Metadata-Version.
**parse_dep(line):** Requirement → check order: InvalidRequirement→BAD_REQUIREMENT, url→URL_DEP, marker mentions `extra`→EXTRA_DEP, marker False→MARKER_FALSE. Success: ('ok', canonical_name, SpecifierSet). Extras on dep (e.g. `requests[security]`) silently ignored, edge kept.
**python_ok(spec_str):** SpecifierSet vs TARGET_PYTHON. None/empty→True. Invalid→('drop', BAD_REQUIREMENT).
**parse_version(s):** Version or reason code (INVALID_VERSION, EPOCH, LOCAL_VERSION).
**Scope update:** Added "Known limitations" section for extras-on-dependency.
**Tests:** 27 parametrized parse_dep lines, 4 specifier checks, 4 parse_metadata, 7 python_ok, 5 parse_version. 47 tests pass. 73 total tests pass.

## P6: Clean (2026-11-09)

**What exists:** `crux/data/clean.py` (`clean(conn)`, CLI `python -m crux.data.clean`), `tests/data/test_clean.py`.
**Tables:** `releases(name, version, upload_time, is_prerelease, requires_python, filename, url, sha256)` and `deps(name, version, dep_name, specifier)`, both dropped and rebuilt each run. `drops` rows with stage `clean`/`edge` are rewritten; `ingest` rows kept.
**Decisions:** Metadata from PEP 658 cache, fallback to release JSON (`requires_dist: null` → no deps). Neither cached or no Metadata-Version → `no_metadata`. Invalid Requires-Python → `bad_requirement` at stage `clean`. `deps` has no PK: one row per kept line, so lines seen == deps + edge drops exactly. `is_prerelease` uses packaging (`.dev` counts).
**Tests:** 5 acceptance tests (idempotent, release conservation, line conservation, no-dep release present, values). 78 total pass.
**Limits:** 30-project pilot not yet verified: ingest fetches metadata sequentially (~0.5 req/s), boto3 alone is ~2000 versions. Smoke run on partial pilot (boto3, 88 releases) cleans without exceptions.

## P7: Graph API (2026-11-09)

**What exists:** `crux/data/graph.py` (`Graph.load(db_path, as_of)`, `Graph.from_dict(d)`, `versions`, `deps`, `allowed`, `dangling`; CLI prints load time + tracemalloc peak), `tests/data/test_graph.py`, `tests/fixtures/toy_abc.json`.
**from_dict format:** `{pkg: {version_str: [[dep_name, spec_str], ...]}}`. Names canonicalized on build and on query; `deps()` accepts `Version` or `str`.
**Decisions:** One shared `Version` per release (and shared `SpecifierSet` per spec string); `allowed()` = `frozenset(spec.filter(versions))` (packaging default pre-release rule), cached by `(dep, str(spec))`, misses counted in `allowed_misses`. `dangling()` = edges whose dep package has zero versions; such edges are kept. `as_of` is an aware datetime; NULL upload_time kept.
**Tests:** 7 acceptance tests. 85 total pass. **Limits:** pilot load time/memory pending the same ingest bottleneck (partial boto3-only DB: 0.01s, 0.2 MiB).

## P7b: Concurrent metadata fetch in ingest (2026-11-09)

**What changed:** `ingest_project` now fetches metadata for all kept versions of a project with `asyncio.gather`, bounded by the shared semaphore (default 10), instead of one-at-a-time. New test `test_metadata_fetched_concurrently` in `tests/data/test_ingest.py`.
**Why:** sequential fetching ran at ~0.5 req/s through the proxy (boto3 ≈ 2000 versions → hours). After the change: ~1660 fetches/min (~80× faster).
**Decisions:** rows still written in original version order (deterministic). A fetch that exhausts retries still fails the whole project, as before. Safe on one SQLite connection: single thread, each `store.put` completes between awaits.
**Tests:** 86 total pass. **Limits:** concurrency is per project; projects are still processed one after another.
