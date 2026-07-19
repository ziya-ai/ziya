"""
Comprehensive behavioral matrix for ShellServer.is_command_allowed and its
supporting split/peel helpers.

Scope and layering
------------------
``is_command_allowed`` is the **allowlist + operator-splitting + git-safety +
substitution** validation layer.  It answers "is every command that the shell
would run on the allowlist (and every git subcommand a safe one)?".

It is deliberately NOT the write-policy layer: destructive *targets*
(``rm -rf /etc``), in-place edits (``sed -i``), and output redirection
(``> /etc/passwd``) pass this allowlist check and are gated separately by
``ShellWriteChecker`` (see tests/test_shell_write_checker.py and
tests/test_shell_destructive_safe_paths.py).  The cases below assert that
split — e.g. ``rm -rf /`` is *allowlist-allowed* here — so the two layers
stay decoupled and neither silently absorbs the other's responsibility.

Historically shell-validation fixes have arrived one reproducer at a time.
This module locks in the foundational contract as an explicit matrix so new
edge cases extend a documented baseline instead of rediscovering it.
"""

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


class TestBasicAllowlist:
    """Single-command allow/deny against the default allowlist."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la",
            "cat file.txt",
            "grep -n foo bar.py",
            "echo hello",
            "wc -l file",
            "pwd",
            "true",
            "seq 1 10",
        ],
    )
    def test_allowed_commands(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"{cmd!r} should be allowed: {reason}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "definitely_not_a_real_cmd",
            "make all",
            "gcc x.c",
            "vim file",
            "python2 x.py",
        ],
    )
    def test_disallowed_commands(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} should be rejected"
        assert "not allowed" in reason.lower()


class TestEmptyAndComment:
    """Empty, whitespace, and comment-only inputs."""

    def test_empty_string(self, server):
        ok, reason = server.is_command_allowed("")
        assert not ok and "empty" in reason.lower()

    def test_whitespace_only(self, server):
        ok, reason = server.is_command_allowed("   ")
        assert not ok and "empty" in reason.lower()

    def test_comment_only(self, server):
        ok, reason = server.is_command_allowed("# just a comment")
        assert not ok and "comment" in reason.lower()


class TestOperatorSplitting:
    """Pipelines / && / || / ; validate every segment independently."""

    def test_pipeline_all_allowed(self, server):
        ok, reason = server.is_command_allowed("echo hi | grep h | wc -l")
        assert ok, reason

    def test_pipeline_one_disallowed_rejected(self, server):
        ok, reason = server.is_command_allowed("echo hi | not_a_real_cmd")
        assert not ok
        assert "not_a_real_cmd" in reason

    def test_and_chain_allowed(self, server):
        ok, reason = server.is_command_allowed("echo a && ls && pwd")
        assert ok, reason

    def test_or_chain_disallowed_segment_rejected(self, server):
        ok, reason = server.is_command_allowed("ls || frobnicate")
        assert not ok
        assert "frobnicate" in reason

    def test_semicolon_sequence_rejects_blocked_tail(self, server):
        ok, reason = server.is_command_allowed("echo a ; sudo reboot")
        assert not ok
        assert "sudo" in reason


class TestSplitHelperSemantics:
    """_split_by_shell_operators returns (operator, segment) pairs and is
    quote-aware (an operator inside quotes is not a delimiter)."""

    def test_split_returns_operator_segment_pairs(self, server):
        pairs = server._split_by_shell_operators("echo a | grep b && ls")
        assert pairs == [("", "echo a"), ("|", "grep b"), ("&&", "ls")]

    def test_quoted_operator_not_split(self, server):
        pairs = server._split_by_shell_operators('echo "a|b" | wc')
        # The quoted pipe stays inside the first segment; only the real pipe
        # to ``wc`` is a delimiter.
        assert pairs == [("", 'echo "a|b"'), ("|", "wc")]


class TestNewlineAsSeparator:
    """A bare newline behaves like ``;`` — the tail must still be validated."""

    def test_newline_tail_disallowed_rejected(self, server):
        ok, reason = server.is_command_allowed("cat f\nsudo reboot")
        assert not ok
        assert "sudo" in reason

    def test_newline_both_allowed(self, server):
        ok, reason = server.is_command_allowed("echo one\necho two")
        assert ok, reason


class TestAlwaysBlocked:
    """always_blocked commands are rejected regardless of allowlist, and the
    basename form (absolute path) is caught too."""

    def test_sudo_rejected(self, server):
        ok, reason = server.is_command_allowed("sudo ls")
        assert not ok and "sudo" in reason

    def test_sudo_absolute_path_rejected(self, server):
        ok, reason = server.is_command_allowed("/usr/bin/sudo ls")
        assert not ok


class TestCompoundConstructs:
    """for/while/if/case bodies get dedicated validation."""

    def test_for_loop_allowed(self, server):
        ok, reason = server.is_command_allowed("for f in *; do echo $f; done")
        assert ok, reason

    def test_if_block_allowed(self, server):
        ok, reason = server.is_command_allowed("if true; then echo yes; fi")
        assert ok, reason

    def test_for_loop_with_disallowed_body_rejected(self, server):
        ok, reason = server.is_command_allowed(
            "for f in *; do frobnicate $f; done"
        )
        assert not ok
        assert "frobnicate" in reason


class TestHeredoc:
    """Heredoc bodies are stdin data, but commands sequenced after the
    terminator must still be validated."""

    def test_plain_heredoc_allowed(self, server):
        ok, reason = server.is_command_allowed("cat <<EOF\nhi there\nEOF")
        assert ok, reason

    def test_heredoc_body_not_validated_as_commands(self, server):
        # The body contains words that are not allowlisted commands; they are
        # data, so the heredoc must still validate.
        ok, reason = server.is_command_allowed(
            "cat <<EOF\nfrobnicate the gadget\nEOF"
        )
        assert ok, f"heredoc body should be treated as data: {reason}"

    def test_command_after_heredoc_terminator_validated(self, server):
        ok, reason = server.is_command_allowed(
            "cat <<EOF\nhi\nEOF\nsudo reboot"
        )
        assert not ok
        assert "sudo" in reason


class TestGitSafety:
    """Safe git subcommands pass; state-changing ones are rejected."""

    @pytest.mark.parametrize("cmd", ["git status", "git log", "git diff", "git show HEAD"])
    def test_safe_git_allowed(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"{cmd!r} should be allowed: {reason}"

    @pytest.mark.parametrize("cmd", ["git push origin main", "git commit -m x", "git reset --hard"])
    def test_unsafe_git_rejected(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} should be rejected"


class TestLayeringContract:
    """Allowlist layer is decoupled from the write-policy layer.

    These commands are *allowlist-allowed* here; their destructive effects
    are gated by ShellWriteChecker, exercised in its own test module.  If any
    of these starts returning False, the two layers have been conflated.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /tmp/x",          # destructive target — write-checker's job
            "echo hi > /tmp/out",     # redirection — write-checker's job
            "sed -i s/a/b/ file",     # in-place edit — write-checker's job
        ],
    )
    def test_destructive_passes_allowlist_layer(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, (
            f"{cmd!r} must pass the ALLOWLIST layer (write policy gates it "
            f"elsewhere); got denial: {reason}"
        )
