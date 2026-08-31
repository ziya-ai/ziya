"""
Read-only git subcommands reachable through the shell allowlist.

Three ops in this set had a *pattern* in ``ShellServer.git_patterns`` but were
absent from ``DEFAULT_SHELL_CONFIG["safeGitOperations"]`` (``cat-file``,
``check-ignore``) or had no pattern at all (``grep``).  Because the pattern is
only installed for ops that appear in the configured/`SAFE_GIT_OPERATIONS`
list, a defined-but-unlisted pattern is dead code: the command is rejected.
These tests assert the seam -- config list, pattern table, and the
``is_command_allowed`` decision -- rather than any one half of it.

``git grep`` is the one entry here that is not unconditionally read-only:
``-O`` / ``--open-files-in-pager`` runs an arbitrary pager command over the
matching files, which is command execution by another name.  The negative
cases below are the load-bearing ones.
"""

import pytest

from app.config.shell_config import DEFAULT_SHELL_CONFIG
from app.mcp_servers.shell_server import ShellServer


NEWLY_SAFE_OPS = ["grep", "cat-file", "check-ignore"]


@pytest.fixture
def server(monkeypatch):
    """A server driven by the *configured* safe-git list, not the fallback.

    Setting ``SAFE_GIT_OPERATIONS`` to the default config's own value is not a
    privilege escalation over the built-in floor, so the escalation-signature
    gate leaves it intact.  If that assumption ever breaks, the assertions on
    already-established ops (``status``) fail loudly rather than silently
    testing a clamped server.
    """
    monkeypatch.setenv(
        "SAFE_GIT_OPERATIONS",
        ",".join(DEFAULT_SHELL_CONFIG["safeGitOperations"]),
    )
    monkeypatch.setenv("GIT_OPERATIONS_ENABLED", "true")
    return ShellServer()


@pytest.fixture
def fallback_server(monkeypatch):
    """A server with no SAFE_GIT_OPERATIONS in env -> hardcoded fallback string."""
    monkeypatch.delenv("SAFE_GIT_OPERATIONS", raising=False)
    monkeypatch.setenv("GIT_OPERATIONS_ENABLED", "true")
    return ShellServer()


class TestConfigAndPatternTableAgree:
    """Every configured safe op must have a pattern, or it silently does nothing."""

    @pytest.mark.parametrize("op", NEWLY_SAFE_OPS)
    def test_op_is_in_default_config(self, op):
        assert op in DEFAULT_SHELL_CONFIG["safeGitOperations"]

    @pytest.mark.parametrize("op", NEWLY_SAFE_OPS)
    def test_op_has_a_pattern(self, server, op):
        assert op in server.git_patterns

    def test_no_configured_op_lacks_a_pattern(self, server):
        """Guard against re-introducing a config entry with no pattern."""
        missing = [
            op for op in DEFAULT_SHELL_CONFIG["safeGitOperations"]
            if op not in server.git_patterns
        ]
        assert missing == [], f"configured but unimplemented git ops: {missing}"


class TestNewlySafeOpsAreAllowed:

    @pytest.mark.parametrize(
        "cmd",
        [
            "git grep foo",
            "git grep -n TODO -- app/",
            "git grep -i --recurse-submodules pattern",
            "git grep -l 'def main' -- '*.py'",
            "git cat-file -p HEAD",
            "git cat-file --batch-check",
            "git check-ignore -v build/",
        ],
    )
    def test_allowed(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"{cmd!r} should be allowed: {reason}"

    def test_control_op_still_allowed(self, server):
        """Positive control: the fixture produced a usable git-enabled server."""
        ok, reason = server.is_command_allowed("git status")
        assert ok, reason

    @pytest.mark.parametrize("cmd", ["git grep foo", "git cat-file -p HEAD",
                                     "git check-ignore -v build/"])
    def test_allowed_via_fallback_default(self, fallback_server, cmd):
        """The hardcoded in-server fallback must not lag the config default."""
        ok, reason = fallback_server.is_command_allowed(cmd)
        assert ok, f"{cmd!r} should be allowed by the fallback default: {reason}"


class TestGitGrepPagerEscapeIsRefused:
    """``-O``/``--open-files-in-pager`` executes a command; it must not pass."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git grep -O/bin/sh foo",
            "git grep -Ovim foo",
            "git grep --open-files-in-pager=/bin/sh foo",
            "git grep --open-files-in-pager foo",
            "git grep -nO foo",
            "git grep -lO foo",
        ],
    )
    def test_pager_forms_rejected(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} must be rejected (executes a pager command)"
        assert "not allowed" in reason.lower()

    def test_prefix_confusion_rejected(self, server):
        """``git grep`` must not admit other subcommands sharing the prefix."""
        for cmd in ["git grepfoo", "git greppush"]:
            ok, _ = server.is_command_allowed(cmd)
            assert not ok, f"{cmd!r} must be rejected"

    def test_write_ops_still_rejected(self, server):
        """The read-only posture is unchanged by this addition."""
        for cmd in ["git commit -m x", "git push", "git reset --hard",
                    "git checkout .", "git clean -fd", "git add ."]:
            ok, _ = server.is_command_allowed(cmd)
            assert not ok, f"{cmd!r} must remain rejected"
