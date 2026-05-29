"""Slice B — per-task shell command grants.

Covers the four layers the grant has to traverse end-to-end:

1. **Matcher** (``ShellWriteChecker._task_scope_grants_command``):
   literal first-token match, ``re:`` regex match, basename match,
   malformed-regex tolerance.

2. **Bypass hooks** (``ShellWriteChecker.check``):
   destructive bypass, interpreter bypass, hard-ceiling preservation
   (``always_blocked`` and redirection still win over a grant).

3. **Wire envelope** (``tool_execution.build_task_scope_envelope``):
   ``shell_commands`` makes it onto the envelope when the ContextVar
   is set; absent when not.

4. **Executor handoff** (``task_executor`` set/reset of the
   ``_task_shell_commands`` ContextVar): tested by exercising the
   public ``app.context`` surface that the executor uses, since the
   full executor path requires substantial async fixtures.

Hard-ceiling preservation is the security-critical case: a grant for
``sudo`` MUST NOT unlock ``sudo whoami``; a grant for ``echo`` MUST
NOT unlock ``echo x > /etc/passwd``.  Those tests fail loudly if
future refactors accidentally widen the bypass.
"""
from __future__ import annotations

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker


# --------------------------------------------------------------------------- #
# Layer 1: matcher
# --------------------------------------------------------------------------- #

@pytest.fixture
def project_root(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / ".ziya").mkdir()
    return str(proj)


@pytest.fixture
def policy_manager(project_root):
    pm = WritePolicyManager()
    pm.load_for_project("test-project", project_root)
    return pm


@pytest.fixture
def checker(policy_manager) -> ShellWriteChecker:
    """Fresh checker per test so ``_task_scope`` mutations don't leak."""
    return ShellWriteChecker(policy_manager)


def _simple_split(cmd):
    """Match the split-fn shape ``ShellWriteChecker.check`` expects."""
    return [(None, cmd)]


class TestMatcher:
    """``_task_scope_grants_command`` in isolation."""

    def test_no_scope_no_grant(self, checker):
        # Default state: no task scope active → never grants.
        assert checker._task_scope_grants_command("rm /tmp/x") is False

    def test_empty_grants_no_match(self, checker):
        checker.set_task_scope({"shell_commands": []})
        assert checker._task_scope_grants_command("rm /tmp/x") is False

    def test_literal_first_token(self, checker):
        checker.set_task_scope({"shell_commands": ["pytest"]})
        assert checker._task_scope_grants_command("pytest -xvs tests/") is True
        assert checker._task_scope_grants_command("py.test tests/") is False

    def test_literal_basename_match(self, checker):
        # ``./run.sh`` first token is ``./run.sh``; the grant is just
        # ``run.sh`` — the matcher falls back to the basename so the
        # natural human grant works without forcing the user to write
        # ``./run.sh`` literally.
        checker.set_task_scope({"shell_commands": ["run.sh"]})
        assert checker._task_scope_grants_command("./run.sh --verbose") is True

    def test_regex_grant(self, checker):
        checker.set_task_scope({
            "shell_commands": [r"re:^make\s+test(:\w+)?$"]
        })
        assert checker._task_scope_grants_command("make test") is True
        assert checker._task_scope_grants_command("make test:unit") is True
        assert checker._task_scope_grants_command("make all") is False

    def test_regex_full_command_line(self, checker):
        # Regex matches anywhere in the command (re.search semantics)
        # so an anchored pattern is the user's responsibility.
        checker.set_task_scope({"shell_commands": [r"re:--dry-run"]})
        assert checker._task_scope_grants_command(
            "deploy.sh --dry-run prod"
        ) is True

    def test_malformed_regex_skipped(self, checker):
        # A bogus regex is silently skipped so a typo can't crash
        # the policy decision for every subsequent command.  The
        # well-formed sibling grant still works.
        checker.set_task_scope({
            "shell_commands": ["re:[unterminated", "pytest"]
        })
        assert checker._task_scope_grants_command("pytest") is True
        assert checker._task_scope_grants_command("nope") is False

    def test_unbalanced_quotes_dont_crash(self, checker):
        # shlex raises on unbalanced quotes; matcher must fall back
        # to whitespace tokenization rather than propagate.
        checker.set_task_scope({"shell_commands": ["echo"]})
        assert checker._task_scope_grants_command('echo "unterminated') is True

    def test_blank_grant_entries_ignored(self, checker):
        checker.set_task_scope({"shell_commands": ["", "   ", "pytest"]})
        assert checker._task_scope_grants_command("pytest") is True


# --------------------------------------------------------------------------- #
# Layer 2: bypass hooks in ``check``
# --------------------------------------------------------------------------- #

class TestBypass:
    """End-to-end through ``ShellWriteChecker.check``."""

    def test_rm_blocked_without_grant(self, checker):
        ok, reason = checker.check("rm /etc/hosts", _simple_split)
        assert ok is False
        assert "rm" in reason.lower() or "destructive" in reason.lower()

    def test_rm_allowed_with_grant(self, checker):
        checker.set_task_scope({"shell_commands": ["rm"]})
        ok, reason = checker.check("rm /etc/hosts", _simple_split)
        assert ok is True, f"grant should bypass destructive check, got: {reason}"

    def test_cp_allowed_with_grant(self, checker):
        checker.set_task_scope({"shell_commands": ["cp"]})
        ok, _ = checker.check("cp src.py dst.py", _simple_split)
        assert ok is True

    def test_grant_does_not_unlock_always_blocked(self, checker):
        # Security-critical: a per-task grant for ``sudo`` MUST NOT
        # let the model run sudo.  ``always_blocked`` is a hard
        # ceiling above any task grant.
        checker.set_task_scope({"shell_commands": ["sudo"]})
        ok, reason = checker.check("sudo whoami", _simple_split)
        assert ok is False, "sudo must remain hard-blocked even with grant"
        assert "sudo" in reason.lower() or "blocked" in reason.lower()

    def test_grant_does_not_unlock_redirection(self, checker):
        # Security-critical: a grant for ``echo`` MUST NOT let the
        # model write to arbitrary paths via output redirection.
        # ``/etc/passwd`` is outside the safe-write paths.
        checker.set_task_scope({"shell_commands": ["echo"]})
        ok, reason = checker.check("echo malicious > /etc/passwd", _simple_split)
        assert ok is False, "redirection must remain blocked even with grant"

    def test_grant_with_safe_redirection(self, checker):
        # Sanity: redirection to a safe-write path is fine; the grant
        # is irrelevant in this case (echo isn't destructive) but the
        # combination should still pass.
        checker.set_task_scope({"shell_commands": ["echo"]})
        ok, _ = checker.check("echo hi > /tmp/note", _simple_split)
        assert ok is True

    def test_grant_clears_after_clear_task_scope(self, checker):
        # Use a path outside safe_write_paths so the destructive
        # check actually fires when the grant is absent.  ``/tmp/x``
        # would slip through on its path alone.
        checker.set_task_scope({"shell_commands": ["rm"]})
        assert checker.check("rm /etc/hosts", _simple_split)[0] is True
        checker.clear_task_scope()
        assert checker.check("rm /etc/hosts", _simple_split)[0] is False


# --------------------------------------------------------------------------- #
# Layer 3: envelope shape (built inline in tool_execution.py)
# --------------------------------------------------------------------------- #

class TestEnvelope:
    """The envelope is built inline in ``tool_execution`` from three
    ContextVars; this layer pins down that all three feed in correctly
    and that an empty ContextVar produces a falsy envelope key.
    """

    def test_all_three_contextvars_default_falsy(self):
        from app.context import (
            get_task_writable_paths,
            get_task_readable_paths,
            get_task_shell_commands,
        )
        # Baseline: no task scope active.  ``tool_execution`` builds
        # the envelope only when at least one of these is truthy.
        assert not get_task_writable_paths()
        assert not get_task_readable_paths()
        assert not get_task_shell_commands()

    def test_shell_commands_alone_makes_envelope_truthy(self):
        from app.context import (
            set_task_shell_commands, reset_task_shell_commands,
            get_task_writable_paths, get_task_readable_paths,
            get_task_shell_commands,
        )
        token = set_task_shell_commands(["pytest", "re:^make\\s"])
        try:
            # Mirror the inline guard in tool_execution:
            #   if _twp or _trp or _tsc: build envelope
            twp = get_task_writable_paths()
            trp = get_task_readable_paths()
            tsc = get_task_shell_commands()
            assert bool(twp or trp or tsc) is True
            assert tsc == ["pytest", "re:^make\\s"]
        finally:
            reset_task_shell_commands(token)

    def test_shell_commands_coexist_with_writable(self):
        from app.context import (
            set_task_writable_paths, reset_task_writable_paths,
            set_task_shell_commands, reset_task_shell_commands,
            get_task_writable_paths, get_task_shell_commands,
        )
        wtoken = set_task_writable_paths([{"path": "/tmp/x", "is_dir": False}])
        stoken = set_task_shell_commands(["rm"])
        try:
            assert get_task_writable_paths() == [{"path": "/tmp/x", "is_dir": False}]
            assert get_task_shell_commands() == ["rm"]
        finally:
            reset_task_shell_commands(stoken)
            reset_task_writable_paths(wtoken)


# --------------------------------------------------------------------------- #
# Layer 4: executor handoff (via the ContextVar surface it uses)
# --------------------------------------------------------------------------- #

class TestContextVarRoundTrip:
    """Token-based set/reset semantics — same surface task_executor uses."""

    def test_set_get_reset(self):
        from app.context import (
            set_task_shell_commands, get_task_shell_commands,
            reset_task_shell_commands,
        )
        assert get_task_shell_commands() is None
        token = set_task_shell_commands(["pytest"])
        try:
            assert get_task_shell_commands() == ["pytest"]
        finally:
            reset_task_shell_commands(token)
        assert get_task_shell_commands() is None

    def test_nested_scopes_restore_outer(self):
        # task_executor for nested tasks (parallel/repeat) must restore
        # the outer scope when an inner task's finally fires.
        from app.context import (
            set_task_shell_commands, get_task_shell_commands,
            reset_task_shell_commands,
        )
        outer = set_task_shell_commands(["outer"])
        try:
            inner = set_task_shell_commands(["inner"])
            try:
                assert get_task_shell_commands() == ["inner"]
            finally:
                reset_task_shell_commands(inner)
            assert get_task_shell_commands() == ["outer"]
        finally:
            reset_task_shell_commands(outer)
        assert get_task_shell_commands() is None

    def test_none_clears(self):
        from app.context import (
            set_task_shell_commands, get_task_shell_commands,
            reset_task_shell_commands,
        )
        token = set_task_shell_commands(None)
        try:
            assert get_task_shell_commands() is None
        finally:
            reset_task_shell_commands(token)
