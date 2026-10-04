"""Cross-platform helpers for launching external commands and process I/O."""

import sys


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
