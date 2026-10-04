"""Cross-platform helpers for launching external commands and process I/O."""

import os
import sys
from typing import Optional, Sequence


def configure_stdio() -> None:
    """Keep emoji status output from crashing on non-UTF-8 streams.

    When stdout is redirected on Windows (log files, IDE runners, services) it
    uses the ANSI code page, e.g. cp1252, with ``strict`` errors, so a bare
    ``print("✅ ...")`` raises ``UnicodeEncodeError``.  stderr already defaults
    to ``backslashreplace``; apply the same policy to stdout.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if reconfigure is None or encoding == "utf8":
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


def resolve_executable(command: str, path: Optional[str] = None) -> str:
    """On Windows, resolve a bare command name through PATH and PATHEXT.

    CreateProcess finds only ``.exe`` files, so ``npm`` and ``npx`` (which
    are ``npm.cmd`` and ``npx.cmd``) fail with FileNotFoundError.  *path*
    is the PATH the child will get, if not this process's.  Off Windows,
    or when nothing is found, the command is returned unchanged, keeping
    exec's own PATH search and the caller's usual error.

    Only absolute PATH entries are searched.  ``shutil.which`` also looks in
    the current directory first, which is usually the user's project, so a
    repository could otherwise supply its own ``npx.cmd``.
    """
    if sys.platform != "win32" or os.path.dirname(command):
        return command
    pathext = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    if any(command.lower().endswith(ext.lower()) for ext in pathext):
        names = [command]
    else:
        names = [command + ext for ext in pathext]
    search = os.environ.get("PATH", "") if path is None else path
    for directory in search.split(os.pathsep):
        if not os.path.isabs(directory):
            continue
        for name in names:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
    return command


def resolve_command(command: Sequence[str], path: Optional[str] = None) -> list:
    """Return *command* with its executable resolved (see resolve_executable)."""
    if not command:
        return []
    return [resolve_executable(command[0], path), *command[1:]]
