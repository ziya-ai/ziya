"""
Regression tests for quote-aware logical-line splitting in the heredoc
validation branch of app.mcp_servers.shell_server.ShellServer.

Origin of these tests
----------------------
The validator rejected a benign command that wrote a JS file via heredoc and
then ran ``node -e "<multi-line JS>"``, reporting ``'const' is not allowed``.

Root cause: the heredoc branch of ``is_command_allowed`` split the
heredoc-stripped command with a raw ``stripped.split('\\n')``.  That is a
byte-level split with no quote awareness.  A multi-line quoted argument that
legitimately *follows* the heredoc terminator survives heredoc-stripping
intact (correctly -- it is not a heredoc body), but the naive split then
shredded ``node -e "\\nconst x = 1;\\n"`` into the fragments ``node -e "``,
``const x = 1;`` and ``"``.  Each fragment was recursively fed back through
``is_command_allowed``, so a line of JavaScript was validated as if it were a
command word.

Fix: ``_split_unquoted_lines`` walks the text tracking quote state and breaks
only on newlines seen outside quotes, mirroring the quote tracking the
non-heredoc validation path already performed.  It additionally reports
whether the text ended while still inside a quote; the heredoc branch refuses
such a command outright, because a command hidden behind an unclosed quote
would otherwise be swallowed as quoted data and never reach the allowlist.

These tests lock in:
  * the splitter's boundaries (unquoted vs quoted newlines, escapes,
    apostrophes inside double quotes, unterminated-quote reporting),
  * end-to-end acceptance of the reported reproducer,
  * that the security property the branch exists for still holds: a
    disallowed command sequenced after a heredoc terminator is rejected,
  * that an unclosed quote cannot be used to smuggle a command past the
    allowlist.
"""

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


class TestSplitUnquotedLines:
    """The quote-aware logical-line scanner underpinning the fix."""

    def test_plain_newlines_split(self):
        lines, unterminated = ShellServer._split_unquoted_lines("a\nb\nc")
        assert lines == ["a", "b", "c"]
        assert unterminated is False

    def test_newline_inside_double_quotes_is_data(self):
        lines, unterminated = ShellServer._split_unquoted_lines('node -e "a\nb"')
        assert lines == ['node -e "a\nb"']
        assert unterminated is False

    def test_newline_inside_single_quotes_is_data(self):
        lines, unterminated = ShellServer._split_unquoted_lines("node -e 'a\nb'")
        assert lines == ["node -e 'a\nb'"]
        assert unterminated is False

    def test_split_resumes_after_quote_closes(self):
        lines, unterminated = ShellServer._split_unquoted_lines('echo "a\nb"\nls')
        assert lines == ['echo "a\nb"', "ls"]
        assert unterminated is False

    def test_escaped_double_quote_does_not_flip_state(self):
        # The \" is data inside the quoted run; the quote still closes at the
        # final ", so the following newline is a real separator.
        lines, unterminated = ShellServer._split_unquoted_lines('echo "a \\" b"\nls')
        assert lines == ['echo "a \\" b"', "ls"]
        assert unterminated is False

    def test_apostrophe_inside_double_quotes_does_not_flip_state(self):
        # A naive tracker treats the ' in don't as opening a single-quoted
        # run and then mis-attributes every following line to it.
        lines, unterminated = ShellServer._split_unquoted_lines('echo "don\'t stop"\nls')
        assert lines == ['echo "don\'t stop"', "ls"]
        assert unterminated is False

    def test_unterminated_double_quote_reported(self):
        lines, unterminated = ShellServer._split_unquoted_lines('echo "unclosed\nls')
        assert unterminated is True
        # Everything after the unclosed quote was swallowed as one run.
        assert lines == ['echo "unclosed\nls']

    def test_unterminated_single_quote_reported(self):
        _lines, unterminated = ShellServer._split_unquoted_lines("echo 'unclosed\nls")
        assert unterminated is True

    def test_backslash_is_literal_inside_single_quotes(self):
        # Inside single quotes a backslash does not escape, so the closing
        # quote here really does close the run.
        lines, unterminated = ShellServer._split_unquoted_lines("echo 'a\\'\nls")
        assert unterminated is False
        assert lines == ["echo 'a\\'", "ls"]


class TestHeredocFollowedByMultilineQuotedArgument:
    """The reported reproducer: heredoc, then node -e with multi-line JS."""

    REPORTED = (
        "cd frontend && cat > /tmp/mathfix/scan.js <<'EOF'\n"
        "function f() { return 1; }\n"
        "module.exports = { f };\n"
        "EOF\n"
        'node -e "\n'
        "const x = 1;\n"
        "console.log(x);\n"
        '"\n'
    )

    def test_reported_command_validates(self, server):
        ok, reason = server.is_command_allowed(self.REPORTED)
        assert ok, f"heredoc followed by multiline node -e should validate: {reason}"

    def test_js_body_line_is_not_reported_as_a_command(self, server):
        ok, reason = server.is_command_allowed(self.REPORTED)
        assert ok or "const" not in reason, (
            "a JavaScript line inside a quoted argument must never be "
            f"validated as a command word: {reason}"
        )

    def test_heredoc_then_multiline_python_c(self, server):
        cmd = (
            "cat > /tmp/b.txt <<'EOF'\n"
            "x\n"
            "EOF\n"
            'python3 -c "\n'
            "if True:\n"
            "    print('ok')\n"
            '"\n'
        )
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"heredoc followed by multiline python3 -c should validate: {reason}"

    def test_plain_heredoc_still_validates(self, server):
        cmd = "cat > /tmp/a.txt <<'EOF'\nhello\nEOF\n"
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"plain heredoc should validate: {reason}"


class TestHeredocSecurityPropertyPreserved:
    """The branch exists to validate commands sequenced after a heredoc."""

    def test_disallowed_command_after_terminator_rejected(self, server):
        cmd = "cat <<'EOF'\ndata\nEOF\nsudo reboot\n"
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, "a disallowed command after the heredoc terminator must be rejected"
        assert "sudo" in reason

    def test_disallowed_command_semicolon_sequenced_rejected(self, server):
        cmd = "cat <<'EOF'\ndata\nEOF\nls; sudo reboot\n"
        ok, _reason = server.is_command_allowed(cmd)
        assert not ok, "a disallowed command sequenced with ; must be rejected"

    def test_unclosed_quote_cannot_smuggle_a_command(self, server):
        # Without the unterminated-quote refusal, the sudo line is swallowed
        # as quoted data and never reaches the allowlist.
        cmd = "cat <<'EOF'\ndata\nEOF\necho \"unclosed\nsudo reboot\n"
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, "an unterminated quote must not let a command skip the allowlist"
        assert "unterminated quote" in reason

    def test_disallowed_command_inside_quoted_run_is_not_executed_as_command(self, server):
        # A closed quoted run containing the word sudo is data, not a command,
        # so it is allowed -- echo never executes it. This pins that the fix
        # does not over-reject by string-matching inside quoted arguments.
        cmd = "cat <<'EOF'\ndata\nEOF\necho \"sudo reboot\"\n"
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"a quoted literal is data, not a command: {reason}"
