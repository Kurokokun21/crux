# Scope

Target: **CPython 3.12, Linux x86-64.** Everything downstream reads a local
cache only. Unit tests never use the network.

## Filtering rules

| Rule | Reason code | Drops |
|---|---|---|
| File is yanked on PyPI | `yanked` | release |
| Version string cannot be parsed by `packaging` | `invalid_version` | release |
| Version has a non-zero epoch (`1!1.0`) | `epoch` | release |
| Version has a local segment (`1.0+local`) | `local_version` | release |
| `requires-python` excludes CPython 3.12 | `py_version` | release |
| No wheel available (sdist only) | `no_wheel` | release |
| No PEP 658 `.metadata` available for wheel | `no_metadata` | release |
| File uploaded after snapshot cutoff | `after_cutoff` | release |
| Dependency marker evaluates to false for target env | `marker_false` | dependency edge |
| Dependency requires an extra (`Requires-Dist: foo ; extra == "test"`) | `extra_dep` | dependency edge |
| Dependency is a direct-URL requirement (`foo @ https://...`) | `url_dep` | dependency edge |
| Dependency requirement string cannot be parsed | `bad_requirement` | dependency edge |

Dropped items are **counted** with their reason code for QA reporting.

## Known limitations

- **Extras requested on a dependency are ignored.** A requirement like
  `requests[security]>=2.0` is treated as `requests>=2.0` — the extra
  `security` is silently dropped and the dependency edge is kept. This means
  the resolver will not see the transitive dependencies that the extra would
  pull in (e.g. `pyOpenSSL`).
