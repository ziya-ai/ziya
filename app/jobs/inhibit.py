"""Keep-awake inhibitor for one job (design/job-runner.md, Keep-awake).

Holds an OS sleep inhibitor for the lifetime of the job's wrapper process:

  macOS   ``caffeinate -i -w <wrapper pid>`` -- exits on its own when the
          wrapper does, so nothing is left behind if the server stops.
  Linux   ``systemd-inhibit --what=idle --who=ziya --why=<label>
          tail --pid=<wrapper pid> -f /dev/null`` -- same self-terminating
          shape, when systemd-inhibit exists.

Neither binary is a job command: they are spawned by the server with a
fixed argv, not adjudicated by the allowlist, and they do nothing but hold
a lock while a pid lives.  When no inhibitor is available the job still
starts; ``job_start`` reports ``keep_awake: "unavailable"``.

One job, one inhibitor.  Keeping the machine awake for a whole card is
item 7 (time-budgeted cards).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Dict, Optional

from app.utils.logging_utils import logger


def _argv(wrapper_pid: int, label: str) -> Optional[list]:
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        return ["caffeinate", "-i", "-w", str(wrapper_pid)]
    if sys.platform.startswith("linux") and shutil.which("systemd-inhibit") and shutil.which("tail"):
        return ["systemd-inhibit", "--what=idle", "--who=ziya", f"--why=job {label}",
                "--mode=block", "tail", f"--pid={wrapper_pid}", "-f", "/dev/null"]
    return None


def acquire(wrapper_pid: int, label: str) -> Dict[str, object]:
    """Start an inhibitor bound to ``wrapper_pid``.

    Returns ``{"keep_awake": "held"|"unavailable"|"failed", ...}``; the
    inhibitor pid is included when held so ``job.json`` can record it.
    """
    argv = _argv(wrapper_pid, label)
    if argv is None:
        return {"keep_awake": "unavailable",
                "detail": "no caffeinate (macOS) or systemd-inhibit (Linux) on this host"}
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as e:
        logger.warning(f"keep-awake inhibitor failed to start: {e}")
        return {"keep_awake": "failed", "detail": str(e)}
    return {"keep_awake": "held", "inhibitor_pid": proc.pid, "inhibitor": argv[0]}


def release(inhibitor_pid: Optional[int]) -> None:
    """Best effort: the inhibitor exits with the wrapper anyway (``-w`` /
    ``--pid``), so this only shortens the window after an early kill."""
    if not inhibitor_pid:
        return
    import os
    import signal
    try:
        os.kill(int(inhibitor_pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, ValueError):
        pass
