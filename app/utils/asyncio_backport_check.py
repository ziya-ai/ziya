"""Detect the obsolete ``asyncio`` backport from PyPI.

``asyncio`` has been part of the standard library since Python 3.4.  The
PyPI package of the same name is a 2015 backport (3.4.3) whose code does not
parse on Python 3.7+ (it calls ``tasks.async(...)``; ``async`` is a keyword).
Some third-party packages still declare it as a requirement (seen:
``mcp-shell-server``), so pip installs it into site-packages next to Ziya.

Normally the stdlib copy wins on ``sys.path`` and the backport is never
imported.  Any environment that puts site-packages first (a job runner, a
custom PYTHONPATH) imports the backport instead and fails with a
``SyntaxError`` deep inside an unrelated import, e.g. pytest-asyncio.

Ziya cannot stop pip from installing it, so this only warns.  Stdlib only,
so it is safe to call before any third-party import.
"""

import os
import sys
import sysconfig
from typing import Optional


def find_asyncio_backport() -> Optional[dict]:
    """Return ``{"version", "location", "shadows"}`` if the backport is installed.

    ``shadows`` is True when the ``asyncio`` this interpreter actually
    imports is not the stdlib one.  Returns None when no backport is
    installed (the stdlib module has no distribution metadata).
    """
    try:
        from importlib import metadata
        dist = metadata.distribution("asyncio")
    except Exception:
        return None
    try:
        location = str(dist.locate_file(""))
    except Exception:
        location = "site-packages"

    shadows = False
    try:
        import importlib.util
        spec = importlib.util.find_spec("asyncio")
        origin = spec.origin if spec else None
        stdlib = sysconfig.get_paths().get("stdlib")
        if origin and stdlib:
            shadows = not os.path.realpath(origin).startswith(
                os.path.realpath(stdlib) + os.sep)
    except Exception:
        pass
    return {"version": dist.version, "location": location, "shadows": shadows}


def format_asyncio_backport_warning(info: Optional[dict] = None,
                                    _detect: bool = True) -> Optional[str]:
    """Return an actionable warning, or None when nothing is wrong."""
    if info is None and _detect:
        info = find_asyncio_backport()
    if not info:
        return None
    lead = ("is shadowing the standard library" if info.get("shadows")
            else "is installed (the standard library still wins here, but any "
                 "environment that puts site-packages first will import it)")
    return (
        f"\n\u26a0\ufe0f  The obsolete 'asyncio' {info.get('version', '')} backport from PyPI {lead}.\n"
        f"   Location: {info.get('location', 'site-packages')}\n"
        f"   It does not run on Python 3.7+ and breaks imports with a SyntaxError.\n"
        f"   asyncio ships with Python; remove the backport with:\n"
        f"\n"
        f"       {os.path.basename(sys.executable) or 'python3'} -m pip uninstall asyncio\n"
        f"\n"
        f"   (A package that declares it, e.g. mcp-shell-server, keeps working.)\n"
    )


def warn_if_asyncio_backport(stream=None) -> bool:
    """Print the warning to ``stream`` (stderr) once.  Returns True if printed."""
    msg = format_asyncio_backport_warning()
    if not msg:
        return False
    try:
        (stream or sys.stderr).write(msg)
    except Exception:
        return False
    return True
