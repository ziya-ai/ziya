r"""
Supplementary regression tests for the escaped-quote / operator-desync
allowlist bypass in
``app.mcp_servers.shell_server.ShellServer._split_by_shell_operators``.

Relationship to test_shell_escaped_quote_bypass.py
--------------------------------------------------
That file pins the bug as originally found: an escaped quote followed by
``;``, on both the non-heredoc and heredoc execution routes, plus
cross-walker escape agreement.  It does NOT cover the other *separators*
or the other *validation routes*, and the desync is a property of the
shared splitter rather than of ``;`` specifically -- so every separator
and every route that consumes the splitter is a distinct way for the same
bug to come back.  This file closes that matrix.

Every test here was verified to be a real guard: with
``_split_by_shell_operators`` reverted to its pre-fix branch (which
consumed only ``\<newline>`` and ``\```), each payload below is ALLOWED,
and with the fix applied each is DENIED.  A test that passes either way
would be worthless as a regression guard, so one candidate
(``echo "A \' B" ; sudo reboot`` -- denied both before and after, because
the escape sits inside a double-quoted run) was deliberately dropped
rather than kept to inflate the count.

Separator matrix
----------------
The pre-fix walker flipped quote state on ``\'``, so EVERY subsequent
separator was swallowed as quoted data, not just ``;``::

    echo A \' && sudo reboot     -> one segment, allowed
    echo A \' || sudo reboot     -> one segment, allowed
    echo A \' |  sudo reboot     -> one segment, allowed
    echo A \'<newline>sudo ...   -> one segment, allowed

Route matrix
------------
``_validate_compound_body`` (for/while/if/case) and the ``$(...)``
substitution validator both call the same splitter, so both inherit the
same desync.  They report denial differently (no pipeline-segment
suffix; a "in command substitution" prefix), which is why these assert on
the boolean verdict and on ``sudo`` appearing in the reason, not on an
exact message.

Not-a-bug case, pinned deliberately
-----------------------------------
``echo A \\' ; sudo reboot`` (a DOUBLE backslash) validates as a single
segment and is ALLOWED -- and that is correct.  ``\\`` is an escaped
backslash, so the following ``'`` is a real quote opener and the rest of
the line is inside an unterminated single quote.  ``sh -n`` agrees:
"unexpected EOF while looking for matching ``'``".  The hidden command is
unreachable because the shell refuses to parse the string at all.  This
is pinned so a future "hardening" pass does not mistake it for the same
bug and over-reject valid escaped-backslash usage.
"""

import subprocess

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


def _segments(server, cmd):
    return [seg for _op, seg in server._split_by_shell_operators(cmd)]


class TestEscapedQuoteAcrossEverySeparator:
    """The desync swallowed every separator, not only ``;``."""

    @pytest.mark.parametrize("cmd", [
        "echo A \\' && sudo reboot",
        "echo A \\' || sudo reboot",
        "echo A \\' | sudo reboot",
        "echo A \\'\nsudo reboot",
        'echo A \\" && sudo reboot',
        'echo A \\" || sudo reboot',
    ])
    def test_hidden_command_after_separator_is_denied(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, (
            f"escaped quote must not hide the command after the separator: {cmd!r}"
        )
        assert 'sudo' in reason, (
            f"denial should name the hidden command, got: {reason!r}"
        )

    @pytest.mark.parametrize("cmd", [
        "echo A \\' && sudo reboot",
        "echo A \\' || sudo reboot",
        "echo A \\' | sudo reboot",
        "echo A \\'\nsudo reboot",
    ])
    def test_splitter_produces_two_segments(self, server, cmd):
        # The verdict above could in principle come from some other guard;
        # assert the split itself is right so the fix is pinned at its cause.
        segs = _segments(server, cmd)
        assert len(segs) == 2, f"expected 2 segments for {cmd!r}, got {segs}"
        assert segs[1] == 'sudo reboot', f"unexpected tail segment: {segs}"


class TestOtherValidationRoutesInheritTheFix:
    """_validate_compound_body and the $() validator use the same splitter."""

    def test_compound_body_route_denies_hidden_command(self, server):
        cmd = "for i in 1; do echo A \\' ; sudo reboot; done"
        assert server._is_compound_command(cmd), (
            "precondition: this must take the compound-validation route"
        )
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, "compound body must not hide a command behind \\'"
        assert 'sudo' in reason, f"denial should name sudo, got: {reason!r}"

    def test_command_substitution_route_denies_hidden_command(self, server):
        cmd = "echo $(echo A \\' ; sudo reboot)"
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, "substitution body must not hide a command behind \\'"
        # This route reports via the substitution wrapper rather than naming
        # sudo directly, so assert on the route, not the exact token.
        assert 'substitution' in reason.lower() or 'sudo' in reason, (
            f"unexpected denial reason: {reason!r}"
        )


class TestEscapedBackslashIsNotTheSameBug:
    """``\\\\'`` opens a REAL quote; the shell itself refuses to parse it."""

    PAYLOAD = "echo A \\\\' ; sudo reboot"

    def test_shell_rejects_it_as_a_syntax_error(self):
        # Ground truth, so this test documents shell behaviour rather than
        # asserting our own model of it. -n = parse only, never execute.
        r = subprocess.run(
            ['sh', '-n', '-c', self.PAYLOAD],
            capture_output=True, text=True,
        )
        assert r.returncode != 0, (
            "precondition: sh must consider the double-backslash payload a "
            "syntax error (unterminated quote), making sudo unreachable"
        )

    def test_validator_reads_it_as_one_segment(self, server):
        # Correct behaviour: the quote really is open, so there is no
        # second command to validate. Pinned so a later hardening pass does
        # not "fix" this into an over-rejection of escaped backslashes.
        segs = _segments(server, self.PAYLOAD)
        assert len(segs) == 1, f"expected a single segment, got {segs}"

    def test_heredoc_route_catches_it_via_unterminated_quote(self, server):
        # On the heredoc route the same payload IS refused, by the
        # unterminated-quote guard rather than by segment splitting.
        cmd = "cat <<'EOF'\nbody\nEOF\necho A \\\\' ; sudo reboot"
        ok, reason = server.is_command_allowed(cmd)
        assert not ok, "unterminated quote on the heredoc route must be refused"
        assert 'unterminated' in reason.lower(), (
            f"expected the unterminated-quote guard, got: {reason!r}"
        )


class TestNoOverRejection:
    """The fix must not start rejecting legitimate escape usage."""

    @pytest.mark.parametrize("cmd", [
        # Escaped operators stay literal (find -exec is the canonical case).
        "find . -name x -exec rm {} \\;",
        "echo a\\; echo b",
        "echo a\\| echo b",
        # Escaped backtick: the one escape the pre-fix branch handled.
        "echo A \\` file",
        # Genuinely quoted operators are still data, not separators.
        'grep -E "\\|" file',
        "echo \"a|b\" | grep -E \"\\|\"",
        # Escaped quotes in ordinary argument positions.
        'echo "a \\" b"',
        "echo \"don't stop\"",
    ])
    def test_benign_escape_usage_still_validates(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f"benign escaped usage must still validate: {cmd!r} -> {reason}"

    @pytest.mark.parametrize("cmd,expected", [
        ("find . -name x -exec rm {} \\;", 1),
        ("echo a\\; echo b", 1),
        ("echo a\\| echo b", 1),
    ])
    def test_escaped_operator_is_not_a_separator(self, server, cmd, expected):
        segs = _segments(server, cmd)
        assert len(segs) == expected, (
            f"escaped operator must not split {cmd!r}, got {segs}"
        )
