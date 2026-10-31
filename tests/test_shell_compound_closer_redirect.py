"""Redirections attached to a compound closer are I/O, not a command.

``for f in *.md; do cat "$f"; done 2>/dev/null | head`` was refused with
``'2>/dev/null' is not allowed``: ``_validate_compound_body`` strips the
``done`` keyword and then treats whatever follows as the command word.
After the fix the redirection tokens are skipped; a keyword-only segment
with nothing but redirections has no command and is not checked against
the allowlist.

The redirection *target* is still enforced -- by the write policy's
``_redirection`` scan of the raw command, which is independent of the
allowlist pass.  The seam test below asserts both halves through the real
``ShellServer`` calls the tool makes before executing.
"""
from __future__ import annotations

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


@pytest.fixture
def checker(server, tmp_path):
    proj = tmp_path / "proj"
    (proj / ".ziya").mkdir(parents=True)
    (proj / "app").mkdir()
    c = server.write_checker
    c.pm.load_for_project("t", str(proj))
    c.set_project_root(str(proj))
    yield c
    c.clear_project_root()


@pytest.mark.parametrize("cmd", [
    'for f in .ziya/signatures/*.md; do echo "## $f"; sed -n 1,40p "$f"; done 2>/dev/null | head -120',
    'for f in *.md; do cat "$f"; done 2>&1 | head',
    'for f in *.md; do cat "$f"; done > /dev/null',
    'if [ -f x ]; then cat x; fi 2>/dev/null',
    'while true; do head -1; break; done < /etc/hosts',
    '{ ls; ls app; } 2>/dev/null',
])
def test_closer_redirect_is_allowed(server, cmd):
    ok, reason = server.is_command_allowed(cmd)
    assert ok, reason


def test_closer_redirect_does_not_hide_a_disallowed_body(server):
    ok, reason = server.is_command_allowed(
        'for f in *.md; do sh -c true; done 2>/dev/null')
    assert not ok
    assert "'sh' is not allowed" in reason


def test_closer_redirect_target_still_gated_by_write_policy(server, checker):
    cmd = 'for f in *.md; do cat "$f"; done > app/out.txt'
    ok, _ = server.is_command_allowed(cmd)
    assert ok, "allowlist pass is not the write gate"
    w_ok, w_reason = checker.check(cmd, server._split_by_shell_operators)
    assert not w_ok
    assert "app/out.txt" in w_reason

    cmd_ok = 'for f in *.md; do cat "$f"; done > .ziya/out.txt'
    w_ok, w_reason = checker.check(cmd_ok, server._split_by_shell_operators)
    assert w_ok, w_reason
