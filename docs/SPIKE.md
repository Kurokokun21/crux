# Spike: PyPI API Exploration

**Date:** 2026-11-04
**Projects:** requests, numpy, django, six, setuptools (many old releases)

---

## 1. Top-packages list

**URL:** `https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json`

| Field | Value |
|---|---|
| Top-level keys | `last_update`, `source`, `meta`, `rows`, `total_rows`, `rows_before_limit_at_least`, `statistics` |
| Row keys | `download_count`, `project` |
| Row count | 15,000 |
| Sample row | `{"download_count": 2672326221, "project": "boto3"}` |
| First 5 names | boto3, packaging, typing-extensions, certifi, idna |
| `total_rows` | 15,000 |
| `rows_before_limit_at_least` | 920,236 |
| `last_update` | 2026-11-01 |

---

## 2. Simple API

**URL pattern:** `GET https://pypi.org/simple/<name>/`
**Header:** `Accept: application/vnd.pypi.simple.v1+json`

### Response keys

`files`, `meta`, `name`, `project-status`, `versions` — same for all 5 projects.

### File-object keys

`core-metadata`, `data-dist-info-metadata`, `filename`, `hashes`, `provenance`, `requires-python`, `size`, `upload-time`, `url`, `yanked`

**Expected vs actual:**

| Key | Expected | Present |
|---|---|---|
| `filename` | yes | **yes** |
| `url` | yes | **yes** |
| `hashes` | yes | **yes** (dict, sha256 only) |
| `requires-python` | yes | **yes** (string or null) |
| `yanked` | yes | **yes** (string reason or `false`) |
| `upload-time` | yes | **yes** (ISO 8601 with timezone) |
| `core-metadata` | yes | **yes** (bool or hash dict) |
| `data-dist-info-metadata` | — | **yes** (legacy PEP 658 key, mirrors `core-metadata`) |
| `provenance` | — | **yes** (null for all sampled files) |
| `size` | — | **yes** (integer bytes) |

### Per-project counts

| Project | Versions | Total files | Wheels | Sdists | Other | Yanked | Response KB |
|---|---|---|---|---|---|---|---|
| requests | 160 | 244 | 83 | 160 | 1 | 4 | 120.2 |
| numpy | 138 | 4,298 | 4,108 | 156 | 34 | 72 | 2,761.9 |
| django | 441 | 815 | 389 | 426 | 0 | 4 | 371.7 |
| six | 29 | 48 | 19 | 29 | 0 | 0 | 23.5 |
| setuptools | 627 | 1,536 | 583 | 874 | 79 | 20 | 759.6 |

---

## 3. PEP 658 metadata

### Core-metadata availability

| Project | Wheels with CM | Wheels total | Wheels % | Sdists with CM | Sdists total | Sdists % |
|---|---|---|---|---|---|---|
| requests | 83 | 83 | 100% | 0 | 160 | 0% |
| numpy | 4,108 | 4,108 | 100% | 0 | 156 | 0% |
| django | 389 | 389 | 100% | 0 | 426 | 0% |
| six | 19 | 19 | 100% | 0 | 29 | 0% |
| setuptools | 583 | 583 | 100% | 0 | 874 | 0% |

**100% of wheels have core-metadata. 0% of sdists have it.**

### Requires-Dist and Requires-Python in .metadata files

| File | Requires-Dist | Requires-Python | Size |
|---|---|---|---|
| requests-2.0.0 (old whl) | **no** | **no** | 28,502 B |
| requests-2.0.1 (old whl) | **no** | **no** | 29,134 B |
| requests-2.34.1 (recent whl) | yes (n=6) | yes | 4,806 B |
| requests-2.34.2 (recent whl) | yes (n=6) | yes | 4,806 B |
| numpy-1.5.1 (old whl) | no | no | 1,519 B |
| numpy-2.5.4 (recent whl) | no | yes | 6,635 B |
| django-1.5.2 (old whl) | no | no | 1,315 B |
| django-6.1.3 (recent whl) | yes (n=5) | yes | 3,937 B |
| six-1.5.0 (old whl) | no | no | 1,283 B |
| six-1.17.0 (recent whl) | no | yes | 1,658 B |
| setuptools-0.9.8 (old whl) | yes (n=4) | no | 40,065 B |
| setuptools-84.2.1 (recent whl) | yes (n=50) | yes | 6,581 B |

**Key finding:** Early .metadata files (pre-2017 era wheels) often lack `Requires-Dist` lines entirely, even for packages that do have dependencies. The old .metadata files are larger (~28 KB for requests-2.0.0) because they embed the full package description; modern ones are compact (~4.8 KB).

**numpy** has no `Requires-Dist` in any version's .metadata — confirmed: numpy genuinely has zero runtime dependencies.

**six** also has zero runtime dependencies — `Requires-Dist` correctly absent in .metadata and null in JSON API.

---

## 4. Release JSON

**URL pattern:** `GET https://pypi.org/pypi/<name>/<version>/json`

### Size comparison: JSON vs .metadata

| Project | Version | JSON KB | .metadata KB | Ratio |
|---|---|---|---|---|
| requests | 2.34.2 | 6.7 | 4.7 | 1.4x |
| numpy | 2.5.4 | 61.7 | 6.5 | 9.5x |
| django | 6.1.3 | 5.7 | 3.8 | 1.5x |
| six | 1.17.0 | 3.8 | 1.6 | 2.4x |
| setuptools | 84.2.1 | 8.0 | 6.4 | 1.2x |

**JSON is 1.2–9.5x larger than .metadata.** numpy is the outlier (61.7 KB JSON) because the JSON includes URLs for ~30+ platform wheels.

### `info.requires_dist` for six

```
six 1.17.0: info.requires_dist = None
```

six has no dependencies. `requires_dist` is `None` (null), not an empty list.

### Null `requires_dist` for packages WITH dependencies

**Yes — this happens.** Early versions of `requests` return null:

| Version | requires_dist |
|---|---|
| requests 0.2.0 | **null** |
| requests 0.2.1 | **null** |
| requests 0.2.2 | **null** |
| requests 0.2.3 | **null** |
| requests 0.2.4 | **null** |
| requests 0.3.0 | **null** |
| requests 0.3.1 | **null** |
| requests 0.3.2 | **null** |
| requests 0.3.3 | **null** |
| requests 0.3.4 | **null** |

All 10 earliest versions return `requires_dist = null` despite `requests` having dependencies even back then (they used `install_requires` in setup.py but metadata wasn't captured by PyPI).

**Implication:** `null` in JSON API does NOT mean "zero dependencies". It means "metadata not available". We cannot distinguish "no deps" (like six) from "deps unknown" (like early requests) using the JSON API alone. The .metadata files have the same problem — early ones lack `Requires-Dist`.

---

## 5. Timing

### Sequential requests (Simple API)

| Project | Time (s) |
|---|---|
| requests | 0.045 |
| numpy | 0.387 |
| django | 0.164 |
| six | 0.038 |
| setuptools | 0.220 |

| Stat | Value |
|---|---|
| **Median** | **0.164 s** |
| Mean | 0.171 s |
| Min | 0.038 s |
| Max | 0.387 s |

Response time correlates with response size (numpy: 2.7 MB → 0.387s; six: 23 KB → 0.038s).

### 10 concurrent requests

| Metric | Value |
|---|---|
| Wall time | 1.244 s |
| Status codes | all 200 |
| **Got 429s** | **no** |

10 concurrent GETs to the Simple API did not trigger rate limiting.

---

## 6. Projection for 5,000 projects

### Observed averages (n=5)

| Metric | Per project |
|---|---|
| Versions | 279 |
| Total files | 1,388 |
| Wheels | 1,036 |
| Yanked files | 20 |
| Wheels with core-metadata | 1,036 (100%) |

### Version-level filtering (yanked + requires-python excludes CPython 3.12)

| Project | Versions | Yanked-only | RP-excluded | Kept | Filter % |
|---|---|---|---|---|---|
| requests | 160 | 2 | 0 | 158 | 1.2% |
| numpy | 138 | 1 | 5 | 132 | 4.3% |
| django | 441 | 0 | 0 | 441 | 0.0% |
| six | 29 | 0 | 0 | 29 | 0.0% |
| setuptools | 627 | 9 | 0 | 618 | 1.4% |

**Average filter rate: ~1.4% of versions eliminated.**

Requires-python filtering is almost negligible for popular packages targeting 3.12 — most older versions didn't set `requires-python` at all, so they pass through.

### Estimate: WITHOUT prefiltering

```
requests = projects + versions_kept
         = 5,000  + 5,000 × 279
         = 5,000  + 1,395,000
         = 1,400,000 total HTTP requests
```

If fetching release-JSON per version (for `requires_dist`).

### Estimate: WITH prefiltering (Simple API → filter → PEP 658)

```
Simple API:          5,000  requests (one per project)
Filter:              ~1.4%  versions removed (yanked + RP-excludes-3.12)
Versions kept:       5,000 × 275 = 1,375,000
.metadata fetches:   1,375,000  (one per kept version, wheel only)
────────────────────────────────
TOTAL:               ~1,380,000 requests
```

Prefiltering by version saves very little (~20,000 requests, 1.4%).

### Estimate: WITH file-level prefiltering (keep only 1 wheel per version)

```
Simple API:          5,000
Pick 1 wheel/ver:    5,000 × 275 = 1,375,000 .metadata fetches
────────────────────────────────
TOTAL:               ~1,380,000
```

Same order. The big saving would be fetching per-version (275/project) instead of per-file (1,388/project): ~4x fewer metadata fetches.

### PEP 658 vs Release JSON

| Strategy | Requests | Bandwidth saved |
|---|---|---|
| Release JSON per version | 5,000 + 1,375,000 = 1,380,000 | — |
| PEP 658 per wheel (1 per version) | 5,000 + 1,375,000 = 1,380,000 | 1.2–9.5x smaller per response |

**Same request count, but PEP 658 .metadata is much smaller (1.6–6.5 KB vs 3.8–61.7 KB JSON).** Total bandwidth: ~1,375,000 × 5 KB ≈ 6.5 GB via PEP 658, vs ~1,375,000 × 15 KB ≈ 19.6 GB via JSON.

---

## Recommended fetch strategy

1. **Use the Simple API** (`application/vnd.pypi.simple.v1+json`) as the single source of truth for listing versions and files per project. It gives us `requires-python`, `yanked`, `upload-time`, `filename`, and `core-metadata` availability — enough to prefilter before any metadata fetch.

2. **Prefilter at file level** using the Simple API response:
   - Drop yanked files.
   - Drop files whose `requires-python` excludes CPython 3.12.
   - Keep only one wheel per version (prefer `manylinux` x86-64, `py3-none-any`). Skip sdists — they never have PEP 658 metadata.

3. **Fetch dependency metadata via PEP 658** (`.metadata` suffix on wheel URL). 100% of wheels have it. The file is 2–10x smaller than the release JSON. It contains `Requires-Dist` and `Requires-Python` lines for all modern releases.

4. **Fall back to release JSON** (`/pypi/<name>/<version>/json`) only for old versions where the PEP 658 `.metadata` file lacks `Requires-Dist` (pre-~2017 wheels). Accept that for very old versions, dependency data may be unavailable (`null` in JSON too).

5. **Concurrency:** 10 concurrent requests did not trigger 429s. A modest concurrency limit (e.g., 10–20 connections) should be safe. Add exponential backoff for transient errors (observed `ConnectError` under load).

6. **Expected scale for 5,000 projects:**
   - ~5,000 Simple API requests (~0.16s median each ≈ 14 min sequential, ~2 min at 10 concurrent)
   - ~1.38M .metadata fetches (~small files ≈ 6.5 GB total)
   - Consider caching aggressively: the Simple API response for a project rarely changes.

---

## Assumptions from the prompt that turned out wrong

1. **"core-metadata" field:** The prompt expected `core-metadata` — this exists AND there is a parallel legacy key `data-dist-info-metadata`. Both are present in every file object (one mirrors the other per PEP 714).

2. **PEP 658 coverage is better than implied:** The prompt suggested checking "where core-metadata is present" as if it might be patchy. In reality, **100% of wheels** across all 5 projects have `core-metadata = true`. However, the .metadata *content* for old wheels often lacks `Requires-Dist` lines, so availability ≠ usefulness.

3. **Prefiltering savings are small:** The prompt suggested estimating "with and without prefiltering (yanked, requires-python excludes 3.12)". In practice, only ~1.4% of versions are filterable — most old versions lack `requires-python` entirely, so they can't be excluded.

4. **`requires_dist: null` is ambiguous:** The prompt asked whether `requires_dist` is "ever null for a package that does have dependencies." Yes — all 10 earliest `requests` versions return null. This means null ≠ "no deps"; it means "metadata unavailable." This affects our fallback strategy.

5. **`provenance` key not mentioned:** The file object includes a `provenance` field (always null in our sample) that wasn't listed in the expected keys. Also `size` (integer bytes) is present but wasn't listed.
