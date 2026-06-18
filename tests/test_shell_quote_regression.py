"""
Regression tests for quote- and newline-aware command validation in
app.mcp_servers.shell_server.ShellServer.

These lock in six fixes made after the validator misreported a literal
pipe inside double quotes as ``'"' is not allowed`` and rejected every
multiline ``python3 -c "..."`` payload:

  1. Pipe/operator splitting is quote-aware (_split_by_shell_operators),
     so an operator inside quotes is not treated as a segment delimiter.
  2-4. All three allowlist match sites use re.DOTALL, so ``.*`` in the
     ``^cmd(\\s+.*)?$`` patterns spans newlines inside a quoted argument.
  5. The comment-strip in is_command_allowed is quote-aware, so a
     ``#``-leading line *inside* a multiline quoted argument is preserved.
  6. The $()/backtick sub-validation masks single-quoted regions, so a
     literal ``$(`` or backtick inside single quotes is not validated as
     a real command substitution.
"""

import pytest

from app.mcp_servers.shell_server import ShellServer, _extract_command_substitutions


@pytest.fixture
def server():
    return ShellServer()


class TestQuoteAwareOperatorSplitting:
    """Fix #1: literal operators inside quotes are not split delimiters."""

    def test_literal_pipe_in_double_quotes_allowed(self, server):
        ok, reason = server.is_command_allowed('grep -E "\\|" file')
        assert ok, f"escaped pipe in double quotes should validate: {reason}"

    def test_literal_pipe_in_single_quotes_allowed(self, server):
        ok, reason = server.is_command_allowed("grep -E '\\|' file")
        assert ok, f"escaped pipe in single quotes should validate: {reason}"

    def test_real_pipe_to_quoted_literal_pipe(self, server):
        # The reproducer that started this: a real pipeline whose second
        # command contains a quoted literal pipe.
        ok, reason = server.is_command_allowed('echo "a|b|c" | grep -E "\\|"')
        assert ok, f"pipeline with quoted literal pipe should validate: {reason}"

    def test_real_pipe_still_validates_both_sides(self, server):
        # A genuine pipe to a disallowed command must still be rejected.
        ok, _ = server.is_command_allowed("echo hi | definitely_not_a_real_cmd")
        assert not ok, "pipe to a disallowed command must be rejected"


class TestDotallMultilineArguments:
    """Fixes #2-4: allowlist patterns match across newlines in arguments."""

    def test_multiline_python_c_allowed(self, server):
        cmd = 'python3 -c "x=1\nif x:\n    print(x)"'
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"multiline python3 -c should validate: {reason}"

    def test_newline_delimited_disallowed_command_still_blocked(self, server):
        # A real newline-delimited second command (outside quotes) is a
        # separate segment and must still be validated independently.
        ok, _ = server.is_command_allowed("echo hi\ndefinitely_not_a_real_cmd")
        assert not ok, "newline-delimited disallowed command must be rejected"


class TestQuoteAwareCommentStrip:
    """Fix #5: a '#' line inside a quoted argument is not stripped."""

    def test_hash_line_inside_python_c_preserved(self, server):
        cmd = 'python3 -c "x=1\n# a python comment\nprint(x)"'
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"'#' line inside quoted arg should validate: {reason}"

    def test_real_comment_only_command_rejected(self, server):
        ok, reason = server.is_command_allowed("# just a comment")
        assert not ok, "a comment-only command should be rejected"
        assert "comment" in reason.lower()


class TestSubstitutionSingleQuoteMasking:
    """Fix #6: '$(' / backtick inside single quotes is not a substitution."""

    def test_dollar_paren_in_single_quotes_not_substitution(self, server):
        # 'grep' is allowed; the $(foo) lives inside single quotes and must
        # be treated as a literal, not validated as a real substitution.
        ok, reason = server.is_command_allowed("grep '$(foo)' file")
        assert ok, f"$() inside single quotes should be literal: {reason}"

    def test_backtick_in_single_quotes_not_substitution(self, server):
        ok, reason = server.is_command_allowed("grep '`foo`' file")
        assert ok, f"backtick inside single quotes should be literal: {reason}"


class TestExtractCommandSubstitutions:
    """Unified quote- and nesting-aware substitution extractor.

    Replaces three duplicated ``re.findall(r'\\$\\(([^)]+)\\)', ...)`` sites
    that (a) truncated a $() body at the first ) inside a quote, and
    (b) stripped all backticks before the backtick findall, so backtick
    substitutions were never validated (a real enforcement gap).
    """

    def test_simple_dollar_paren(self):
        assert _extract_command_substitutions('echo "$(echo hi)"') == ['echo hi']

    def test_paren_inside_quoted_body_not_truncated(self):
        # The residual bug: ) inside a quoted substitution body used to
        # truncate the capture at the first ).
        assert _extract_command_substitutions("echo \"$(echo 'a) b')\"") == ["echo 'a) b'"]

    def test_nested_substitution_returns_outermost(self):
        # Only the outermost sub is returned; the inner one is revalidated
        # when the returned string is passed back through is_command_allowed.
        assert _extract_command_substitutions('echo $(echo $(date))') == ['echo $(date)']

    def test_backtick_substitution_extracted(self):
        assert _extract_command_substitutions('echo `date`') == ['date']

    def test_backtick_inside_double_quotes_extracted(self):
        assert _extract_command_substitutions('echo "`curl x`"') == ['curl x']

    def test_single_quoted_dollar_paren_ignored(self):
        assert _extract_command_substitutions("grep '$(foo)' f") == []

    def test_single_quoted_backtick_ignored(self):
        assert _extract_command_substitutions("grep '`foo`' f") == []

    def test_literal_paren_in_sed_not_substitution(self):
        assert _extract_command_substitutions("sed 's/)/X/' f") == []


class TestBacktickValidationGapClosed:
    """End-to-end: backtick substitutions are now validated (was a gap)."""

    def test_disallowed_command_in_backticks_rejected(self, server):
        # Before the refactor the main path stripped all backticks before
        # matching, so this slipped through validation entirely.
        ok, _ = server.is_command_allowed("echo `definitely_not_a_real_cmd`")
        assert not ok, "disallowed command in backticks must be rejected"

    def test_allowed_command_in_backticks_accepted(self, server):
        ok, reason = server.is_command_allowed("echo `date`")
        assert ok, f"allowed command in backticks should validate: {reason}"


class TestResolveSubstitutionsSpanBased:
    """_resolve_substitutions uses the shared span-finder (execution layer).

    These exercise the *executor*, not the validator. They run only harmless
    deterministic `echo` substitutions in /tmp, asserting on the spliced
    result string. The key case is the bypass closure: a $()/backtick inside
    single quotes is a bash literal the validator never checks, so the
    resolver must not execute it either.
    """

    CWD = "/tmp"
    TIMEOUT = 5.0

    def test_single_quoted_dollar_paren_not_executed(self, server):
        # Bypass closure: must be returned verbatim, NOT executed.
        r = server._resolve_substitutions("echo '$(echo PWNED)'", self.TIMEOUT, self.CWD)
        assert r == "echo '$(echo PWNED)'", r

    def test_single_quoted_backtick_not_executed(self, server):
        r = server._resolve_substitutions("grep '`id`' f", self.TIMEOUT, self.CWD)
        assert r == "grep '`id`' f", r

    def test_plain_dollar_paren_executed(self, server):
        r = server._resolve_substitutions("echo $(echo hi)", self.TIMEOUT, self.CWD)
        assert r == "echo hi", r

    def test_paren_inside_quoted_body_not_truncated(self, server):
        # The [^)]+ truncation bug: the ) inside the quoted body must not
        # end the substitution early.
        r = server._resolve_substitutions("echo \"$(echo 'a) b')\"", self.TIMEOUT, self.CWD)
        assert r == "echo \"a) b\"", r

    def test_nested_substitution_resolved_inner_first(self, server):
        # Old single-level regex left the inner $() as a literal; now it
        # resolves fully.
        r = server._resolve_substitutions("echo $(echo $(echo deep))", self.TIMEOUT, self.CWD)
        assert r == "echo deep", r

    def test_backtick_executed(self, server):
        r = server._resolve_substitutions("echo `echo bt`", self.TIMEOUT, self.CWD)
        assert r == "echo bt", r
