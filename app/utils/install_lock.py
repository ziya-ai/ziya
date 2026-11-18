"""
Keep a pip/pipx self-upgrade from replacing files under a running ziya.

``app/main.py`` upgrades ziya in place at startup (``pip install --upgrade``
into the same environment).  Any other ziya process already running from
that environment keeps its old modules in memory while the files on disk
change.  Its next lazy import then fails or loads a module from the new
version: reported 2026-11-11 as
``No module named 'app.utils.provider_error_chunks'`` mid-response, fixed
only by a restart.  The upgrading process has the same problem with
itself, since it keeps running the old code it already imported.

The rule here:

* every running ziya holds a SHARED ``flock`` on one lock file per
  installation (keyed by the real path of ``sys.prefix``) for its lifetime;
* an upgrade needs the EXCLUSIVE lock, taken without waiting, so it only
  happens when no other ziya is running from this installation;
* a process that starts while an upgrade is in progress waits for it, and
  is told so (``hold_shared() == "waited"``) so it can restart into the
  new files.

Locks are released by the kernel when the process exits, so a crash never
leaves a stale lock.  Without ``fcntl`` (Windows) nothing is locked;
Windows never upgrades in place anyway.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import IO, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover -- Windows
    fcntl = None  # type: ignore[assignment]

_handle: Optional[IO] = None
_mode: Optional[str] = None  # None | "ex" | "sh"


def lock_path(prefix: Optional[str] = None) -> str:
    """Lock file for the installation at *prefix* (default ``sys.prefix``)."""
    from app.utils.paths import get_ziya_home
    real = os.path.realpath(prefix or sys.prefix)
    key = hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]
    d = get_ziya_home() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"install-{key}.lock")


def _open() -> IO:
    global _handle
    if _handle is None:
        _handle = open(lock_path(), "a+")
    return _handle


def acquire_for_upgrade() -> bool:
    """Take the exclusive lock without waiting.

    True: no other ziya process holds this installation, and none can start
    on it until ``hold_shared`` is called or this process exits.
    """
    global _mode
    if fcntl is None:
        return False
    h = _open()
    try:
        fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _mode = "ex"
    return True


def hold_shared() -> str:
    """Mark this process as running from the installation, until it exits.

    Returns ``"held"``, ``"waited"`` when another process was upgrading
    the installation and this call waited for it to finish (so the files
    this process started from have been replaced), or ``"unsupported"``.
    """
    global _mode
    if fcntl is None:
        return "unsupported"
    if _mode == "sh":
        return "held"
    h = _open()
    was_ex = _mode == "ex"
    try:
        fcntl.flock(h.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        _mode = "sh"
        return "held"
    except OSError:
        pass
    # flock conversion is not atomic, so a starting process can slip in
    # between dropping our exclusive lock and taking the shared one.  It
    # cannot upgrade (it takes the exclusive lock only without waiting and
    # we are about to hold shared), so waiting here is brief.
    fcntl.flock(h.fileno(), fcntl.LOCK_SH)
    _mode = "sh"
    return "held" if was_ex else "waited"


def reexec_argv() -> List[str]:
    """argv that restarts this process with the same command line."""
    orig = getattr(sys, "orig_argv", None) or [sys.executable] + list(sys.argv)
    return [sys.executable] + list(orig[1:])


def release_for_tests() -> None:
    global _handle, _mode
    if _handle is not None:
        try:
            _handle.close()
        except OSError:
            pass
    _handle = None
    _mode = None
