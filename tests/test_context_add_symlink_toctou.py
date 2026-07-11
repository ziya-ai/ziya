"""
Regression coverage for PenPal #89 [LOW, CWE-367]: TOCTOU symlink race in
ContextAddFileTool bypassing project-root containment.

execute() validated a path, did a multi-ms _write_json, then read via
resolved.stat()/open()/read_text() — all symlink-following, holding only a
path string. A same-user process could swap a symlink into the final
component during the write window so the read followed it out-of-scope,
surfacing an out-of-project file's contents in model context and defeating
the containment check that confines prompt-injection-steered path requests.

Fixed by reading through os.open(..., O_RDONLY | O_NOFOLLOW) + os.fstat on
the open fd. `resolved` is already fully .resolve()'d by
_resolve_and_validate (legitimate symlinks dereferenced at validation), so
O_NOFOLLOW rejects only a symlink swapped into the final component AFTER
validation — exactly the race.
"""
import inspect
import os
import stat as stat_mod
import tempfile
import shutil

import pytest


class TestONofollowReadGate:
    """The O_NOFOLLOW read primitive rejects a final-component symlink while
    reading a normal file and a resolved regular file unchanged. This is the
    exact behavior the fixed read block relies on (the race window itself is
    not synchronously interleavable in a unit test, so the primitive is
    pinned directly)."""

    def setup_method(self):
        self.d = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _read_nofollow(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                return None
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return data
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def test_normal_file_reads(self):
        p = os.path.join(self.d, "n.txt")
        open(p, "w").write("hello")
        assert self._read_nofollow(p) == "hello"

    def test_symlink_final_component_rejected(self):
        secret = os.path.join(self.d, "SECRET")
        open(secret, "w").write("out-of-scope content")
        link = os.path.join(self.d, "trap")
        os.symlink(secret, link)
        with pytest.raises(OSError):   # ELOOP
            self._read_nofollow(link)

    def test_directory_rejected_as_non_regular(self):
        # fdopen path guarded by S_ISREG — a dir fd must not be read.
        sub = os.path.join(self.d, "sub")
        os.mkdir(sub)
        # os.open on a dir succeeds; the S_ISREG guard returns None.
        assert self._read_nofollow(sub) is None


class TestSourceContract:
    """Guards the inline O_NOFOLLOW read against silent regression back to the
    symlink-following resolved.read_text()/open() form."""

    def test_execute_uses_o_nofollow(self):
        from app.mcp.tools import context_management
        src = inspect.getsource(context_management.ContextAddFileTool.execute)
        assert "O_NOFOLLOW" in src, "O_NOFOLLOW read gate removed"
        assert "os.fstat" in src
        # The old symlink-following read must be gone from the inline-read block.
        assert "resolved.read_text(" not in src
        assert "resolved.open(" not in src


class TestNegativeControl:
    """Proves the O_NOFOLLOW gate is non-vacuous: a plain follow-symlink open
    DOES leak the out-of-scope target (the pre-fix behavior)."""

    def test_following_open_leaks_symlink_target(self):
        d = tempfile.mkdtemp()
        try:
            secret = os.path.join(d, "SECRET")
            open(secret, "w").write("SECRET-DATA")
            link = os.path.join(d, "trap")
            os.symlink(secret, link)
            # Pre-fix: a following open reads the out-of-scope content.
            with open(link, "r") as f:
                assert f.read() == "SECRET-DATA"
        finally:
            shutil.rmtree(d, ignore_errors=True)
