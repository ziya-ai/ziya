"""
The shell's read-only git tier must not admit ``--output=<file>``.

``--output`` is a diff/revision option: every subcommand that walks history or
renders a diff honours it and writes (or overwrites) an arbitrary path,
including an untracked file.  Probed against real git (2026-09-25): diff, log,
show, whatchanged, rev-list, shortlog, reflog, blame and ``stash list`` all
wrote the file; git accepts neither abbreviations (``--outp``) nor a short
``-o``.  So one guard on every safe-tier pattern closes it, and ops that do not
take the option lose nothing (git would reject it anyway).

Asserted at ``is_command_allowed`` — the decision the model actually hits —
not against the pattern strings.  ``TestTheThreatIsReal`` pins that the
listed forms really write, so this file cannot silently certify a guard
against a non-problem.
"""

import os
import subprocess
from pathlib import Path

import pytest

from app.config.shell_config import DEFAULT_SHELL_CONFIG
from app.mcp_servers.shell_server import ShellServer

WRITING_FORMS = [
    "git diff --output=notes.txt",
    "git diff --output notes.txt",
    "git diff HEAD~1 --stat --output=/tmp/x",
    "git log -p --output=notes.txt",
    "git log --oneline --output notes.txt",
    "git show --output=notes.txt",
    "git show HEAD '--output=notes.txt'",
    'git show HEAD "--output=notes.txt"',
    "git whatchanged -p --output=notes.txt",
    "git rev-list HEAD --output=notes.txt",
    "git shortlog HEAD --output=notes.txt",
    "git reflog -p --output=notes.txt",
    "git blame --output=notes.txt README.md",
    "git stash list -p --output=notes.txt",
    "git diff --OUTPUT=notes.txt",  # patterns match case-insensitively
]

STILL_ALLOWED = [
    "git status",
    "git diff",
    "git diff --stat",
    "git diff --output-indicator-new=+ --output-indicator-old=-",
    "git log -p -3",
    "git log --oneline --grep=fix",
    "git show HEAD:README.md",
    "git blame -L 1,20 README.md",
    "git rev-list --count HEAD",
    "git stash list",
]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv(
        "SAFE_GIT_OPERATIONS", ",".join(DEFAULT_SHELL_CONFIG["safeGitOperations"]),
    )
    monkeypatch.setenv("GIT_OPERATIONS_ENABLED", "true")
    return ShellServer()


class TestOutputOptionRefused:
    @pytest.mark.parametrize("cmd", WRITING_FORMS)
    def test_writing_form_is_rejected(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} writes a file and must be rejected"

    @pytest.mark.parametrize("cmd", STILL_ALLOWED)
    def test_read_forms_still_allowed(self, server, cmd):
        """Positive control: the guard does not cost ordinary read-only use."""
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"{cmd!r} should be allowed: {reason}"

    def test_every_safe_pattern_carries_the_guard(self, server):
        """A future op added to git_patterns inherits the guard automatically."""
        import re
        missing = [op for op, pat in server.git_patterns.items()
                   if re.match(pat, f"git {op} --output=x", re.IGNORECASE | re.DOTALL)]
        assert not missing, f"patterns admitting --output: {missing}"


_GIT = subprocess.run(["git", "--version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _GIT, reason="git not available")
class TestTheThreatIsReal:
    @pytest.fixture
    def repo(self, tmp_path):
        def g(*a):
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *a],
                           cwd=tmp_path, capture_output=True, check=True)
        g("init", "-q", "-b", "main")
        (tmp_path / "a").write_text("1\n")
        g("add", "a")
        g("commit", "-q", "-m", "i")
        (tmp_path / "a").write_text("2\n")
        return tmp_path

    @pytest.mark.parametrize("args", [
        ["diff", "--output=OUT"], ["log", "-p", "-1", "--output=OUT"],
        ["show", "--output=OUT"], ["rev-list", "--output=OUT", "HEAD"],
        ["blame", "--output=OUT", "a"],
    ])
    def test_git_really_writes_the_file(self, repo, args):
        victim = repo / "OUT"
        victim.write_text("untracked work\n")
        subprocess.run(["git", *args], cwd=repo, capture_output=True)
        assert victim.read_text() != "untracked work\n", (
            f"git {' '.join(args)} no longer overwrites --output; "
            "the guard may be obsolete")
