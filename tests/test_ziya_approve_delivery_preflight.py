"""Mint-time delivery preflight for `ziya-approve --session` (2026-08-22 incident).

A session grant is enforced by the shell subprocess but DELIVERED by the
long-running server's in-memory manager. Two conditions break delivery while
every component still reports success:

  1. The server is running code older than the installed grant-delivery path
     (module loaded before an upgrade; Python never reloads it).
  2. Multiple servers share the single-slot ``.session_nonce`` file, so a
     grant binds to whichever wrote it last and silently fails elsewhere.

`_session_delivery_warnings` detects both at mint time. These tests exercise
the mtime heuristic with controlled files and pin the fail-soft contract.
The vintage heuristic keys on the fact that ``.session_nonce`` is written
once, in ``MCPManager.__init__`` — its mtime IS the server start time.
"""

import os
import time
from pathlib import Path

import pytest

from app.utils import ziya_approve as za


@pytest.fixture()
def fake_install(tmp_path, monkeypatch):
    """A fake installed package tree + ~/.ziya, with controllable mtimes.

    Relocates the module's `__file__` so the manager.py it resolves
    (Path(__file__).parent.parent / "mcp" / "manager.py") points into the
    fixture tree, and returns paths for the nonce and manager files.
    """
    pkg = tmp_path / "app"
    (pkg / "utils").mkdir(parents=True)
    (pkg / "mcp").mkdir(parents=True)
    manager = pkg / "mcp" / "manager.py"
    manager.write_text("# fixture manager\n")

    ziya_dir = tmp_path / ".ziya"
    ziya_dir.mkdir()
    config_path = ziya_dir / "mcp_config.json"
    config_path.write_text("{}")
    nonce = ziya_dir / ".session_nonce"
    nonce.write_text("a" * 32)

    monkeypatch.setattr(za, "__file__", str(pkg / "utils" / "ziya_approve.py"))
    return config_path, nonce, manager


def _set_mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def test_warns_when_delivery_code_newer_than_server_start(fake_install):
    config_path, nonce, manager = fake_install
    now = time.time()
    _set_mtime(nonce, now - 3600)   # server started an hour ago...
    _set_mtime(manager, now - 60)   # ...code upgraded a minute ago
    warnings = za._session_delivery_warnings(config_path)
    assert any("OLDER in-memory code" in w for w in warnings), warnings
    # The remediation must be actionable: restart server, then re-mint.
    assert any("restart the Ziya server" in w for w in warnings)


def test_quiet_when_server_newer_than_delivery_code(fake_install):
    config_path, nonce, manager = fake_install
    now = time.time()
    _set_mtime(manager, now - 3600)  # code installed an hour ago...
    _set_mtime(nonce, now - 60)      # ...server (re)started after it
    warnings = za._session_delivery_warnings(config_path)
    assert not any("OLDER in-memory code" in w for w in warnings), warnings


def test_one_second_slack_absorbs_same_instant_writes(fake_install):
    """Install + immediate server start must not warn (sub-second skew)."""
    config_path, nonce, manager = fake_install
    now = time.time()
    _set_mtime(manager, now)
    _set_mtime(nonce, now - 0.5)
    warnings = za._session_delivery_warnings(config_path)
    assert not any("OLDER in-memory code" in w for w in warnings), warnings


def test_fails_soft_when_nonce_missing(fake_install):
    """No nonce file -> vintage check silently skipped, never raises.

    (_approve_session refuses earlier on a missing nonce; the helper must
    still be safe to call unconditionally.)
    """
    config_path, nonce, _manager = fake_install
    nonce.unlink()
    warnings = za._session_delivery_warnings(config_path)
    assert isinstance(warnings, list)
    assert not any("OLDER in-memory code" in w for w in warnings)


def test_multi_server_warning_uses_ps_listing(fake_install, monkeypatch):
    """Two live `/bin/ziya` processes -> single-slot nonce warning; the
    signer's own `ziya-approve` process must not count."""
    config_path, _nonce, _manager = fake_install

    class _FakeCompleted:
        stdout = (
            " 111 /usr/bin/python3 /opt/x/bin/ziya --model a\n"
            " 222 /usr/bin/python3 /opt/x/bin/ziya --model b --port 9866\n"
            " 333 sudo /opt/x/bin/ziya-approve --session\n"
        )

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted())
    warnings = za._session_delivery_warnings(config_path)
    multi = [w for w in warnings if "single-slot" in w]
    assert len(multi) == 1
    assert "2 Ziya server processes" in multi[0]


def test_no_multi_server_warning_for_single_server(fake_install, monkeypatch):
    config_path, _nonce, _manager = fake_install

    class _FakeCompleted:
        stdout = " 111 /usr/bin/python3 /opt/x/bin/ziya --model a\n"

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted())
    warnings = za._session_delivery_warnings(config_path)
    assert not any("single-slot" in w for w in warnings)
