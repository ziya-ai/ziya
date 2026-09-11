"""Drive the interactive frontend inside a real PTY (outermost surface)."""
import os
import pty
import select
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


def _read_until(fd, needle: bytes, timeout=10.0) -> bytes:
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline and needle not in buf:
        r, _, _ = select.select([fd], [], [], 0.2)
        if fd in r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
    return buf


def test_run_interactive_passthrough_and_teardown(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=os.getcwd(), TERM="xterm")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [
            sys.executable, "-c",
            "from app.shadow.pty_host import run_interactive; "
            "import sys; sys.exit(run_interactive(['sh', '-i'], label='smoke'))",
        ], env)
    try:
        out = _read_until(fd, b"C-x C-z for menu")
        assert b"shadow session" in out and b'("smoke")' in out
        os.write(fd, b"echo marker-$((40+2))\r")
        out = _read_until(fd, b"marker-42")
        assert b"marker-42" in out
        # Menu: relabel via the composer, then confirm the overlay reflects it.
        os.write(fd, b"\x18\x1a")
        out = _read_until(fd, b"[l] relabel")
        assert b'shadow ' in out
        os.write(fd, b"l")
        _read_until(fd, b"new label")
        os.write(fd, b"renamed\r")
        out = _read_until(fd, b'label is now "renamed"')
        assert b'label is now "renamed"' in out
        os.write(fd, b"exit\r")
        out = _read_until(fd, b"ended (exit")
        assert b"journal removed" in out
    finally:
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    # Nothing left behind in the registry dir.
    sessions = tmp_path / ".ziya" / "shadow" / "sessions"
    assert not any(sessions.iterdir()) if sessions.exists() else True
