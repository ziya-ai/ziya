"""The AF_UNIX-overflow fallback directory must be usable regardless of umask.

``ShadowSocketServer.start()`` binds inside an ``os.umask(0o177)`` window so
the socket file is created 0600.  ``short_socket_dir()`` is called from that
window; ``os.mkdir(d, 0o700)`` is masked to 0o600 — a directory with no
owner search bit — and, once it exists, ``assert_private_dir`` accepted it
(it only tightened over-permissive modes).  Every later bind in that
directory then failed with EACCES, taking down every shadow session whose
registry path overflowed the 104/108-byte socket limit (deep $HOME, pytest
``tmp_path`` on macOS).
"""
import os
import socket
import stat
import sys

import pytest

from app.shadow import sock_server
from app.shadow.sock_server import assert_private_dir, short_socket_dir

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def isolated_tmp(monkeypatch):
    """Point tempfile.gettempdir() at a SHORT private dir so the test never
    touches the real per-user fallback directory.  pytest's tmp_path is not
    usable here: on macOS it already exceeds the 104-byte AF_UNIX limit, so a
    bind probe inside it fails with "path too long" regardless of the fix."""
    import shutil
    import tempfile
    real_gettempdir = tempfile.gettempdir
    base = tempfile.mkdtemp(prefix="zs-", dir=real_gettempdir())
    monkeypatch.setattr(tempfile, "gettempdir", lambda: base)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_created_under_restrictive_umask_is_0700_and_bindable(isolated_tmp):
    old = os.umask(0o177)  # exactly what ShadowSocketServer.start() sets
    try:
        d = short_socket_dir()
    finally:
        os.umask(old)
    assert _mode(d) == 0o700
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(os.path.join(d, "probe.sock"))  # raised EACCES before the fix
    finally:
        s.close()


def test_preexisting_0600_dir_is_repaired(isolated_tmp):
    """The persisted-damage case: a directory already created wrong by an
    earlier run must be fixed, not accepted."""
    d = os.path.join(str(isolated_tmp), f"ziya-shadow-{os.getuid()}")
    os.mkdir(d)
    os.chmod(d, 0o600)
    assert _mode(d) == 0o600  # precondition: search bit really is missing
    assert short_socket_dir() == d
    assert _mode(d) == 0o700


def test_over_permissive_dir_is_still_tightened(tmp_path):
    """Positive control for the original intent of assert_private_dir."""
    d = tmp_path / "loose"
    d.mkdir()
    os.chmod(d, 0o755)
    assert_private_dir(str(d))
    assert _mode(str(d)) == 0o700


def test_symlink_and_plain_file_still_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(RuntimeError):
        assert_private_dir(str(link))
    plain = tmp_path / "plainfile"
    plain.write_text("x")
    with pytest.raises(RuntimeError):
        assert_private_dir(str(plain))
