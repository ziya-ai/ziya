"""Headless shadow host entrypoint (design doc §6.2).

``python -m app.shadow.headless --label prod-42 --ceiling gated \
    --spawned-by '{"conversation_id": "..."}' --announce-fd 3 -- ssh prod-42``

Runs a shadow session with no human terminal: PTY + segmenter + journal +
socket, nothing displayed.  Started by ``client.spawn_headless`` from a
chat process, detached into its own session so it outlives the tool call
and the conversation turn.

Authority inverts for these sessions: there is no second human at a
shadow terminal, so the *spawning conversation* is the authority (the
chat-side human approved the spawn tool call).  Consequences enforced
elsewhere: leases on a spawned session are granted implicitly and are
clamped to ``strict`` (§6.3) — nobody is watching a banner, so the
policy is the entire control.

Lifecycle: SIGTERM/SIGHUP hang up the child (which ends the session and
unlinks its files).  An idle watchdog ends the session after
``--idle-timeout`` seconds with no live lease heartbeat, no subscriber
and no journal activity (default 24 h).
"""
import argparse
import json
import os
import signal
import sys
import threading
import time
from typing import Optional

DEFAULT_IDLE_S = 24 * 3600
_WATCH_TICK_S = 5.0


def _announce(fd: Optional[int], text: str) -> None:
    if fd is None:
        return
    try:
        os.write(fd, (text + "\n").encode("utf-8"))
        os.close(fd)
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ziya-shadow-headless", add_help=False)
    ap.add_argument("--label")
    ap.add_argument("--ceiling", default="gated", choices=["gated", "unrestricted"])
    ap.add_argument("--spawned-by", default="{}")
    ap.add_argument("--announce-fd", type=int)
    ap.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_S)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    try:
        spawned_by = json.loads(args.spawned_by) or {}
        if not isinstance(spawned_by, dict):
            spawned_by = {}
    except ValueError:
        spawned_by = {}

    # Daemonize: the process Popen'd by the spawner exits at once so the
    # spawner can wait() it; the real host is this fork's child, reparented
    # to init and reaped by the system when it ends.  Without this the host
    # would linger as a zombie of the chat process that spawned it (which
    # never waits), so liveness checks would report it alive forever.
    if os.fork() != 0:
        os._exit(0)

    from app.shadow.pty_host import ShadowCore

    try:
        core = ShadowCore(cmd, label=args.label, headless=True,
                          control_ceiling=args.ceiling, spawned_by=spawned_by)
        core.spawn()
    except Exception as e:  # noqa: BLE001 — report to the spawner, then exit
        _announce(args.announce_fd, f"error: {e}")
        return 1
    _announce(args.announce_fd, core.entry.session_id)

    def _on_term(signum, frame):
        core.terminate_child(signal.SIGHUP)

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGHUP, _on_term)

    stop = threading.Event()

    def _watchdog() -> None:
        """End the session after ``idle_timeout`` of nothing happening."""
        last_activity = time.monotonic()
        last_seq = core.journal._seq
        while not stop.wait(_WATCH_TICK_S):
            now = time.monotonic()
            srv = core.server
            busy = False
            if srv is not None:
                with srv._lease_lock:
                    busy = srv._leases.current(now) is not None
                busy = busy or srv.subscriber_count > 0
            seq = core.journal._seq
            if seq != last_seq:
                last_seq, busy = seq, True
            if busy:
                last_activity = now
            elif now - last_activity >= args.idle_timeout:
                core.journal.meta("idle_shutdown", {"idle_s": round(now - last_activity, 1)})
                core.terminate_child(signal.SIGHUP)
                return

    threading.Thread(target=_watchdog, name="shadow-idle", daemon=True).start()
    try:
        return core.run(stdin_fd=None)
    finally:
        stop.set()


if __name__ == "__main__":
    sys.exit(main())
