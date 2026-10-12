"""Shared, dependency-free Python version gate for Ziya.

This module is intentionally self-contained (stdlib only) so it can be imported
and executed at the very earliest point of package import — before any
third-party dependency (e.g. ``mcp``) is touched. Those dependencies declare
``Requires-Python >= 3.10`` and are simply absent on older interpreters, which
produces confusing errors like::

    ERROR: Could not find a version that satisfies the requirement mcp>=1.23.0

macOS ships a system Python 3.9 (``/usr/bin/python3``), which is a common cause
on freshly-provisioned machines. Failing fast here turns that into an
actionable message.

The supported range mirrors ``pyproject.toml`` (``python = ">=3.10,<3.15"``).
Keep the bounds below in sync with that file.
"""

import sys

# Keep in sync with pyproject.toml: python = ">=3.10,<3.15"
MIN_PYTHON = (3, 10)
MAX_PYTHON_EXCLUSIVE = (3, 15)


def _fmt(bound):
    return ".".join(str(p) for p in bound)


def format_python_version_error(version_info=None):
    """Return an actionable error message if ``version_info`` is unsupported.

    Returns ``None`` when the running (or supplied) interpreter is within the
    supported range, so callers can do::

        msg = format_python_version_error()
        if msg:
            sys.stderr.write(msg)
            sys.exit(1)

    ``version_info`` may be any indexable sequence like ``sys.version_info``;
    it defaults to the current interpreter's version when omitted.
    """
    if version_info is None:
        version_info = sys.version_info

    major = version_info[0]
    minor = version_info[1]
    micro = version_info[2] if len(version_info) > 2 else 0
    current = f"{major}.{minor}.{micro}"

    if (major, minor) < MIN_PYTHON:
        return (
            f"\n\u274c Ziya requires Python {_fmt(MIN_PYTHON)} or higher.\n"
            f"   Current interpreter: Python {current}\n"
            f"\n"
            f"   macOS ships an older system Python (often 3.9 at /usr/bin/python3),\n"
            f"   which cannot satisfy Ziya's dependencies (e.g. mcp>=1.23.0 requires\n"
            f"   Python >= {_fmt(MIN_PYTHON)}). Install a newer Python and reinstall Ziya with it:\n"
            f"\n"
            f"       brew install python@3.12\n"
            f"       python3.12 -m pip install --user ziya\n"
            f"\n"
            f"   Then run Ziya with that interpreter (ensure its 'ziya' is on PATH).\n"
        )

    if (major, minor) >= MAX_PYTHON_EXCLUSIVE:
        return (
            f"\n\u274c Ziya does not yet support Python {current}"
            f" (supported range: >= {_fmt(MIN_PYTHON)}, < {_fmt(MAX_PYTHON_EXCLUSIVE)}).\n"
            f"   Install a supported interpreter (Python 3.10\u20133.14) and reinstall Ziya with it.\n"
        )

    return None
