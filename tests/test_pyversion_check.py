"""Tests for the early Python-version gate (app/utils/pyversion_check.py).

Covers both halves of the seam:
  * the pure message function returns None on supported versions and an
    actionable message on unsupported ones, and
  * the bounds stay in sync with pyproject.toml.
"""

import re
from pathlib import Path

import pytest

from app.utils.pyversion_check import (
    MIN_PYTHON,
    MAX_PYTHON_EXCLUSIVE,
    format_python_version_error,
)


class FakeVersion(tuple):
    """A stand-in for sys.version_info (indexable major, minor, micro)."""


def test_python_39_is_rejected_with_actionable_message():
    # The exact scenario reported on a freshly-provisioned macOS (system 3.9.6).
    msg = format_python_version_error((3, 9, 6))
    assert msg is not None
    # Must name the requirement and give a concrete remediation.
    assert "3.10" in msg
    assert "3.9.6" in msg
    # Must point at the real cause and a fix, not a generic dependency error.
    assert "mcp" in msg
    assert "pip install" in msg


def test_supported_versions_return_none():
    # Every version in the declared range must pass cleanly.
    for minor in range(MIN_PYTHON[1], MAX_PYTHON_EXCLUSIVE[1]):
        assert format_python_version_error((3, minor, 0)) is None, (
            f"3.{minor} should be supported"
        )


def test_lower_boundary_is_inclusive():
    assert format_python_version_error(MIN_PYTHON + (0,)) is None


def test_too_new_is_rejected():
    too_new = (MAX_PYTHON_EXCLUSIVE[0], MAX_PYTHON_EXCLUSIVE[1], 0)
    msg = format_python_version_error(too_new)
    assert msg is not None
    assert "3.15" in msg


def test_missing_micro_component_is_tolerated():
    # sys.version_info always has micro, but callers may pass a 2-tuple.
    assert format_python_version_error((3, 9)) is not None
    assert format_python_version_error((3, 12)) is None


def test_bounds_match_pyproject():
    """The guard's floor must equal pyproject's declared minimum, or the
    build-time / import-time message and pip's Requires-Python check disagree."""
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    m = re.search(r'python\s*=\s*">=\s*(\d+)\.(\d+)\s*,\s*<\s*(\d+)\.(\d+)"', pyproject)
    assert m, "could not find python constraint in pyproject.toml"
    declared_min = (int(m.group(1)), int(m.group(2)))
    declared_max = (int(m.group(3)), int(m.group(4)))
    assert declared_min == MIN_PYTHON
    assert declared_max == MAX_PYTHON_EXCLUSIVE
