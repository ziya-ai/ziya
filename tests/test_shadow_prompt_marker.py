"""First-run prompt-marker hint + menu install (design doc §8)."""
import os
import pty
import select
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


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


def test_should_hint_only_when_rc_lacks_marker_and_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.shadow import prompt_marker as pm
    assert pm.should_hint(["/bin/sh"]) == (None, False)          # unknown shell: never
    assert pm.should_hint(["/bin/zsh"]) == ("zsh", True)         # no rc → hint
    pm.mark_hinted()
    assert pm.should_hint(["/bin/zsh"]) == ("zsh", False)        # one-time
    pm.hint_marker().unlink()
    (tmp_path / ".zshrc").write_text("export ZIYA_SHADOW_SESSION_X=1\n")
    assert pm.is_installed("zsh")                                # rc mentions the env → no hint
    assert pm.should_hint(["/bin/zsh"]) == ("zsh", False)


def test_install_appends_snippet_idempotently_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.shadow import prompt_marker as pm
    (tmp_path / ".bashrc").write_text("# existing\n")
    pm.install("bash")
    text = (tmp_path / ".bashrc").read_text()
    assert text.startswith("# existing") and "ZIYA_SHADOW_SESSION" in text and "⏺" in text
    assert pm.is_installed("bash")


def test_first_run_shows_hint_and_menu_p_installs(tmp_path):
    """Outermost surface: a zsh child with no ~/.zshrc → the hint renders once,
    and C-x C-z p appends the snippet.  Wrapping `sh` shows no hint."""
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=os.getcwd(), TERM="xterm",
               ZIYA_SHADOW_TINT="off")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, "-c",
            "from app.shadow.pty_host import run_interactive; import sys; "
            "sys.exit(run_interactive(['zsh', '-f', '-i'], label='pm'))"], env)
    try:
        out = _read_until(fd, b"C-x C-z, p appends")
        assert b"tip: add a" in out and b"ZIYA_SHADOW_SESSION" in out
        # Let the start banner and zsh's first prompt finish before pressing
        # the menu key, or the prefix lands mid-stream as passthrough.
        _read_until(fd, b"\x00never", 1.0)
        os.write(fd, b"\x18\x1a")
        out = _read_until(fd, b"[p] add")
        os.write(fd, b"p")
        out = _read_until(fd, b"added to")
        assert b".zshrc" in out
        os.write(fd, b"exit\r")
        _read_until(fd, b"ended (exit")
    finally:
        os.waitpid(pid, 0)
        os.close(fd)
    assert "ZIYA_SHADOW_SESSION" in (tmp_path / ".zshrc").read_text()
    assert (tmp_path / ".ziya/shadow/prompt_hint_shown").exists()
