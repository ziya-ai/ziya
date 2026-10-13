"""``ziya-jobwrap``: the process a managed job runs under (design/job-runner.md).

Stdlib only.  Started by ``JobManager`` in its own session, so it survives
the server that launched it; the server holds no pipes to it and learns
everything from the files it writes in the job directory:

  journal.jsonl   output records in the shadow journal format
                  ({"t": "cmd"|"output"|"exit"|"meta", "seq", "ts", ...}),
                  so ``app.utils.journal_wait`` serves jobs unchanged
  status.json     written once, on exit: code or signal, duration

Usage: ``ziya-jobwrap --job-dir DIR --cwd DIR [--max-runtime S] -- COMMAND``

The command is run with ``sh -c`` in a NEW session (its own process
group), and the wrapper itself is the group leader's parent.  Killing the
job means ``killpg`` on the child's pid, which the wrapper records in
``status.json`` as ``pgid`` immediately after spawn so the manager can do
it even if the wrapper has since died.  At ``--max-runtime`` the wrapper
sends TERM to the group, then KILL after a grace period, and records
``reason: max_runtime``.

Output is read line by line from a merged stdout+stderr pipe and appended
as ``output`` records of at most OUTPUT_CHUNK_BYTES; the journal rotates
at ROTATION_CAP_BYTES, and output past TOTAL_CAP_BYTES is counted, not
stored.  Journaling writes are plaintext: the job directory is server-only
(write-policy ``SERVER_ONLY_PATHS``) and a job's output is the user's own
command output, like a shell tool result.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import threading
import time

OUTPUT_CHUNK_BYTES = 64 * 1024
ROTATION_CAP_BYTES = 50 * 1024 * 1024
TOTAL_CAP_BYTES = 200 * 1024 * 1024
KILL_GRACE_S = 10.0


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _Journal:
    """Append-only JSONL with the shadow record shape and size rotation."""

    def __init__(self, job_dir: str):
        self.job_dir = job_dir
        self.path = os.path.join(job_dir, "journal.jsonl")
        self._seq = 0
        self._lock = threading.Lock()
        self._bytes = 0
        self._total_bytes = 0
        self._dropped_bytes = 0
        self._rotations = 0
        open(self.path, "a").close()
        os.chmod(self.path, 0o600)

    def append(self, rec: dict) -> int:
        with self._lock:
            self._seq += 1
            rec["seq"] = self._seq
            rec["ts"] = _ts()
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            n = len(line.encode("utf-8", errors="replace"))
            self._total_bytes += n
            if self._total_bytes > TOTAL_CAP_BYTES:
                # Past the hard cap: count, don't store.  seq still advances
                # so a waiter sees "change"; the text is a placeholder.
                self._dropped_bytes += n
                return self._seq
            if self._bytes + n > ROTATION_CAP_BYTES and self._bytes > 0:
                self._rotations += 1
                os.replace(self.path, os.path.join(self.job_dir, f"journal.{self._rotations}.jsonl"))
                self._bytes = 0
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            self._bytes += n
            return self._seq

    def cmd(self, text: str) -> int:
        return self.append({"t": "cmd", "text": text})

    def output(self, text: str, cmd_seq: int, stream: str = "stdout") -> None:
        data = text.encode("utf-8", errors="replace")
        for i in range(0, len(data), OUTPUT_CHUNK_BYTES):
            chunk = data[i:i + OUTPUT_CHUNK_BYTES]
            self.append({"t": "output", "cmd_seq": cmd_seq, "stream": stream,
                         "text": chunk.decode("utf-8", errors="replace"),
                         "truncated": (i + OUTPUT_CHUNK_BYTES) < len(data)})

    def exit(self, cmd_seq: int, code, **extra) -> int:
        return self.append({"t": "exit", "cmd_seq": cmd_seq, "code": code, **extra})

    def meta(self, event: str, data: dict) -> int:
        return self.append({"t": "meta", "event": event, "data": data})

    @property
    def dropped_bytes(self) -> int:
        return self._dropped_bytes


def _write_status(job_dir: str, status: dict) -> None:
    tmp = os.path.join(job_dir, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(job_dir, "status.json"))


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run(job_dir: str, cwd: str, command: str, max_runtime_s: float) -> int:
    journal = _Journal(job_dir)
    started = time.time()
    t0 = time.monotonic()
    cmd_seq = journal.cmd(command)
    proc = subprocess.Popen(
        ["sh", "-c", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=cwd, start_new_session=True,
    )
    pgid = proc.pid
    # Record the group id before anything else: the manager needs it to
    # kill the job even if this wrapper is gone.
    _write_status(job_dir, {"state": "running", "wrapper_pid": os.getpid(), "pgid": pgid,
                            "started_at": started})

    reason = {"value": None}

    def _terminate(why: str, sig_first: int = signal.SIGTERM) -> None:
        if reason["value"] is None:
            reason["value"] = why
        journal.meta("kill", {"reason": why, "signal": sig_first})
        _killpg(pgid, sig_first)
        deadline = time.monotonic() + KILL_GRACE_S
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            _killpg(pgid, signal.SIGKILL)

    # The manager's job_kill signals THIS wrapper with TERM; relay to the
    # group so the whole tree goes, not just us.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(
        target=_terminate, args=("killed",), daemon=True).start())
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    watchdog = None
    if max_runtime_s and max_runtime_s > 0:
        watchdog = threading.Timer(max_runtime_s, _terminate, args=("max_runtime",))
        watchdog.daemon = True
        watchdog.start()

    assert proc.stdout is not None
    for raw in proc.stdout:
        journal.output(raw.decode("utf-8", errors="replace"), cmd_seq)
    code = proc.wait()
    if watchdog is not None:
        watchdog.cancel()

    duration = time.monotonic() - t0
    try:
        peak_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        if sys.platform != "darwin":
            peak_rss *= 1024  # Linux reports KiB; macOS bytes
    except Exception:  # noqa: BLE001
        peak_rss = None
    sig = -code if code < 0 else None
    status = {
        "state": "exited", "wrapper_pid": os.getpid(), "pgid": pgid,
        "started_at": started, "ended_at": time.time(),
        "exit_code": code if code >= 0 else None, "signal": sig,
        "duration_s": round(duration, 3), "peak_rss_bytes": peak_rss,
        "reason": reason["value"] or "exit",
        "output_dropped_bytes": journal.dropped_bytes,
    }
    journal.exit(cmd_seq, status["exit_code"], signal=sig, duration_s=status["duration_s"],
                 reason=status["reason"])
    _write_status(job_dir, status)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ziya-jobwrap", add_help=True)
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--max-runtime", type=float, default=0.0)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)
    cmd = ns.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if len(cmd) != 1:
        ap.error("exactly one command string after --")
    return run(ns.job_dir, ns.cwd, cmd[0], ns.max_runtime)


if __name__ == "__main__":
    sys.exit(main())
