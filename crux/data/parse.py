"""Pure parsing — no I/O, no database.

Functions for parsing PEP 658 metadata, dependency lines,
requires-python specifiers, and version strings.
"""

import re
from email.parser import Parser

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from crux.data.config import (
    BAD_REQUIREMENT,
    EPOCH,
    EXTRA_DEP,
    INVALID_VERSION,
    LOCAL_VERSION,
    MARKER_ENV,
    MARKER_FALSE,
    TARGET_PYTHON,
    URL_DEP,
)

_EXTRA_RE = re.compile(r"\bextra\b")


# ── metadata ─────────────────────────────────────────────────────────

def parse_metadata(text):
    """Parse PEP 658 metadata text.

    Returns (requires_python | None, list[str] of Requires-Dist lines).
    Raises ValueError if there is no Metadata-Version header.
    """
    msg = Parser().parsestr(text)
    if msg.get("Metadata-Version") is None:
        raise ValueError("no Metadata-Version header")
    rp = msg.get("Requires-Python")          # None if absent
    rd = msg.get_all("Requires-Dist") or []  # [] if absent
    return rp, rd


# ── dependency line ──────────────────────────────────────────────────

def parse_dep(line):
    """Parse one Requires-Dist line.

    Returns ('ok', canonical_name, SpecifierSet)
         or ('drop', reason_code).
    """
    try:
        req = Requirement(line)
    except (InvalidRequirement, Exception):
        return ("drop", BAD_REQUIREMENT)

    if req.url:
        return ("drop", URL_DEP)

    if req.marker is not None:
        marker_str = str(req.marker)
        if _EXTRA_RE.search(marker_str):
            return ("drop", EXTRA_DEP)
        try:
            if not req.marker.evaluate(MARKER_ENV):
                return ("drop", MARKER_FALSE)
        except Exception:
            return ("drop", BAD_REQUIREMENT)

    name = canonicalize_name(req.name)
    return ("ok", name, req.specifier)


# ── requires-python ──────────────────────────────────────────────────

def python_ok(spec_str):
    """Check whether *spec_str* allows TARGET_PYTHON.

    Returns True / False, or ('drop', BAD_REQUIREMENT) for invalid input.
    None or empty string → True.
    """
    if not spec_str:
        return True
    try:
        spec = SpecifierSet(spec_str)
    except InvalidSpecifier:
        return ("drop", BAD_REQUIREMENT)
    return TARGET_PYTHON in spec


# ── version string ───────────────────────────────────────────────────

def parse_version(s):
    """Parse a version string.

    Returns a Version on success, or a reason-code string
    (INVALID_VERSION, EPOCH, LOCAL_VERSION).
    """
    try:
        ver = Version(s)
    except InvalidVersion:
        return INVALID_VERSION
    if ver.epoch > 0:
        return EPOCH
    if ver.local is not None:
        return LOCAL_VERSION
    return ver
