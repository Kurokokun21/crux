"""Acceptance tests for crux.data.parse — pure parsing, no I/O."""

import pytest
from packaging.version import Version

from crux.data.config import (
    BAD_REQUIREMENT,
    EPOCH,
    EXTRA_DEP,
    INVALID_VERSION,
    LOCAL_VERSION,
    MARKER_FALSE,
    URL_DEP,
)
from crux.data.parse import parse_dep, parse_metadata, parse_version, python_ok


# ── parse_dep: table-driven (27 lines) ──────────────────────────────

@pytest.mark.parametrize("line, tag, name_or_reason", [
    # bare name
    ("requests", "ok", "requests"),
    # ~=1.4
    ("requests~=1.4", "ok", "requests"),
    # ==1.*
    ("requests==1.*", "ok", "requests"),
    # !=1.5
    ("requests!=1.5", "ok", "requests"),
    # comma-joined ranges
    ("requests>=1.0,<2.0", "ok", "requests"),
    # marker true on Linux: sys_platform
    ('requests; sys_platform == "linux"', "ok", "requests"),
    # marker false on Linux: sys_platform win32
    ('requests; sys_platform == "win32"', "drop", MARKER_FALSE),
    # python_version marker true
    ('requests; python_version >= "3.8"', "ok", "requests"),
    # python_version marker false
    ('requests; python_version < "3.0"', "drop", MARKER_FALSE),
    # extra == "test"
    ('requests; extra == "test"', "drop", EXTRA_DEP),
    # URL requirement
    ("requests @ https://example.com/r.tar.gz", "drop", URL_DEP),
    # malformed
    ("!!!invalid!!!", "drop", BAD_REQUIREMENT),
    # upper-case name
    ("Requests>=1.0", "ok", "requests"),
    # underscore name
    ("my_package>=1.0", "ok", "my-package"),
    # pre-release specifier
    ("requests>=1.0a1", "ok", "requests"),
    # extras on dep — ignored, edge kept
    ("requests[security]>=1.0", "ok", "requests"),
    # os_name true (posix)
    ('requests; os_name == "posix"', "ok", "requests"),
    # os_name false (nt)
    ('requests; os_name == "nt"', "drop", MARKER_FALSE),
    # platform_machine true (x86_64)
    ('requests; platform_machine == "x86_64"', "ok", "requests"),
    # combined markers — both true
    ('requests; python_version >= "3.8" and sys_platform == "linux"',
     "ok", "requests"),
    # combined markers — one false
    ('requests; python_version >= "3.8" and sys_platform == "win32"',
     "drop", MARKER_FALSE),
    # extra mixed with other marker
    ('requests; extra == "dev" and python_version >= "3.8"',
     "drop", EXTRA_DEP),
    # implementation_name true
    ('requests; implementation_name == "cpython"', "ok", "requests"),
    # implementation_name false
    ('requests; implementation_name == "pypy"', "drop", MARKER_FALSE),
    # complex multi-clause specifier
    ("requests>=1.0,!=1.5,<3.0", "ok", "requests"),
    # dot-separated name
    ("zope.interface>=4.0", "ok", "zope-interface"),
    # platform_python_implementation true
    ('requests; platform_python_implementation == "CPython"',
     "ok", "requests"),
], ids=[
    "bare", "compatible", "wildcard", "not_equal", "range",
    "linux_true", "win32_false", "pyver_true", "pyver_false",
    "extra_test", "url_dep", "malformed", "uppercase", "underscore",
    "prerelease", "extras_on_dep", "posix_true", "nt_false",
    "x86_64_true", "combined_true", "combined_false", "extra_mixed",
    "cpython_true", "pypy_false", "complex_spec", "dotted_name",
    "cpython_impl_true",
])
def test_parse_dep(line, tag, name_or_reason):
    result = parse_dep(line)
    assert result[0] == tag, f"got {result}"
    assert result[1] == name_or_reason, f"got {result}"


# ── parse_dep: specifier values ──────────────────────────────────────

def test_parse_dep_specifier_compatible():
    _, _, spec = parse_dep("requests~=1.4")
    assert "1.4.0" in spec
    assert "1.5.0" in spec
    assert "2.0.0" not in spec


def test_parse_dep_specifier_wildcard():
    _, _, spec = parse_dep("requests==1.*")
    assert "1.5" in spec
    assert "2.0" not in spec


def test_parse_dep_specifier_range():
    _, _, spec = parse_dep("requests>=1.0,<2.0")
    assert "1.5" in spec
    assert "0.9" not in spec
    assert "2.0" not in spec


def test_parse_dep_specifier_bare_is_empty():
    _, _, spec = parse_dep("requests")
    assert str(spec) == ""
    assert "999.0" in spec


# ── parse_metadata ───────────────────────────────────────────────────

def test_parse_metadata_with_deps():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: test-pkg\n"
        "Version: 1.0.0\n"
        "Requires-Python: >=3.8\n"
        "Requires-Dist: requests>=2.0\n"
        "Requires-Dist: six\n"
    )
    rp, rd = parse_metadata(text)
    assert rp == ">=3.8"
    assert rd == ["requests>=2.0", "six"]


def test_parse_metadata_no_requires_dist():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: test-pkg\n"
        "Version: 1.0.0\n"
    )
    rp, rd = parse_metadata(text)
    assert rp is None
    assert rd == []


def test_parse_metadata_no_metadata_version():
    text = "Name: test-pkg\nVersion: 1.0.0\n"
    with pytest.raises(ValueError, match="Metadata-Version"):
        parse_metadata(text)


def test_parse_metadata_with_dynamic():
    text = (
        "Metadata-Version: 2.2\n"
        "Name: test-pkg\n"
        "Version: 1.0.0\n"
        "Dynamic: Requires-Dist\n"
    )
    rp, rd = parse_metadata(text)
    assert rp is None
    assert rd == []


# ── python_ok ────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec_str, expected", [
    (None, True),
    ("", True),
    (">=3.8", True),
    (">=3.13", False),
    (">=3.8,<4", True),
    ("<3.0", False),
], ids=["none", "empty", "gte38", "gte313", "range", "lt30"])
def test_python_ok(spec_str, expected):
    assert python_ok(spec_str) is expected


def test_python_ok_invalid_specifier():
    result = python_ok("not a specifier!")
    assert result == ("drop", BAD_REQUIREMENT)


# ── parse_version ────────────────────────────────────────────────────

def test_parse_version_valid():
    result = parse_version("1.0.0")
    assert isinstance(result, Version)
    assert str(result) == "1.0.0"


def test_parse_version_prerelease():
    result = parse_version("2.0.0a1")
    assert isinstance(result, Version)


def test_parse_version_epoch():
    assert parse_version("1!1.0.0") == EPOCH


def test_parse_version_local():
    assert parse_version("1.0.0+local") == LOCAL_VERSION


def test_parse_version_invalid():
    assert parse_version("invalid!") == INVALID_VERSION
