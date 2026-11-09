"""Acceptance tests for crux.data.config."""

from packaging.markers import Marker

from crux.data.config import MARKER_ENV


# ── keys the task requires ───────────────────────────────────────────
EXPECTED_MARKER_KEYS = {
    "python_version",
    "python_full_version",
    "implementation_name",
    "implementation_version",
    "platform_system",
    "platform_machine",
    "platform_release",
    "platform_version",
    "platform_python_implementation",
    "sys_platform",
    "os_name",
    "extra",
}


def test_marker_env_has_all_keys():
    assert set(MARKER_ENV.keys()) == EXPECTED_MARKER_KEYS


def test_marker_env_no_host_values():
    """Values are hardcoded for CPython 3.12 Linux x86-64, not read from host."""
    assert MARKER_ENV["sys_platform"] == "linux"
    assert MARKER_ENV["platform_system"] == "Linux"
    assert MARKER_ENV["platform_machine"] == "x86_64"
    assert MARKER_ENV["implementation_name"] == "cpython"
    assert MARKER_ENV["platform_python_implementation"] == "CPython"
    assert MARKER_ENV["python_version"] == "3.12"
    assert MARKER_ENV["os_name"] == "posix"


def test_marker_sys_platform_not_win32():
    m = Marker("sys_platform == 'win32'")
    assert m.evaluate(MARKER_ENV) is False


def test_marker_python_version_gte_38():
    m = Marker("python_version >= '3.8'")
    assert m.evaluate(MARKER_ENV) is True


def test_marker_extra_test_is_false():
    m = Marker("extra == 'test'")
    assert m.evaluate(MARKER_ENV) is False
