"""
Placement tests for the unterminated-quote guard in
``ShellServer.is_command_allowed``.

Why this file exists
--------------------
The guard must analyse quote balance on heredoc-STRIPPED text, not on the
raw command.  This distinction is easy to get wrong and was in fact got
wrong on the first attempt, in a way no other test in the suite detected.

A heredoc BODY is stdin *data*, not shell syntax.  Ordinary prose contains
apostrophes -- "it's fine", "O'Brien", "user's guide" -- so the body has an
odd number of quote characters and the PRE-STRIP quote state reads as
still open.  A guard placed before heredoc stripping therefore rejects
benign commands: measured at 6 of 7 realistic heredocs.  Running the same
guard on the stripped text rejects 0 of 7 while still closing every known
bypass payload.

The security direction is preserved: stripping removes only body content,
so a real quote imbalance in actual shell syntax (outside any body) is
still seen, and a command sequenced after the heredoc terminator is still
validated.

These tests pin the placement.  Without them, a future refactor could hoist
the guard above the heredoc-stripping step -- which looks tidier and is
strictly worse -- and the whole suite would stay green.
"""

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


class TestApostropheInHeredocBodyIsNotAnOpenQuote:
    """Body content must not contribute to quote-balance analysis."""

    @pytest.mark.parametrize('cmd', [
        "cat > /tmp/a.txt <<'EOF'\nit's fine\nEOF",
        "cat > /tmp/a.txt <<'EOF'\ndon't stop\nEOF",
        'cat > /tmp/a.txt <<EOF\nsay "hi\nEOF',
        "cat > /tmp/a.txt <<'EOF'\nO'Brien\nEOF\nls",
        "cat > /tmp/a.md <<'EOF'\nuser's guide\nEOF\necho done",
        "cat > /tmp/a.py <<'EOF'\nprint('a')\n# don't\nEOF",
        "cat > /tmp/a.js <<'EOF'\nconst s = \"it's\";\nEOF",
    ])
    def test_benign_heredoc_with_body_quotes_is_allowed(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, (
            'a quote character inside a heredoc body is data, not shell '
            f'syntax, and must not read as an unterminated quote. Got: {reason}'
        )

    def test_multiple_heredocs_with_body_quotes(self, server):
        cmd = ("cat > /tmp/a.txt <<'A'\nit's\nA\n"
               "cat > /tmp/b.txt <<'B'\nO'Brien\nB")
        ok, reason = server.is_command_allowed(cmd)
        assert ok, reason


class TestStrippingDoesNotWeakenEnforcement:
    """Guard placement must not create a hiding place."""

    def test_command_after_terminator_still_validated(self, server):
        ok, reason = server.is_command_allowed(
            "cat <<'EOF'\nit's fine\nEOF\nsudo reboot"
        )
        assert not ok, (
            'a stray quote in the body must not mask a command sequenced '
            'after the heredoc terminator'
        )
        assert 'sudo' in reason, reason

    def test_real_imbalance_outside_body_still_caught(self, server):
        """ANSI-C form after the terminator is still refused."""
        ok, _reason = server.is_command_allowed(
            "cat <<'EOF'\nbody\nEOF\necho $'\\'' ; /tmp/zz_evil.sh"
        )
        assert not ok

    def test_unterminated_quote_outside_body_still_caught(self, server):
        ok, _reason = server.is_command_allowed(
            "cat <<'EOF'\nbody\nEOF\necho 'unclosed ; sudo reboot"
        )
        assert not ok

    def test_body_quote_plus_valid_quoted_arg_after(self, server):
        """Body quotes and a real quoted argument coexist."""
        ok, reason = server.is_command_allowed(
            "cat <<'EOF'\nit's\nEOF\necho \"ok\""
        )
        assert ok, reason
