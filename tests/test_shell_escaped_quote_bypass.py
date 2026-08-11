"""
Regression tests for the escaped-quote / operator-desync allowlist bypass in
app.mcp_servers.shell_server.ShellServer._split_by_shell_operators.

Origin of these tests
---------------------
``_split_by_shell_operators`` walks the command tracking quote state so that a
shell operator appearing *inside* quotes is not mistaken for a segment
separator.  Its backslash branch handled only two escapes -- ``\\<newline>``
(line continuation) and ``\\```  -- so a backslash before a QUOTE fell through
to the quote handler and *toggled* ``in_single_quote`` / ``in_double_quote``.

Consequence: everything after ``\\'`` was treated as quoted data, so a
following ``;`` was not recognized as a separator::

    echo A \\' ; sudo reboot
      ->  segments: ["echo A \\' ; sudo reboot"]     (ONE segment)
      ->  verdict : allowed

A POSIX tokenizer disagrees.  ``shlex(posix=True, punctuation_chars=True)``
yields ``['echo', "'", ';', 'echo', 'INJECTED']`` -- the ``;`` IS a separator
and the trailing command IS a real command.  The validator's model of the
string diverged from the shell's.

Why it was exploitable
----------------------
The divergence is only *reachable* where a real shell runs the original
string.  Two execution routes exist:

  * non-heredoc -> ``_execute_pipeline`` orchestrates each segment with
    ``shell=False``, so the un-split tail is passed as literal argv to
    ``echo``.  Incorrect, but not a code-execution bypass.
  * heredoc -> ``_has_heredoc`` routes the whole command to ``sh -c``, and
    bash *does* honour that ``;``.  Verified by side effect: a hidden
    ``touch`` created its sentinel file only on this route.

``always_blocked`` did not backstop it -- ``ShellWriteChecker.check`` consumes
the same splitter, so it also returned allowed for the hidden ``sudo``.

Fix: outside single quotes, consume ``\\<any char>`` as a literal pair so an
escaped quote can never toggle quote state.

These tests lock in:
  * the splitter's segment boundaries for escaped single/double quotes,
  * agreement with a POSIX tokenizer on where separators fall,
  * end-to-end denial of the hidden command on BOTH routes, incl. heredoc,
  * that the write checker no longer inherits a broken split,
  * that genuinely-quoted operators and ``find -exec \\;`` still validate
    (the over-rejection failure mode),
  * that all six quote-state walkers agree on escape handling, so the bug
    cannot silently reappear in one of them.
"""

import shlex

import pytest

from app.mcp_servers.shell_server import (
    ShellServer,
    _consume_assignment_with_subst,
    _consume_raw_word,
    _find_substitution_spans,
)


@pytest.fixture
def server():
    return ShellServer()


def _segments(server, command):
    return [seg for _op, seg in server._split_by_shell_operators(command)]


class TestEscapedQuoteDoesNotToggleQuoteState:
    """The core defect: ``\\'`` must be a literal pair, not a quote opener."""

    def test_escaped_single_quote_then_semicolon_splits(self, server):
        segs = _segments(server, "echo A \\' ; ls")
        assert len(segs) == 2, (
            f"';' after an escaped single quote is a separator; got {segs!r}"
        )
        assert segs[1] == 'ls'

    def test_escaped_double_quote_then_semicolon_splits(self, server):
        segs = _segments(server, 'echo A \\" ; ls')
        assert len(segs) == 2, (
            f"';' after an escaped double quote is a separator; got {segs!r}"
        )
        assert segs[1] == 'ls'

    def test_escaped_single_quote_then_pipe_splits(self, server):
        segs = _segments(server, "echo A \\' | grep A")
        assert len(segs) == 2, f"'|' must remain a separator; got {segs!r}"

    def test_escaped_quote_inside_single_quotes_stays_literal(self, server):
        # Inside single quotes a backslash is literal and does NOT escape,
        # so the quote closes and the ';' IS a separator.
        segs = _segments(server, "echo 'a\\' ; ls")
        assert len(segs) == 2, (
            f"backslash is literal inside single quotes, so the quote closes "
            f"and ';' separates; got {segs!r}"
        )

    def test_unescaped_quote_still_suppresses_separator(self, server):
        # The inverse guard: a REAL quoted region must still hide the ';'.
        segs = _segments(server, "echo 'a ; b'")
        assert len(segs) == 1, (
            f"';' inside a genuine quoted string is data; got {segs!r}"
        )


class TestAgreementWithPosixTokenizer:
    """The splitter's separator positions must match a POSIX tokenizer's."""

    @pytest.mark.parametrize("command", [
        "echo A \\' ; echo INJECTED",
        'echo A \\" ; echo INJECTED',
        "echo a ; echo INJECTED",
    ])
    def test_semicolon_is_a_separator_for_both(self, server, command):
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        posix_tokens = list(lex)
        assert ';' in posix_tokens, (
            "precondition: POSIX tokenizer must see ';' as punctuation"
        )
        segs = _segments(server, command)
        assert len(segs) == 2, (
            f"POSIX sees a separator ({posix_tokens!r}) but splitter produced "
            f"{segs!r}"
        )


class TestHiddenCommandIsDenied:
    """End-to-end: the concealed command reaches the allowlist and is denied."""

    HEREDOC_EXPLOIT = (
        "cat <<'EOF'\n"
        "harmless body\n"
        "EOF\n"
        "echo A \\' ; sudo reboot"
    )

    def test_heredoc_route_denies_hidden_sudo(self, server):
        # This is the genuinely exploitable route: _has_heredoc sends the
        # whole string to `sh -c`, which honours the ';'.
        ok, reason = server.is_command_allowed(self.HEREDOC_EXPLOIT)
        assert not ok, "hidden command on the sh -c route must be denied"
        assert 'sudo' in reason, f"denial should name the hidden command: {reason!r}"

    def test_bare_escaped_single_quote_denies_hidden_sudo(self, server):
        ok, reason = server.is_command_allowed("echo A \\' ; sudo reboot")
        assert not ok, f"hidden command must be denied: {reason!r}"
        assert 'sudo' in reason

    def test_bare_escaped_double_quote_denies_hidden_sudo(self, server):
        ok, reason = server.is_command_allowed('echo A \\" ; sudo reboot')
        assert not ok, f"hidden command must be denied: {reason!r}"
        assert 'sudo' in reason

    def test_hidden_unknown_binary_denied(self, server):
        ok, reason = server.is_command_allowed(
            "echo A \\' ; definitely_not_a_real_cmd --flag"
        )
        assert not ok, f"hidden unknown binary must be denied: {reason!r}"
        assert 'definitely_not_a_real_cmd' in reason

    def test_hidden_destructive_outside_safe_paths_denied(self, server):
        # Layering note: ``is_command_allowed`` gates the command NAME, and
        # ``rm`` is deliberately allowlisted (see the destructive_commands
        # merge in __init__).  Path safety is ``ShellWriteChecker``'s job.
        # The bug was that a collapsed split hid this segment from BOTH
        # gates; assert against the gate that actually owns the decision.
        cmd = "echo A \\' ; rm -rf /etc"
        ok, reason = server.write_checker.check(
            cmd, server._split_by_shell_operators
        )
        assert not ok, (
            f"a destructive command concealed behind an escaped quote must be "
            f"seen by the write checker: {reason!r}"
        )
        assert '/etc' in reason, f"denial should name the unsafe path: {reason!r}"

    def test_hidden_destructive_inside_safe_path_still_permitted(self, server):
        # Inverse guard: the fix must not make the write checker refuse a
        # destructive command that targets a declared-safe path, which would
        # be over-rejection rather than a security win.
        ok, reason = server.write_checker.check(
            "echo A \\' ; rm -rf /tmp/scratch", server._split_by_shell_operators
        )
        assert ok, f"rm inside a safe write path must still be permitted: {reason!r}"

    def test_write_checker_sees_the_corrected_split(self, server):
        # The write checker consumes the same splitter, so a broken split
        # silently disarmed it too. Pin that it now observes both segments.
        ok, reason = server.write_checker.check(
            "echo A \\' ; sudo reboot", server._split_by_shell_operators
        )
        assert not ok, (
            f"write checker must observe the hidden segment, not inherit a "
            f"collapsed split: {reason!r}"
        )


class TestNoOverRejectionOfBenignCommands:
    """The opposite failure mode: quoted operators must still be data.

    Without these, a future 'fix' could pass the suite by refusing anything
    containing a backslash or a quoted operator -- re-breaking the very
    commands the earlier quote-awareness work fixed.
    """

    @pytest.mark.parametrize("command", [
        r'grep -E "\|" file',
        r"grep -E '\|' file",
        r'echo "a|b|c" | grep -E "\|"',
        r'find . -name x -exec rm {} \;',
        r'find . -type f -exec grep pat {} \;',
        'echo hi | grep h',
        'cd frontend && ls',
        'python3 -c "x=1\nprint(x)"',
        'echo "don\'t stop"',
        r'echo "a \" b"',
    ])
    def test_benign_command_still_validates(self, server, command):
        ok, reason = server.is_command_allowed(command)
        assert ok, f"benign command must still validate: {command!r} -> {reason!r}"

    @pytest.mark.parametrize("command,expected_segments", [
        (r'find . -name x -exec rm {} \;', 1),
        (r'echo a\; echo b', 1),
        (r'echo a\| echo b', 1),
    ])
    def test_escaped_operator_is_not_a_separator(
        self, server, command, expected_segments
    ):
        # ``\;`` / ``\|`` are literal characters, not operators. Pair-consumption
        # must preserve this (it is what made the old ``command[i-1] == '\\'``
        # branch redundant rather than load-bearing).
        segs = _segments(server, command)
        assert len(segs) == expected_segments, (
            f"escaped operator must stay literal; got {segs!r}"
        )


class TestAllQuoteWalkersAgreeOnEscapes:
    """Six independent quote-state walkers exist in this module.

    Only ``_split_by_shell_operators`` mishandled escapes; the others already
    carried an explicit escape flag. These assertions pin that agreement so a
    future edit to any one walker cannot reintroduce the divergence unnoticed.
    """

    def test_split_by_shell_operators(self, server):
        assert len(_segments(server, "echo A \\' ; ls")) == 2

    def test_comment_strip_loop_keeps_state(self, server):
        # If the inline comment-strip walker desynced, it would treat the
        # '#' line as data-inside-a-quote, keep it, and then validate it as
        # a command named '#sudo' -- which would be denied.
        ok, _reason = server.is_command_allowed("echo A \\'\n# a comment")
        assert ok, "escaped quote must not turn a comment line into data"

    def test_split_unquoted_lines(self, server):
        lines, unterminated = server._split_unquoted_lines("echo A \\'\nls")
        assert lines == ["echo A \\'", 'ls']
        assert not unterminated, (
            "an escaped quote must not leave the scanner inside a quote"
        )

    def test_consume_raw_word(self):
        assert _consume_raw_word("A=\\' next") == "A=\\'"

    def test_find_substitution_spans(self):
        spans = _find_substitution_spans("echo \\' $(ls)")
        bodies = [body for _s, _e, body, _k in spans]
        assert bodies == ['ls'], (
            f"an escaped quote must not conceal a command substitution: {bodies!r}"
        )

    def test_consume_assignment_with_subst(self):
        assert _consume_assignment_with_subst("V=$(echo \\' hi)") == "V=$(echo \\' hi)"
