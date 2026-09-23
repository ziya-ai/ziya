"""Shadow tint (design §8): a shadowed terminal must never look identical to
an unshadowed one, in a way that survives scrolling, `clear` and ssh hops.
Background (OSC 11) and, under a control lease, cursor colour (OSC 12) are
set on the LOCAL emulator and reset (OSC 111/112) on exit.  iTerm2 and
ghostty both honour these; the journal never sees them (display path only).
"""
import os
import pty
import select
import sys
import tempfile
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


def test_tint_sequence_shapes(monkeypatch):
    import importlib
    from app.shadow import pty_host
    obs = pty_host.tint_sequence(False)
    ctl = pty_host.tint_sequence(True)
    assert obs.startswith(b"\x1b]11;#") and obs.endswith(b"\x1b]112\x07")
    assert b"\x1b]12;" not in obs                       # cursor untouched when observing
    assert ctl.startswith(b"\x1b]11;#") and b"\x1b]12;#" in ctl
    assert obs != ctl                                   # control is visibly different
    monkeypatch.setattr(pty_host, "TINT_OBSERVE", "off")
    assert pty_host.tint_sequence(False) == b"" and pty_host.tint_sequence(True) == b""
    monkeypatch.setattr(pty_host, "TINT_OBSERVE", "not-a-colour")
    assert pty_host.tint_sequence(False) == b""         # malformed override → no tint


def _read_until(fd, needle, timeout=10.0):
    buf = b""
    end = time.time() + timeout
    while time.time() < end and needle not in buf:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                c = os.read(fd, 65536)
            except OSError:
                break
            if not c:
                break
            buf += c
    return buf


def test_tint_set_on_start_deepened_on_grant_reset_on_exit(tmp_path):
    from app.shadow import pty_host
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=os.getcwd(), TERM="xterm")
    env.pop("ZIYA_SHADOW_TINT", None)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [
            sys.executable, "-c",
            "from app.shadow.pty_host import run_interactive; import sys; "
            "sys.exit(run_interactive(['sh', '-i'], label='tint', control_ceiling='gated'))",
        ], env)
    try:
        out = _read_until(fd, b"C-x C-z for menu")
        out += _read_until(fd, pty_host.tint_sequence(False), 3.0)
        assert pty_host.tint_sequence(False) in out          # observe tint at start

        os.environ["HOME"] = str(tmp_path)
        from app.shadow import client, registry
        sid = registry.list_sessions()[0].session_id
        client.control_acquire(sid, "conv-tint", restriction="gated", policy="builtin")
        _read_until(fd, b"[g] grant")
        os.write(fd, b"g")
        out = _read_until(fd, pty_host.tint_sequence(True))
        assert pty_host.tint_sequence(True) in out           # deeper tint + cursor on grant

        os.write(fd, b"\x18\x1ar")                           # menu → revoke
        out = _read_until(fd, b"control lease ended")
        out += _read_until(fd, pty_host.tint_sequence(False), 3.0)
        assert pty_host.tint_sequence(False) in out          # back to observe tint

        os.write(fd, b"exit\r")
        out = _read_until(fd, b"ended (exit")
        assert pty_host.TINT_RESET in out                     # OSC 111/112 on exit
    finally:
        os.waitpid(pid, 0)
        os.close(fd)
