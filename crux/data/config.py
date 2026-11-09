"""Constants for the Crux data layer."""

from datetime import datetime, timezone

# ── Snapshot ─────────────────────────────────────────────────────────
SNAPSHOT_DATE = "2026-11-01"
SNAPSHOT_CUTOFF = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)

# ── Network ──────────────────────────────────────────────────────────
USER_AGENT = "crux/0.1 (university project; contact: crux-team@example.com)"

# ── Storage ──────────────────────────────────────────────────────────
DB_PATH = "data/crux.db"

# ── Filtering ────────────────────────────────────────────────────────
MAX_VERSIONS_PER_PROJECT = None
TARGET_PYTHON = "3.12"

# ── Marker environment: CPython 3.12, Linux x86-64 ──────────────────
MARKER_ENV = {
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "implementation_name": "cpython",
    "implementation_version": "3.12.0",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "platform_release": "",
    "platform_version": "",
    "platform_python_implementation": "CPython",
    "sys_platform": "linux",
    "os_name": "posix",
    "extra": "",
}

# ── Reason codes (why a release or dependency edge was dropped) ──────
YANKED = "yanked"
INVALID_VERSION = "invalid_version"
EPOCH = "epoch"
LOCAL_VERSION = "local_version"
PY_VERSION = "py_version"
NO_WHEEL = "no_wheel"
NO_METADATA = "no_metadata"
AFTER_CUTOFF = "after_cutoff"
MARKER_FALSE = "marker_false"
EXTRA_DEP = "extra_dep"
URL_DEP = "url_dep"
BAD_REQUIREMENT = "bad_requirement"
