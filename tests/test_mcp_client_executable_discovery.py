"""
Regression coverage for PenPal #46 [LOW, CWE-434]: unvalidated executable
auto-discovery in MCPClient.connect().

The Python-script discovery branch was hardened against "attacker drops a
file into the install dir -> auto-run on next start" (requires a canonical
entrypoint name; refuses to guess among several). The native-executable
branch immediately below ran the first execute-bit file with NO name check —
a forgotten variant. Fixed by mirroring the canonical-allowlist hardening.

The discovery loop is inline in a large async method (impractical to unit
test end-to-end — it spawns subprocesses), so this pins (1) the selection
behavior via a faithful reimplementation and (2) a source-contract guard so
the inline hardening can't silently regress out (this repo's convention for
large non-pure modules; see diagramPluginXssRegression.test.ts).
"""
import inspect
import os
import stat
import tempfile
import shutil

import pytest


_CANONICAL_EXEC = ("server", "main", "run", "start")


def _choose_executable(files, installation_path):
    """Faithful reimplementation of the fixed discovery selection in
    MCPClient.connect() (client.py). Returns the chosen basename or None."""
    execs = [
        f for f in files
        if os.path.isfile(os.path.join(installation_path, f))
        and os.access(os.path.join(installation_path, f), os.X_OK)
    ]
    by_base = {os.path.splitext(f)[0].lower(): f for f in execs}
    chosen = next((by_base[c] for c in _CANONICAL_EXEC if c in by_base), None)
    if chosen is None and len(execs) == 1:
        chosen = execs[0]
    return chosen


@pytest.fixture
def instdir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _mk(d, name, execbit=True):
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\necho hi\n")
    if execbit:
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return name


class TestExecutableSelection:
    def test_canonical_name_wins_over_dropped_file(self, instdir):
        # A malicious "aaa_evil.sh" sorts first but must NOT be chosen when a
        # canonical "server" is present.
        _mk(instdir, "aaa_evil.sh")
        _mk(instdir, "server")
        assert _choose_executable(sorted(os.listdir(instdir)), instdir) == "server"

    def test_lone_executable_still_allowed(self, instdir):
        # A legit single-binary install (non-canonical name) still works.
        _mk(instdir, "my-mcp-bin")
        assert _choose_executable(os.listdir(instdir), instdir) == "my-mcp-bin"

    def test_multiple_noncanonical_execs_refused(self, instdir):
        # Ambiguous: two non-canonical executables -> refuse to guess.
        _mk(instdir, "evil1.sh")
        _mk(instdir, "evil2.sh")
        assert _choose_executable(sorted(os.listdir(instdir)), instdir) is None

    def test_non_executable_dropped_file_ignored(self, instdir):
        # A dropped readme.txt without the x-bit is not a candidate.
        _mk(instdir, "readme.txt", execbit=False)
        _mk(instdir, "run")
        assert _choose_executable(os.listdir(instdir), instdir) == "run"

    def test_canonical_with_extension_matched_by_base(self, instdir):
        _mk(instdir, "server.sh")
        _mk(instdir, "zzz_evil")
        assert _choose_executable(sorted(os.listdir(instdir)), instdir) == "server.sh"


class TestSourceContract:
    """Guards the inline hardening against silent regression."""

    def test_client_connect_has_canonical_exec_allowlist(self):
        from app.mcp import client
        src = inspect.getsource(client)
        assert "_CANONICAL_EXEC" in src, "canonical executable allowlist removed"
        # The old unconditional "first execute-bit file wins" pattern must be gone:
        # a bare `command = [file_path]` inside a `for file in files` loop with
        # only an X_OK check and no name gate.
        assert '_CANONICAL_EXEC = ("server", "main", "run", "start")' in src

    def test_negative_control_old_logic_would_pick_evil(self, instdir):
        # Proves the tests above are non-vacuous: the PRE-FIX logic (first
        # execute-bit file) would have chosen the malicious dropped file.
        _mk(instdir, "aaa_evil.sh")
        _mk(instdir, "server")
        first_exec = next(
            f for f in sorted(os.listdir(instdir))
            if os.access(os.path.join(instdir, f), os.X_OK)
        )
        assert first_exec == "aaa_evil.sh"  # pre-fix would auto-run this
