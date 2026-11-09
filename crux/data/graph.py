"""Read-only dependency graph for the solver.

CLI: python -m crux.data.graph   (prints load time and peak memory)
"""

import sqlite3
from datetime import datetime

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version


class Graph:
    def __init__(self):
        self._versions = {}   # pkg -> list[Version], newest first
        self._deps = {}       # (pkg, Version) -> list[(dep_name, SpecifierSet)]
        self._allowed = {}    # (dep_name, str(spec)) -> frozenset[Version]
        self.allowed_misses = 0

    # ── construction ─────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, d):
        """d = {pkg: {version_str: [[dep_name, spec_str], ...]}}."""
        rels = [(p, v) for p, vs in d.items() for v in vs]
        edges = [(p, v, dep, spec)
                 for p, vs in d.items() for v, es in vs.items()
                 for dep, spec in es]
        return cls._build(rels, edges)

    @classmethod
    def load(cls, db_path, as_of=None):
        """Load releases/deps; drop releases uploaded after *as_of*."""
        conn = sqlite3.connect(db_path)
        try:
            rels = []
            for name, ver, ut in conn.execute(
                    "SELECT name, version, upload_time FROM releases"):
                if as_of is not None and ut and \
                        datetime.fromisoformat(ut) > as_of:
                    continue
                rels.append((name, ver))
            edges = conn.execute(
                "SELECT name, version, dep_name, specifier FROM deps"
            ).fetchall()
        finally:
            conn.close()
        return cls._build(rels, edges)

    @classmethod
    def _build(cls, rels, edges):
        g = cls()
        obj = {}   # (pkg, ver_str) -> shared Version
        for pkg, ver in rels:
            pkg = canonicalize_name(pkg)
            v = Version(ver)
            obj[(pkg, ver)] = v
            g._versions.setdefault(pkg, []).append(v)
            g._deps[(pkg, v)] = []
        for vs in g._versions.values():
            vs.sort(reverse=True)
        specs = {}  # share SpecifierSet objects too
        for pkg, ver, dep, spec in edges:
            v = obj.get((canonicalize_name(pkg), ver))
            if v is None:          # release excluded (as_of) or unknown
                continue
            s = specs.get(spec)
            if s is None:
                s = specs[spec] = SpecifierSet(spec)
            g._deps[(canonicalize_name(pkg), v)].append(
                (canonicalize_name(dep), s))
        return g

    # ── queries ──────────────────────────────────────────────────────

    def versions(self, pkg):
        return self._versions.get(canonicalize_name(pkg), [])

    def deps(self, pkg, version):
        if isinstance(version, str):
            version = Version(version)
        return self._deps.get((canonicalize_name(pkg), version), [])

    def allowed(self, dep_name, specifier):
        dep_name = canonicalize_name(dep_name)
        key = (dep_name, str(specifier))
        hit = self._allowed.get(key)
        if hit is None:
            self.allowed_misses += 1
            hit = self._allowed[key] = frozenset(
                specifier.filter(self.versions(dep_name)))
        return hit

    def dangling(self):
        return [(pkg, v, dep)
                for (pkg, v), es in self._deps.items()
                for dep, _ in es if not self._versions.get(dep)]


if __name__ == "__main__":
    import time
    import tracemalloc

    from crux.data.config import DB_PATH

    tracemalloc.start()
    t0 = time.perf_counter()
    g = Graph.load(DB_PATH)
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    n_rel = sum(len(v) for v in g._versions.values())
    n_edge = sum(len(e) for e in g._deps.values())
    print(f"packages {len(g._versions)}  releases {n_rel}  edges {n_edge}"
          f"  dangling {len(g.dangling())}")
    print(f"load {dt:.2f}s  peak {peak / 2**20:.1f} MiB")
