# Pilot: 30 seeds (2026-11-09)

Cold run: empty `data/crux.db`, `run_ingest --limit 30` → `clean` → `qa`.
Snapshot cutoff 2026-11-01, target CPython 3.12 / Linux x86-64.
Requests were counted with an httpx response hook on the client passed to
`run_ingest.run`, so retries are included.

## Run

| Metric | Value |
|---|---|
| Seeds | 30 (top of `snapshots/top5000-2026-11-01.json`) |
| Projects after closure | 79 (78 done, 1 not_found: `requestes`) |
| Closure projects also in the top-5000 list | 69 of 79 |
| HTTP requests | 7,910 (7,909 × 200, 1 × 404, 0 retries) |
| Requests by type | 1 top list, 79 Simple API, 7,829 `.metadata`, 1 release JSON |
| Ingest time | 324 s (≈ 24 req/s, semaphore 10) |
| Clean time | 3.2 s |
| QA time | 31 s |
| Graph load (`python -m crux.data.graph`) | 0.41 s, peak 14.3 MiB (tracemalloc) |
| DB size after ingest | 45.2 MB (raw bodies 31.4 MB zlib-compressed) |
| DB size after clean | 51.0 MB |
| QA result | 0 errors, 0 warnings |

An earlier warm-cache run of the same pilot took 417 s, so network speed varies
by about ±30% between runs.

## Versions

| Stage | Count |
|---|---|
| Versions seen (Simple API, done projects) | 9,339 |
| Kept by ingest (`files`) | 7,830 |
| Kept by clean (`releases`) | 7,830 |
| Releases with zero dependencies | 1,920 |
| Requires-Dist lines seen | 45,298 |
| Dependency edges kept (`deps`) | 17,300 |
| Dangling edges (dependency has 0 versions) | 27 (`distribute` ×15, `markerlib` ×10, `requestes` ×2) |

## Drops by reason

| Stage | Reason | Count |
|---|---|---|
| ingest | no_wheel | 1,382 |
| ingest | yanked | 103 |
| ingest | after_cutoff | 13 |
| ingest | py_version | 11 |
| ingest | invalid_version / epoch / local_version / no_metadata | 0 |
| clean | no_metadata / py_version / bad_requirement | 0 |
| edge | extra_dep | 22,948 |
| edge | marker_false | 5,049 |
| edge | bad_requirement | 1 (boto3 1.2.0: `botocore>=1.3.0,<1.4.0',`) |
| edge | url_dep | 0 |

Conservation: 9,339 seen = 7,830 kept + 1,509 ingest drops; 7,830 files = 7,830 releases + 0
clean drops; 45,298 lines = 17,300 deps + 27,998 edge drops.

## Projection for 5,000 seeds

| Quantity | Pilot per project | × 5,700 projects |
|---|---|---|
| HTTP requests | 100 | ≈ 570,000 |
| Ingest time | 4.1 s | ≈ 23,400 s ≈ 6.5 h |
| DB size | 646 KB | ≈ 3.7 GB |
| Releases | 100 | ≈ 570,000 |
| Dependency edges | 222 | ≈ 1.27 M |
| Clean time | 0.04 s | ≈ 4 min |
| QA time | 0.39 s | ≈ 37 min |
| Graph load / peak memory | 5 ms / 0.18 MiB | ≈ 30 s / ≈ 1 GiB |

Assumptions:
1. **Project count ≈ 5,700.** In the pilot, 10 of 79 closure projects (13%) were outside the
   top-5000. Applying that ratio to 5,000 seeds gives about 5,700. Because the closure grows
   less than linearly as the seeds already cover popular dependencies, 5,000 to 6,500 is a
   plausible range.
2. **Per-project cost equals the pilot average.** That's probably an overestimate: the top 30
   include very large histories (boto3 and botocore have about 2,000 versions each), and the
   pilot averaged 120 versions seen per project. A typical top-5000 project likely has fewer.
3. **Throughput stays at ≈ 24 req/s** with concurrency 10 and no PyPI rate limiting. The pilot
   saw 0 retries, so no 429s. Projects are still processed one at a time; only metadata
   fetches within a project run concurrently.
4. **Everything else scales linearly** with releases: clean, QA and graph memory. Graph peak
   memory of about 1 GiB is the number most worth watching for the solver team.

## Cross-check: `.metadata` vs release JSON (20 releases)

Method: 20 projects sampled with `random.Random(0)` from the pilot, one random release each.
Dependency lists come from the cached PEP 658 `.metadata` and a fresh, uncached GET of
`/pypi/<name>/<version>/json`. Lines were compared as multisets after
`str(Requirement(line))` normalization.

| Result | Releases |
|---|---|
| Identical, byte-for-byte | 17 |
| Identical after normalization (formatting/order only) | 2 (setuptools-rust 1.10.0, pyparsing 3.2.0b1) |
| **Disagree** | **1: bcdoc 0.15.0** |

- **bcdoc 0.15.0:** `.metadata` lists 3 lines (`docutils>=0.10`, `six<2.0.0,>=1.8.0`,
  `ordereddict==1.1; extra == ":python-version=="2-6""`), but the JSON has
  `requires_dist: null`. The JSON loses 2 real dependencies. This supports the
  PEP-658-first strategy and the P0 finding that JSON `null` means "unknown", not
  "no dependencies".
- **Requires-Python:** identical in all 20, except that 3 releases have no value in
  `.metadata` (None) and `""` in the JSON. Both mean "no constraint".

Reported only; nothing changed.

## Eyeball: latest final release of 5 projects

Only `requests` is in the 30-seed pilot. These 5 were ingested separately into a scratch DB
with the same code and cutoff, then cleaned. Kept edges only; extras and false markers are dropped.

| Project | Latest | Requires-Python | Kept dependencies | Dropped edges |
|---|---|---|---|---|
| requests | 2.34.2 | >=3.10 | certifi>=2023.5.7, charset-normalizer<4,>=2, idna<4,>=2.5, urllib3<3,>=1.26 | extra_dep 2 |
| flask | 3.1.3 | >=3.9 | blinker>=1.9.0, click>=8.1.3, itsdangerous>=2.2.0, jinja2>=3.1.2, markupsafe>=2.1.1, werkzeug>=3.1.0 | extra_dep 2, marker_false 1 |
| pandas | 3.1.0 | >=3.11 | numpy>=2.0.2, python-dateutil>=2.9.0 | extra_dep 77, marker_false 3 |
| scikit-learn | 1.9.1 | >=3.11 | joblib>=1.4.0, narwhals>=2.0.1, numpy>=1.24.1, scipy>=1.10.0, threadpoolctl>=3.5.0 | none |
| fastapi | 0.162.0 | >=3.10 | annotated-doc>=0.0.2, opentelemetry-api>=1.44.0, pydantic>=2.9.0, starlette>=0.46.0, typing-extensions>=4.8.0, typing-inspection>=0.4.2 | extra_dep 38 |

Kept releases: requests 81, flask 46, pandas 23, scikit-learn 22, fastapi 359.
pandas and scikit-learn keep few releases because older versions have no cp312 wheel.
