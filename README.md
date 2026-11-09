# Crux data layer

This is the data layer for Crux, a university project on search and problem solving.
It downloads PyPI metadata for popular projects into a local SQLite cache, cleans it,
and turns it into a dependency graph for a teammate-built solver.
The target is CPython 3.12 on Linux x86-64; after ingest everything reads the local cache only, and there is no ML anywhere.

## Quick start

Run from the repository root (paths such as `data/crux.db` are relative to it).

```bash
pip install -q "httpx>=0.28,<1" "packaging==26.0" "pytest>=8"
python -m crux.data.run_ingest --limit 30
python -m crux.data.clean
python -m crux.data.qa
python -m crux.data.graph
```

`run_ingest` fetches the first N top projects plus their full dependency closure, and skips
projects already `done`. `clean` prints row counts and drops by (stage, reason), `qa` prints
violations (`errors 0  warnings 0` on the pilot), and `graph` prints size, load time and peak memory.

## Data model

Everything lives in one SQLite file, `data/crux.db` (gitignored).

| Table | Written by | Columns |
|---|---|---|
| `raw` | `fetch` | `url` PK, `status` (200 or 404), `fetched_at`, `body` (zlib) |
| `projects` | `run_ingest` | `name` PK, `status` (done / not_found / error), `last_serial`, `n_seen`, `n_kept` |
| `files` | `ingest` | `name`, `version`, `filename`, `url`, `sha256`, `upload_time`, `requires_python`; PK (`name`, `version`), one chosen wheel per version |
| `releases` | `clean` | `name`, `version`, `upload_time`, `is_prerelease`, `requires_python`, `filename`, `url`, `sha256`; PK (`name`, `version`) |
| `deps` | `clean` | `name`, `version`, `dep_name`, `specifier` (string); no PK, one row per kept Requires-Dist line |
| `drops` | `ingest`, `clean` | `stage` (ingest / clean / edge), `reason`, `name`, `version`, `detail` |

Names are canonical (PEP 503). `ingest` rewrites its own `drops` rows per project;
`clean` drops and rebuilds `releases` and `deps` and rewrites `clean`/`edge` drops.

## Scope rules and reason codes

From `docs/SCOPE.md`. Every dropped item is counted with a reason code. Snapshot cutoff:
`2026-11-01T00:00:00Z` (`crux/data/config.py`).

| Rule | Reason code | Drops |
|---|---|---|
| File is yanked on PyPI | `yanked` | release |
| Version cannot be parsed by `packaging` | `invalid_version` | release |
| Version has a non-zero epoch (`1!1.0`) | `epoch` | release |
| Version has a local segment (`1.0+local`) | `local_version` | release |
| `requires-python` excludes CPython 3.12 | `py_version` | release |
| No wheel available (sdist only) | `no_wheel` | release |
| No PEP 658 `.metadata` available for wheel | `no_metadata` | release |
| File uploaded after snapshot cutoff | `after_cutoff` | release |
| Dependency marker is false for the target env | `marker_false` | dependency edge |
| Dependency requires an extra (`; extra == "test"`) | `extra_dep` | dependency edge |
| Dependency is a direct URL (`foo @ https://...`) | `url_dep` | dependency edge |
| Dependency requirement cannot be parsed | `bad_requirement` | dependency edge |

## Graph API example (run against the pilot DB)

```bash
python - <<'EOF'
from datetime import datetime, timezone
from packaging.specifiers import SpecifierSet
from crux.data.graph import Graph

g = Graph.load("data/crux.db")
print(g.versions("requests")[:3])
latest = g.versions("requests")[0]
print(g.deps("requests", latest))
print(len(g.allowed("urllib3", SpecifierSet("<3,>=1.26"))))
print(g.allowed("requestes", SpecifierSet("")))
print(len(g.dangling()))

old = Graph.load("data/crux.db", as_of=datetime(2024, 1, 1, tzinfo=timezone.utc))
print(old.versions("requests")[0])

toy = Graph.from_dict({"a": {"1.0": [["c", "<2.0"]]}, "c": {"1.0": [], "2.0": []}})
print(toy.allowed("c", SpecifierSet("<2.0")))
EOF
```

Output:

```
[<Version('2.34.2')>, <Version('2.34.1')>, <Version('2.34.0')>]
[('charset-normalizer', <SpecifierSet('<4,>=2')>), ('idna', <SpecifierSet('<4,>=2.5')>), ('urllib3', <SpecifierSet('<3,>=1.26')>), ('certifi', <SpecifierSet('>=2023.5.7')>)]
41
frozenset()
27
2.31.0
frozenset({<Version('1.0')>})
```

`versions()` is newest first (`[]` if unknown). `allowed()` uses packaging's default pre-release
rule and is cached by `(dep, str(spec))`. Edges to a package with no versions (here `requestes`,
a typo in someone's metadata) are kept, and `allowed()` returns `frozenset()`.
`from_dict` takes `{pkg: {version: [[dep, spec], ...]}}`.

## Results

**Pilot (30 seeds, cold run, from `docs/PILOT.md`):** 79 projects after closure (78 done,
1 not_found), 7,910 HTTP requests, 324 s ingest, 3.2 s clean, 31 s QA, DB 51.0 MB,
graph load 0.41 s with a 14.3 MiB peak, QA 0 errors and 0 warnings.

| | Count |
|---|---|
| Versions seen | 9,339 |
| Releases kept | 7,830 |
| Releases with zero dependencies | 1,920 |
| Requires-Dist lines seen | 45,298 |
| Dependency edges kept | 17,300 |
| Dangling edges | 27 |

| Stage | Reason | Count |
|---|---|---|
| ingest | no_wheel | 1,382 |
| ingest | yanked | 103 |
| ingest | after_cutoff | 13 |
| ingest | py_version | 11 |
| edge | extra_dep | 22,948 |
| edge | marker_false | 5,049 |
| edge | bad_requirement | 1 |

All other reason codes were 0 in the pilot. `docs/PILOT.md` projects about 6.5 h, 3.7 GB and about 1 GiB of graph memory for 5,000 seeds, and lists the assumptions behind that.

**Full run (5,000 seeds):** TODO: no full run has been done yet; there are no counts or drops for it.

## Known limitations

From `docs/PROGRESS.md` and `docs/SCOPE.md`:
- Extras requested on a dependency are ignored: `requests[security]>=2.0` is treated as
  `requests>=2.0`, so the extra's own dependencies are missing.
- `requires_dist: null` in the release JSON is ambiguous ("unknown", not "no dependencies"),
  but it is stored as zero dependencies. The pilot cross-check found one case (bcdoc 0.15.0)
  where JSON says null and `.metadata` lists 3 lines.
- Projects are ingested one at a time. Only metadata fetches within a project run concurrently (10).
- The QA check `conservation_lines` re-reads raw metadata, so it is the slowest check.

Noticed in the code:
- `ingest` falls back to the release JSON only when the `.metadata` fetch fails.
  `docs/SPIKE.md` found that old (pre-~2017) `.metadata` files often have no Requires-Dist
  at all; those releases are recorded with zero dependencies.
- One wheel is picked per version, so platform-specific wheels with different metadata are
  not compared. `yanked` reflects PyPI at fetch time, not at the cutoff date.
- A missing `upload-time` counts as before the cutoff, and an unparseable `requires-python` in the
  Simple API is let through at ingest (`clean` then checks the metadata's own value).
- `run_ingest` finds dependencies for the closure with its own parser
  (`_extract_dep_names`), separate from `parse.parse_dep` used by `clean`.
- Only 200 and 404 responses are cached. If a request still fails after 5 attempts, that project
  is marked `error`.
- `pip install -e .` fails (setuptools finds top-level `data`, `crux`, `snapshots`); use the line above.
- `USER_AGENT` in `config.py` still has a placeholder contact (`crux-team@example.com`).

## Reproducing the snapshot

The snapshot is defined by `SNAPSHOT_DATE` / `SNAPSHOT_CUTOFF` in `crux/data/config.py`
(2026-11-01) plus:
- The seed list `snapshots/top5000-2026-11-01.json`, 5,000 names. `run_ingest` writes it on
  first run and reads it on later runs, even if the online list has changed.
- The raw cache in `data/crux.db`. With the same DB, every step runs with no network access.

```bash
python -c "import json; print(len(json.load(open('snapshots/top5000-2026-11-01.json'))))"
```

prints `5000`. A fresh DB on a later date can differ: files can be yanked or deleted, and the
cutoff only excludes later uploads. TODO: `snapshots/` is not committed yet. Without it, a fresh clone downloads the current top
list instead of the 2026-11-01 one. Without a copy of `data/crux.db`, the pilot can't be
reproduced byte for byte.

## Running tests

```bash
python -m pytest -q
```

99 tests, all offline (`httpx.MockTransport`; in-memory or temp-file DBs; fixtures in `tests/fixtures/`).

## Layout

```
crux/data/config.py      constants: cutoff, target env, marker env, reason codes
crux/data/store.py       raw HTTP cache (SQLite, zlib bodies)
crux/data/fetch.py       cached async fetch with retries and backoff
crux/data/ingest.py      one project: Simple API -> filter -> pick wheel -> metadata
crux/data/run_ingest.py  batch ingest with dependency closure and resume (CLI)
crux/data/parse.py       pure parsing: metadata, Requires-Dist, requires-python, versions
crux/data/clean.py       files + raw -> releases, deps, drops (CLI)
crux/data/graph.py       Graph API for the solver (CLI prints load stats)
crux/data/qa.py          SQL checks over the cleaned DB (CLI)
tests/data/              acceptance tests, one file per module
tests/fixtures/          offline fixtures
scripts/                 throwaway spike scripts (spike.py, spike2.py)
snapshots/               seed list for the snapshot
docs/                    SCOPE, SPIKE, PROGRESS, PILOT
data/                    local DB (gitignored)
```
