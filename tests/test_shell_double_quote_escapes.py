"""Backslash escapes inside double quotes follow bash, not ``shlex``.

Inside double quotes bash removes a backslash before exactly ``$``, `` ` ``,
``"``, ``\\`` (and newline); before anything else the backslash is kept.
``shlex.split`` applies the rule only to ``"`` and ``\\``, so the shell
server handed programs ``\\`` + backtick and ``\\$`` with the backslash
intact.  Observed 2026-10-14 in the kuiper-telemetry skill::

    python3 graf_q.py "SELECT ... FROM \\`kuipernetwork0-PAA\\` WHERE ..."

reached ClickHouse as ``\\`kuipernetwork0-PAA\\``` and failed with
``Syntax error: failed at position 37 (\\)``.  The agent's workaround was to
write every quoted-identifier query to a file first -- a "known trap" that
should not exist.

A second half of the same rule: ``_expand_vars`` and
``_expand_special_params`` run BEFORE tokenizing and are quote-naive, so
``"\\$HOME"`` was expanded instead of being delivered as the literal
``$HOME``.  Both halves are asserted on the executor's outermost surface
(the child's stdout), and the single-quote case is pinned so the fix does
not over-reach: inside single quotes a backslash is always literal.
"""
from __future__ import annotations

import os

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


def _run(server, cmd, cwd="/tmp"):
    return server._execute_pipeline(cmd, timeout=10, cwd=cwd)


class TestDoubleQuotedEscapes:
    def test_escaped_backtick_is_a_literal_backtick(self, server):
        # The graf_q.py shape: a SQL quoted identifier inside a double-quoted
        # argument.  No command substitution must run, and no backslash may
        # survive into the argument.
        r = _run(server, 'printf "%s\\n" "SELECT 1 FROM \\`kuipernetwork0-PAA\\` WHERE x"')
        assert r.returncode == 0, r.stderr
        assert r.stdout == "SELECT 1 FROM `kuipernetwork0-PAA` WHERE x\n"

    def test_escaped_dollar_is_a_literal_dollar(self, server):
        os.environ["ZIYA_DQ_TEST_VAR"] = "EXPANDED"
        try:
            r = _run(server, 'printf "%s\\n" "cost: \\$ZIYA_DQ_TEST_VAR \\$5"')
        finally:
            del os.environ["ZIYA_DQ_TEST_VAR"]
        assert r.returncode == 0, r.stderr
        assert r.stdout == "cost: $ZIYA_DQ_TEST_VAR $5\n"

    def test_escaped_special_params_are_literal(self, server):
        # ``$?`` / ``$$`` are expanded by a separate pre-pass; an escaped one
        # must survive it too.
        r = _run(server, 'true; printf "%s\\n" "rc=\\$? pid=\\$$"')
        assert r.returncode == 0, r.stderr
        assert r.stdout == "rc=$? pid=$$\n"

    def test_unescaped_dollar_still_expands(self, server):
        # Positive control: the fix protects only ESCAPED dollars.
        os.environ["ZIYA_DQ_TEST_VAR"] = "EXPANDED"
        try:
            r = _run(server, 'printf "%s\\n" "v=$ZIYA_DQ_TEST_VAR"')
        finally:
            del os.environ["ZIYA_DQ_TEST_VAR"]
        assert r.stdout == "v=EXPANDED\n"

    def test_other_backslashes_are_kept_in_double_quotes(self, server):
        # bash keeps the backslash before a character that is not special:
        # "\z" stays two characters, and printf's own "\n" must still arrive
        # as backslash-n for printf to interpret.
        r = _run(server, 'printf "%s\\n" "a\\zb"')
        assert r.stdout == "a\\zb\n"

    def test_escaped_quote_and_backslash_unchanged(self, server):
        r = _run(server, 'printf "%s\\n" "say \\"hi\\" \\\\ done"')
        assert r.stdout == 'say "hi" \\ done\n'


class TestSingleQuotesUntouched:
    def test_backslash_literal_inside_single_quotes(self, server):
        # ``'\`'`` is three characters in bash: backslash, backtick, nothing
        # removed.  The fix must not reach into single quotes.
        r = _run(server, "printf '%s\\n' 'a \\` \\$X b'")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "a \\` \\$X b\n"


class TestUnquotedEscapes:
    def test_unquoted_escaped_backtick_and_dollar(self, server):
        # Outside quotes shlex already drops the backslash; the pre-pass must
        # agree with it, not double-handle.
        r = _run(server, 'printf "%s|" \\`x\\` \\$y')
        assert r.stdout == "`x`|$y|"


class TestSubstitutionStillWorks:
    def test_real_backtick_substitution_in_double_quotes(self, server):
        # Guard against over-tightening: an UNESCAPED backtick inside double
        # quotes is still a command substitution.
        r = _run(server, 'printf "%s\\n" "v=`printf sub`"')
        assert r.stdout == "v=sub\n"
