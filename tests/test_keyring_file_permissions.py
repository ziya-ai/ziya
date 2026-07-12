"""Regression tests for PenPal #134 [CWE-200]: keyring/salt file TOCTOU.

The pre-fix code wrote sensitive files (keyring.json holding wrapped DEKs,
the per-install PBKDF2 salt) with ``write_text``/``write_bytes`` and *then*
``chmod 0o600`` — leaving a window where the file existed world-readable
(0o644 under the default umask). Combined with ``get_ziya_home`` creating
``~/.ziya`` at the umask default (0o755, world-traversable), a cross-user
local process could read key material during that window.

Fix: (1) ``get_ziya_home`` hardens ~/.ziya to 0o700; (2) the keyring and salt
writers create the file 0o600 atomically via ``os.open`` + ``os.fchmod`` so it
is never group/world-readable at any instant.

These tests assert the post-fix invariants AND include a negative control
proving the old write-then-chmod pattern is observably exposed.
"""
import json
import os
import stat
import tempfile

import pytest


@pytest.fixture
def fresh_ziya_home(monkeypatch):
    """Point ZIYA_HOME at a brand-new dir so we exercise creation paths."""
    d = tempfile.mkdtemp()
    home = os.path.join(d, ".ziya")
    monkeypatch.setenv("ZIYA_HOME", home)
    return home


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class TestZiyaHomeHardened:
    def test_get_ziya_home_is_owner_only(self, fresh_ziya_home):
        from app.utils.paths import get_ziya_home

        home = get_ziya_home()
        assert os.path.isdir(home)
        m = _mode(home)
        # No group/other bits at all.
        assert m & 0o077 == 0, f"~/.ziya is {oct(m)}, expected owner-only"

    def test_get_ziya_home_idempotent_when_preexisting(self, fresh_ziya_home):
        from app.utils.paths import get_ziya_home

        first = get_ziya_home()
        # Loosen perms, then re-call: it should re-harden.
        os.chmod(first, 0o755)
        second = get_ziya_home()
        assert str(first) == str(second)
        assert _mode(second) & 0o077 == 0


class TestKeyringSavePrivate:
    def test_saved_keyring_is_owner_only(self, fresh_ziya_home):
        from app.utils.encryption import Keyring

        kr = Keyring()
        kr._save()
        assert os.path.exists(kr.path)
        assert _mode(str(kr.path)) == 0o600
        # No leftover world-readable temp file.
        assert not os.path.exists(str(kr.path) + ".tmp")

    def test_keyring_roundtrip_content_intact(self, fresh_ziya_home):
        from app.utils.encryption import Keyring

        kr = Keyring()
        kr._save()
        # File is valid JSON with the expected top-level shape.
        data = json.loads(open(kr.path).read())
        assert "keys" in data and isinstance(data["keys"], list)


class TestSaltWriterPrivate:
    def test_salt_file_is_owner_only_and_stable(self, fresh_ziya_home):
        from app.utils.encryption import DataEncryptor

        enc = DataEncryptor()
        salt = enc._get_or_create_passphrase_salt()
        spath = enc._passphrase_salt_path()
        assert _mode(str(spath)) == 0o600
        assert len(salt) == 16
        # Idempotent: a second call returns the same salt (read path).
        assert enc._get_or_create_passphrase_salt() == salt


class TestNegativeControlOldPattern:
    """Proves the vulnerability was real: the OLD write-then-chmod pattern
    leaves a world-readable window that an atomic create does not."""

    def test_write_then_chmod_exposes_window(self, tmp_path):
        target = tmp_path / "old_keyring.json"
        tmp = target.with_suffix(".tmp")
        # Reproduce the pre-fix sequence.
        tmp.write_text(json.dumps({"keys": ["wrapped"]}))
        window_mode = _mode(str(tmp))
        os.rename(tmp, target)
        os.chmod(target, 0o600)
        # The window mode was group/other-readable — the bug.
        assert window_mode & 0o044, (
            "negative control failed to reproduce the exposure "
            f"(temp mode {oct(window_mode)})"
        )

    def test_atomic_create_has_no_window(self, tmp_path):
        target = tmp_path / "new_keyring.json"
        # The fix's primitive: create private from byte zero.
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(b"wrapped")
        except BaseException:
            os.close(fd)
            raise
        assert _mode(str(target)) & 0o077 == 0
