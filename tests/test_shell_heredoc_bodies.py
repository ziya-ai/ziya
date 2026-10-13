"""Heredoc bodies: data is skipped, what the shell actually runs is validated.

Three defects at one seam, found while making a compound command with a
``python3 - <<'EOF'`` tail pass in one turn instead of four.

**Compound route validated raw bodies.**  ``_validate_compound_body`` split
the RAW command, so ``for …; done; python3 - <<'EOF' … EOF`` had every
Python line checked as a shell command and failed on ``import``.  The plain
heredoc route had stripped bodies for a long time; the compound route had
not.

**Unquoted bodies are expanded.**  With a bare delimiter (``<<EOF``) the
shell performs ``$(...)``/backtick substitution inside the body.  Both gates
stripped bodies wholesale, so ``cat <<EOF\\n$(cmd)\\nEOF`` ran ``cmd``
without either the allowlist or the write gate seeing it.

**``<<-`` terminators may be tab-indented.**  The stripper demanded the
delimiter at column 0, so ``\\tEOF`` was not recognised and stripping ran on
to a later bare ``EOF``: the commands between, which the shell executes,
were classified as body and skipped.

The seam tests go through the real ``ShellServer`` (``is_command_allowed``
and ``write_checker.check``), the same two calls the tool makes before
executing.  Disallowed commands are benign look-alikes (``sh -c true`` is
not allowlisted) so the assertion is about the gate, not the payload.
"""
from __future__ import annotations

import pytest

from app.mcp_servers import write_policy as wp
from app.mcp_servers.shell_server import ShellServer

PY_BODY = "import gzip, json\nfor k in range(2):\n    print(k)\n"


@pytest.fixture
def server():
    return ShellServer()


@pytest.fixture
def checker(server, tmp_path):
    proj = tmp_path / "proj"
    (proj / ".ziya").mkdir(parents=True)
    (proj / "app").mkdir()
    (proj / "app" / "main.py").write_text("x = 1\n")
    c = server.write_checker
    c.pm.load_for_project("t", str(proj))
    c.set_project_root(str(proj))
    yield c
    c.clear_project_root()


def _allowed(server, cmd):
    return server.is_command_allowed(cmd)


def _write_ok(server, checker, cmd):
    return checker.check(cmd, server._split_by_shell_operators)


# ── the turn-saving case: compound command + quoted heredoc ─────────────────

class TestCompoundRouteSkipsBodies:
    def test_loop_then_python_heredoc_is_allowed(self, server):
        cmd = ("for k in a b; do echo $k; done; "
               "python3 - <<'EOF'\n" + PY_BODY + "EOF")
        assert _allowed(server, cmd) == (True, "")

    def test_body_with_prose_apostrophe_is_allowed(self, server):
        cmd = "for k in a; do echo; done; cat <<'EOF'\nit's data, O'Brien\nEOF"
        assert _allowed(server, cmd) == (True, "")

    def test_unquoted_body_without_substitution_is_allowed(self, server):
        cmd = "for k in a; do echo; done; cat <<EOF\nplain text here\nEOF"
        assert _allowed(server, cmd) == (True, "")

    def test_command_after_terminator_is_still_validated(self, server):
        cmd = "for k in a; do echo; done; cat <<'EOF'\nx\nEOF\nsh -c true"
        ok, reason = _allowed(server, cmd)
        assert not ok and "'sh'" in reason

    def test_allowed_command_after_terminator_passes(self, server):
        cmd = "for k in a; do echo; done; cat <<'EOF'\nx\nEOF\necho after"
        assert _allowed(server, cmd) == (True, "")


# ── unquoted-body substitutions reach the allowlist ────────────────────────

class TestUnquotedBodySubstitutionsAllowlist:
    @pytest.mark.parametrize("cmd", [
        "cat <<EOF\n$(sh -c true)\nEOF",
        "cat <<EOF\n`sh -c true`\nEOF",
        # Quotes are literal in a heredoc body: they do NOT suppress $().
        "cat <<EOF\n'$(sh -c true)'\nEOF",
        # Compound route.
        "for k in a; do echo; done; cat <<EOF\n$(sh -c true)\nEOF",
        # Nested inside an allowed substitution.
        "cat <<EOF\n$(echo $(sh -c true))\nEOF",
        # Substitution body spans a quoted ')' before the real close.
        "cat <<EOF\n$(sh -c ')' ; true)\nEOF",
    ])
    def test_disallowed_substitution_is_refused(self, server, cmd):
        ok, reason = _allowed(server, cmd)
        assert not ok
        assert "'sh'" in reason and "heredoc" in reason

    @pytest.mark.parametrize("cmd", [
        "cat <<EOF\nhost: $(uname -s)\nEOF",
        "cat <<EOF\n`uname -s`\nEOF",
        "for k in a; do echo; done; cat <<EOF\n$(uname -s)\nEOF",
        # Escaped: the shell does not expand these.
        "cat <<EOF\n\\$(sh -c true)\nEOF",
        "cat <<EOF\n\\`sh -c true\\`\nEOF",
    ])
    def test_allowed_or_escaped_substitution_passes(self, server, cmd):
        assert _allowed(server, cmd) == (True, "")

    def test_quoted_delimiter_body_is_never_expanded(self, server):
        # <<'EOF': the shell passes $() through literally, so it is data.
        assert _allowed(server, "cat <<'EOF'\n$(sh -c true)\nEOF") == (True, "")


# ── unquoted-body substitutions reach the write gate ───────────────────────

class TestUnquotedBodySubstitutionsWriteGate:
    @pytest.mark.parametrize("cmd", [
        # ``touch`` is not a gated destructive command (top-level passes too);
        # mkdir/cp/redirection are.
        "cat <<EOF\n$(mkdir app/newdir)\nEOF",
        "cat <<EOF\n`cp /etc/hosts app/main.py`\nEOF",
        "cat <<EOF\n$(echo hi > app/main.py)\nEOF",
    ])
    def test_project_write_in_body_is_refused(self, server, checker, cmd):
        ok, reason = _write_ok(server, checker, cmd)
        assert not ok and "heredoc substitution" in reason

    @pytest.mark.parametrize("cmd", [
        "cat <<EOF\n$(touch /tmp/scratch.txt)\nEOF",
        "cat <<'EOF'\n$(touch app/new.py)\nEOF",   # quoted: data
        "cat <<EOF\nif x > 1: pass\nEOF",           # '>' in body is data
    ])
    def test_safe_or_literal_body_passes(self, server, checker, cmd):
        assert _write_ok(server, checker, cmd) == (True, "")


# ── <<- with a tab-indented terminator ─────────────────────────────────────

class TestDashHeredocTerminator:
    CMD = "cat <<-EOF\n\tbody\n\tEOF\nsh -c true\nEOF"

    def test_stripper_ends_body_at_tabbed_terminator(self):
        # The terminator line's own newline survives stripping (as it always has).
        assert wp._strip_heredoc_bodies(self.CMD) == "cat <<-EOF\n\nsh -c true\nEOF"

    def test_command_after_tabbed_terminator_is_validated(self, server):
        ok, reason = _allowed(server, self.CMD)
        assert not ok and "'sh'" in reason

    def test_plain_heredoc_does_not_accept_tabbed_terminator(self):
        # Without the dash, "\tEOF" is body: the shell requires column 0.
        cmd = "cat <<EOF\n\tEOF\nmiddle\nEOF"
        assert wp._strip_heredoc_bodies(cmd) == "cat <<EOF\n"


# ── parser unit cases ──────────────────────────────────────────────────────

class TestHeredocParsers:
    def test_multiple_heredocs_all_stripped(self):
        cmd = "cat <<A\none\nA\ncat <<B\ntwo\nB\necho done"
        assert wp._strip_heredoc_bodies(cmd) == "cat <<A\n\ncat <<B\n\necho done"

    def test_opener_inside_body_is_not_a_second_heredoc(self):
        cmd = "cat <<EOF\ncat <<X\nEOF\necho after"
        assert wp._strip_heredoc_bodies(cmd) == "cat <<EOF\n\necho after"

    def test_unterminated_heredoc_left_intact(self):
        cmd = "cat <<EOF\nnever closed"
        assert wp._strip_heredoc_bodies(cmd) == cmd

    def test_body_commands_only_from_unquoted(self):
        cmd = ("cat <<'A'\n$(one)\nA\ncat <<B\n$(two) `three`\nB\n"
               "cat <<\"C\"\n$(four)\nC")
        assert wp._heredoc_body_commands(cmd) == ["two", "three"]

    def test_unterminated_substitution_returned_to_body_end(self):
        assert wp._heredoc_body_commands("cat <<E\n$(never closed\nE") == ["never closed\n"]

    def test_backslash_escapes_suppress(self):
        assert wp._heredoc_body_commands("cat <<E\n\\$(a) \\`b\\` \\\\$(c)\nE") == ["c"]
