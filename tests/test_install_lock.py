"""
pip/pipx self-upgrade must not replace files under a running ziya.

Reported 2026-11-11: ``[Response interrupted: Error: No module named
'app.utils.provider_error_chunks']`` after an upgrade, fixed only by a
restart.  ``app/main.py`` upgraded the installation in place at startup
while another ziya was running from it, and continued running its own old
code against the new files.

Covered: the lock (exclusive only when nobody else holds the installation,
waiting reported when an upgrade was in progress, per-installation keying),
and the seam in ``app/main.py``: pip is not run while another process holds
the installation; after a successful upgrade the process re-execs itself
with the same command line; the re-exec'd process does not upgrade again
and does not leak the marker to its children.
"""
import fcntl
import os
import subprocess
import sys
import threading
import time

import pytest

from app.utils import install_lock


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ZIYA_DISABLE_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("ZIYA_UPGRADE_REEXEC", raising=False)
    install_lock.release_for_tests()
    yield tmp_path
    install_lock.release_for_tests()


def _other(mode):
    """Another holder of the lock (a separate open file = a separate holder)."""
    f = open(install_lock.lock_path(), "a+")
    fcntl.flock(f.fileno(), mode)
    return f


# -- the lock ----------------------------------------------------------------

def test_upgrade_allowed_only_when_nobody_else_holds_it():
    other = _other(fcntl.LOCK_SH)
    assert install_lock.acquire_for_upgrade() is False
    other.close()
    assert install_lock.acquire_for_upgrade() is True
    # Holding exclusive keeps a starting process from taking shared.
    probe = open(install_lock.lock_path(), "a+")
    with pytest.raises(OSError):
        fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    # Downgrading to shared lets it start, and is not a "waited".
    assert install_lock.hold_shared() == "held"
    fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    probe.close()


def test_start_during_an_upgrade_waits_and_says_so():
    upgrader = _other(fcntl.LOCK_EX)
    threading.Timer(0.3, upgrader.close).start()
    t0 = time.monotonic()
    assert install_lock.hold_shared() == "waited"
    assert time.monotonic() - t0 >= 0.25
    # Positive control: with nobody upgrading it is just "held".
    install_lock.release_for_tests()
    assert install_lock.hold_shared() == "held"


def test_lock_is_per_installation(tmp_path):
    a, b = tmp_path / "venv-a", tmp_path / "venv-b"
    a.mkdir(); b.mkdir()
    link = tmp_path / "venv-link"
    link.symlink_to(a)
    assert install_lock.lock_path(str(a)) != install_lock.lock_path(str(b))
    assert install_lock.lock_path(str(link)) == install_lock.lock_path(str(a))


def test_a_real_second_process_blocks_the_upgrade(home):
    code = ("import sys; from app.utils import install_lock;"
            "print(install_lock.hold_shared(), flush=True); sys.stdin.read()")
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=dict(os.environ),
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        assert child.stdout.readline().strip() == "held"
        assert install_lock.acquire_for_upgrade() is False
    finally:
        child.stdin.close()
        child.wait(10)
    # The kernel released the child's lock when it exited.
    assert install_lock.acquire_for_upgrade() is True


def test_reexec_argv_keeps_the_command_line(monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python3", "/venv/bin/ziya", "--port", "7000"], raising=False)
    assert install_lock.reexec_argv() == [sys.executable, "/venv/bin/ziya", "--port", "7000"]


# -- seam: app/main.py -------------------------------------------------------

@pytest.fixture
def ziya_main(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.sys, "platform", "darwin")
    monkeypatch.setattr(m, "get_current_version", lambda: "1.0.0")
    monkeypatch.setattr(m, "get_latest_version", lambda: "1.0.1")
    monkeypatch.setattr(m, "is_package_installed_with_pip", lambda name: True)
    calls = {"pip": [], "exec": []}
    monkeypatch.setattr(m.subprocess, "check_call", lambda cmd, **k: calls["pip"].append(cmd))
    monkeypatch.setattr(m.os, "execv", lambda exe, argv: calls["exec"].append((exe, argv,
                        os.environ.get("ZIYA_UPGRADE_REEXEC"))))
    m._calls = calls
    return m


def test_no_upgrade_while_another_ziya_runs_from_the_installation(ziya_main, monkeypatch):
    warned = []
    monkeypatch.setattr(ziya_main.logger, "warning", lambda msg, *a, **k: warned.append(msg))
    other = _other(fcntl.LOCK_SH)
    try:
        ziya_main.check_version_and_upgrade()
    finally:
        other.close()
    assert ziya_main._calls["pip"] == [] and ziya_main._calls["exec"] == []
    assert any("another ziya" in w and "1.0.1" in w for w in warned), warned


def test_upgrade_then_restart_into_the_new_version(ziya_main, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python3", "/venv/bin/ziya", "--port", "7000"], raising=False)
    ziya_main.check_version_and_upgrade()
    assert len(ziya_main._calls["pip"]) == 1 and "--upgrade" in ziya_main._calls["pip"][0]
    assert ziya_main._calls["exec"] == [
        (sys.executable, [sys.executable, "/venv/bin/ziya", "--port", "7000"], "1.0.1")]


def test_restarted_process_does_not_upgrade_again_or_leak_the_marker(ziya_main, monkeypatch):
    monkeypatch.setenv("ZIYA_UPGRADE_REEXEC", "1.0.1")
    ziya_main.check_version_and_upgrade()
    assert ziya_main._calls["pip"] == [] and ziya_main._calls["exec"] == []
    assert "ZIYA_UPGRADE_REEXEC" not in os.environ
    # It now holds the installation, so a second starter cannot upgrade.
    probe = open(install_lock.lock_path(), "a+")
    with pytest.raises(OSError):
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    probe.close()


def test_up_to_date_holds_the_installation(ziya_main, monkeypatch):
    monkeypatch.setattr(ziya_main, "get_latest_version", lambda: "1.0.0")
    ziya_main.check_version_and_upgrade()
    assert ziya_main._calls["pip"] == [] and ziya_main._calls["exec"] == []
    probe = open(install_lock.lock_path(), "a+")
    with pytest.raises(OSError):
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    probe.close()


def test_started_during_someone_elses_upgrade_restarts(ziya_main, monkeypatch):
    monkeypatch.setattr(ziya_main, "get_latest_version", lambda: "1.0.0")
    upgrader = _other(fcntl.LOCK_EX)
    threading.Timer(0.3, upgrader.close).start()
    ziya_main.check_version_and_upgrade()
    assert ziya_main._calls["pip"] == []
    assert len(ziya_main._calls["exec"]) == 1


def test_failed_pip_does_not_restart(ziya_main, monkeypatch):
    def boom(cmd, **k):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(ziya_main.subprocess, "check_call", boom)
    ziya_main.check_version_and_upgrade()
    assert ziya_main._calls["exec"] == []
