"""Substitution output is data, not shell syntax.

``_resolve_substitutions`` splices a ``$(...)`` body's stdout back into the
segment text, which is then re-tokenized, re-scanned for redirections and
peeled for ``NAME=value`` prefixes.  Raw output was therefore re-parsed::

    LB=$(aws elbv2 describe-load-balancers ... --output text); echo "$LB"

with two matching load balancers produced ``LB=arn1\\narn2``; ``LB=arn1``
was peeled as the assignment and ``arn2`` ran as a command, surfacing as
``FileNotFoundError: 'arn:aws:...'`` out of the whole tool call.

Bash never word-splits an assignment RHS or a ``"$(...)"``, and never
re-parses quotes or ``>`` out of substitution output.  These tests pin that
on the executor's outermost surface (stdout / filesystem), plus the
shell-style exit 127 for a missing executable.
"""
from __future__ import annotations

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


def _run(server, cmd, cwd=None):
    return server._execute_pipeline(cmd, timeout=10, cwd=cwd)


class TestAssignmentIsOneWord:
    def test_multiline_output_assigned_whole(self, server):
        r = _run(server, 'LB=$(printf "arn:one\\narn:two\\n"); echo "LB=$LB"')
        assert r.returncode == 0, r.stderr
        assert r.stdout == "LB=arn:one\narn:two\n"

    def test_output_with_spaces_assigned_whole(self, server):
        r = _run(server, "V=$(printf 'a b  c'); printf '[%s]' \"$V\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "[a b  c]"

    def test_single_quote_in_output(self, server):
        r = _run(server, 'V=$(printf "it'"'"'s"); echo "$V"')
        assert r.returncode == 0, r.stderr
        assert r.stdout == "it's\n"

    def test_redirect_char_in_output_is_not_a_redirection(self, server, tmp_path):
        target = tmp_path / "should_not_exist"
        r = _run(server, f"V=$(printf '>{target}'); echo \"$V\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == f">{target}\n"
        assert not target.exists(), "substitution output was parsed as a redirection"

    def test_leading_assignment_prefix(self, server):
        # ``A=1 V=$(...) cmd``: V is the second leading assignment word.
        r = _run(server, "A=1 V=$(printf 'x y') printf '[%s]' \"$V\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "[x y]"

    def test_assignment_with_prefix_text(self, server):
        r = _run(server, "V=pre-$(printf 'a b'); echo \"$V\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "pre-a b\n"


class TestDoubleQuotedSubstitution:
    def test_double_quote_in_output_preserved(self, server):
        r = _run(server, "echo \"v=$(printf 'say \"hi\"')\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == 'v=say "hi"\n'

    def test_multiline_output_in_quotes_is_one_arg(self, server):
        r = _run(server, "printf '[%s]' \"$(printf 'a\\nb')\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "[a\nb]"

    def test_earlier_span_quotes_do_not_skew_context(self, server):
        # The first body contains a bare double quote; the second
        # substitution must still be seen as double-quoted (one argument).
        # Single segment on purpose: _split_by_shell_operators has a
        # separate, pre-existing blind spot for ``"$(...'"'...)"; …``.
        r = _run(server, "printf '[%s][%s]' \"$(printf '\"')\" \"$(printf 'a b')\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == '["][a b]'

    def test_earlier_span_quotes_do_not_skew_assignment_context(self, server):
        # Same shape in leading-assignment position: the first body's
        # quote must not make the second look like an unquoted argument.
        r = _run(server, "A=$(printf \"'\") B=$(printf 'a b') printf '[%s][%s]' \"$A\" \"$B\"")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "['][a b]"


class TestUnquotedArgumentStillSplits:
    def test_bash_word_splitting_preserved(self, server):
        r = _run(server, "printf '%s|' $(printf 'a b')")
        assert r.returncode == 0, r.stderr
        assert r.stdout == "a|b|"


class TestMissingExecutable:
    def test_command_not_found_is_exit_127_not_exception(self, server):
        r = _run(server, "definitely_not_a_real_cmd_xyz; echo after")
        assert r.returncode == 0
        assert "definitely_not_a_real_cmd_xyz: command not found" in r.stderr
        assert r.stdout == "after\n"

    def test_command_not_found_rc_when_last(self, server):
        r = _run(server, "definitely_not_a_real_cmd_xyz")
        assert r.returncode == 127
