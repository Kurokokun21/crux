# Handover to the resolver-engine team

What this layer gives you, what you may rely on, and what it deliberately
leaves out. Read the "Caveats" section before writing anything in the report
that compares this resolver to pip or uv.

## Install

```bash
pip install -e ".[dev]"
python -m pytest -q          # 111 tests, all offline
```

You then `from crux.data.graph import Graph` from anywhere. You do **not** need
network access, and you should never need to touch `fetch`, `ingest`,
`run_ingest` or `clean` — those build the database; you consume it.

## The API

```python
from crux.data.graph import Graph

g = Graph.load("data/crux.db")              # or as_of=aware_datetime
g = Graph.from_dict({pkg: {ver: [[dep, spec], ...]}})   # for unit tests
```

| Call | Returns | Cost |
|---|---|---|
| `versions(pkg)` | `list[Version]`, newest first, `[]` if unknown | O(1) lookup |
| `deps(pkg, version)` | `list[(dep_name, SpecifierSet)]` | O(1) lookup |
| `candidates(dep, spec)` | `tuple[Version, ...]`, newest first | O(versions) on first call, O(1) after |
| `allowed(dep, spec)` | `frozenset[Version]` | same cache as `candidates` |
| `dependents(dep)` | `tuple[(pkg, Version, SpecifierSet), ...]` | O(all edges) on first call only, then O(1) |
| `dangling()` | `list[(pkg, Version, dep_name)]` | O(all edges) |
| `collisions` | `list[(pkg, version_str)]` | attribute |
| `allowed_misses` | `int`, cache misses so far | attribute |

`pkg`, `dep` and `version` are accepted in any spelling: names are
canonicalized on the way in, `deps()` takes a `Version` or a `str`, and
`candidates()`/`allowed()` take a `SpecifierSet` or a `str`.

### Invariants you may rely on

- **Names are canonical** (PEP 503) everywhere in the database and on every
  return value. `Flask`, `flask` and `FLASK` all reach the same node.
- **One shared `Version` object per release**, and one shared `SpecifierSet`
  per distinct specifier string. `is` comparison is valid and hashing is cheap.
  Do not construct your own `Version` to use as a dict key alongside these —
  it will be equal and hash the same, so it works, but you lose the sharing.
- **`versions()` is sorted newest first.** `candidates()` preserves that order,
  so `candidates(...)[0]` is "newest version satisfying this requirement" and
  that is the natural value-ordering default.
- **`str(SpecifierSet)` is sorted**, so `">=1.2,<2"` and `"<2,>=1.2"` are the
  same cache key and the same stored `deps.specifier` text. Specifier strings
  are stable identifiers; you can memoize on them.
- **Edges to packages with no versions are kept.** `deps()` will hand you a
  dependency whose `candidates()` is empty — that is an immediate dead end, not
  a missing-data bug. `dangling()` lists them all; the pilot has 27, from
  sdist-only packages (`distribute`, `markerlib`) and one typo in someone's
  published metadata (`requestes`).
- **`allowed()` and `candidates()` apply packaging's default pre-release rule**
  (pre-releases are excluded unless the specifier itself mentions one).
  `versions()` does **not**. Go through `candidates()` everywhere; if you
  iterate `versions()` for root requirements you will silently get different
  pre-release behaviour than for transitive ones.
- **`collisions` should always be empty.** It is non-empty only if two releases
  of one package have different version *strings* that are equal under PEP 440
  (`"1.0"` and `"1.0.0"`). The database is keyed on the string, this graph is
  keyed on the `Version` object, so such releases would collapse into one node
  with merged dependency edges. PyPI normalizes versions, so this should not
  happen; `python -m crux.data.qa` has a `version_collision` check for it.
  If you ever see a non-empty list, stop and tell the data owner.

### What is not in the graph

These are the things you will reach for and not find. Ask the data owner before
building workarounds, since some are a few lines to add.

- **No `requires_python` and no `is_prerelease` on the loaded graph.** Both are
  in the `releases` table. Pre-release status is available for free from the
  `Version` object (`v.is_prerelease`).
- **No drop reasons.** When your resolver reports "no version of `urllib3`
  satisfies these constraints", the *reason* a plausible version is absent
  (`no_wheel`, `py_version`, `yanked`, `after_cutoff`) is in the `drops` table
  and is not reachable through `Graph`. This is the raw material the
  explanation module needs, so agree on an interface for it early rather than
  querying SQL from inside the solver.
- **No integer version ids.** If you want bitset domains or a SAT encoding
  later, you will want a dense `(pkg, version) -> int` mapping.
- **No subset loading.** `Graph.load` reads the whole database eagerly. Fine at
  pilot scale (0.41 s, 14.3 MiB); projected at about 1 GiB for the full
  5,000-seed dataset, which is the number to watch once that run exists.

## What you can test against today

| | Pilot (30 seeds) |
|---|---|
| Packages | 76 |
| Releases | 7,830 |
| Dependency edges | 17,300 |
| Dangling edges | 27 |
| Load time / peak memory | 0.41 s / 14.3 MiB |

`boto3` and `botocore` are in there with roughly 2,000 versions each. That is
the pathological backtracking case — the one that makes pip spin for minutes —
so it is the most valuable thing in the pilot for benchmarking, and worth
building a dedicated test case around.

`tests/fixtures/toy_abc.json` is already an unsatisfiable instance: `a` requires
`c<2.0`, `b` requires `c>=3.1`, so `a` and `b` together cannot be satisfied.
Use it as the first target for the explanation module — the correct answer is
two lines, and nothing about the rest of the graph is relevant to it.

**There is no full 5,000-seed run yet**, so there is no large dataset to
benchmark on. The run takes about 6.5 hours of wall clock and has to happen
before any performance claim in the report means anything.

## Caveats that affect what you can claim

This graph is **easier than real-world dependency resolution**, in ways that
have to be stated plainly whenever the project compares itself to a real
resolver.

- **Extras are dropped entirely.** 22,948 of the pilot's 45,298 `Requires-Dist`
  lines — over half the data — were dropped as `extra_dep`. A requirement like
  `requests[security]>=2.0` is also silently treated as `requests>=2.0`, so the
  extra's own dependencies are missing. Extras are a major source of real
  conflicts, so their absence makes instances easier.
- **Sdist-only releases are dropped**: 1,382 of 9,339 versions (15%). Version
  histories are badly truncated for compiled packages — `pandas` keeps 23
  releases and `scikit-learn` 22, where the real projects have far more. Fewer
  candidates per variable means a smaller search space.
- **`requires_dist: null` is recorded as zero dependencies** though on PyPI it
  means "unknown". The 20-release cross-check in `docs/PILOT.md` found one case
  (`bcdoc` 0.15.0) where this loses two real dependencies.
- **One wheel per version**, so platform-specific wheels whose metadata differs
  are never compared.
- **`yanked` reflects PyPI at fetch time**, not as of the snapshot cutoff, so
  the snapshot is not a perfect point-in-time view.
- **The dataset is not yet reproducible from a clone**: `snapshots/` is not
  committed and `data/crux.db` is gitignored, so a fresh clone downloads
  *today's* top-5000 list and builds a different dataset. Get the pilot
  database from the data owner rather than rebuilding it, so that everyone
  benchmarks on the same bytes.

## Conventions if you change this layer

Every discarded release and dependency edge is counted in the `drops` table
with a reason code, and `python -m crux.data.qa` asserts conservation: versions
seen equals releases kept plus drops, and `Requires-Dist` lines seen equals
`deps` rows plus edge drops, exactly. If you change what gets kept, update
`docs/SCOPE.md`, re-run `clean` and `qa`, and update the counts in
`docs/PILOT.md` and `README.md`. Tests stay offline — `httpx.MockTransport`,
in-memory or temp-file databases, fixtures in `tests/fixtures/`.
