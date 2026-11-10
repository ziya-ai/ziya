"""
An apostrophe in a shell comment is not a quote.

``# don't worry\\ncat <<'EOF' ...`` was refused as "unterminated quote in
command": every quote scanner in the shell gates (the unterminated-quote
probe, the heredoc line split, the write checker's line split, the
operator splitter the executor uses) read the ``'`` in the comment as an
opening quote that ran to the end of the command.  The executor also ran
a ``# note`` line as a command named ``#``.

Comments are now removed by ``_strip_shell_comments`` before those
scanners run.  The security-relevant cases are covered too: text the
shell would RUN must never be classed as a comment (escaped blank before
``#``, ``#`` inside quotes, ``#`` inside a substitution), and a command on
the line after a comment is still checked by the allowlist and by the
write policy.
"""
import os

import pytest

from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import _strip_shell_comments


@pytest.fixture(scope="module")
def server():
    return ShellServer()


# -- the stripper itself ---------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("# don't\necho hi", "\necho hi"),
    ("echo hi # it's", "echo hi "),
    ("echo a;# x'\necho b", "echo a;\necho b"),
    ("(# x'\n)", "(\n)"),
])
def test_comments_removed_newlines_kept(text, expected):
    assert _strip_shell_comments(text) == expected


@pytest.mark.parametrize("text", [
    "echo a#b $# ${#x}",                 # # not at a word start
    "echo a\\ #;sudo ls",                # escaped blank: '#' is mid-word
    "echo \\#x",                         # escaped '#'
    "echo \"a # it's\" 'b # c'",         # inside quotes
    "echo $(echo hi # x'\n)",            # inside $(...): left alone
    "echo `echo hi # x`; echo y",        # inside backticks: left alone
    "diff <(sort a # x'\n) b",           # inside <(...): left alone
])
def test_non_comments_untouched(text):
    assert _strip_shell_comments(text) == text


# -- allowlist -------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "# don't worry\necho hi",
    "echo hi # it's fine",
    "echo hi # it's\necho there",
    "echo hi # ; sudo reboot",           # a ';' in a comment runs nothing
    "# don't\ncat <<'EOF'\nhi\nEOF",
])
def test_comment_apostrophe_allowed(server, cmd):
    assert server.is_command_allowed(cmd) == (True, "")


@pytest.mark.parametrize("cmd", [
    "# it's\nsudo ls",                   # next line still validated
    "echo hi # it's\nsudo ls",
    "echo a\\ #;sudo ls",                # bash runs 'sudo ls' here
    "# don't\ncat <<'EOF'\nhi\nEOF\nsudo ls",
])
def test_command_after_comment_still_refused(server, cmd):
    assert server.is_command_allowed(cmd)[0] is False


def test_only_comments_still_refused(server):
    assert server.is_command_allowed("# it's only a comment")[0] is False


# -- write policy ----------------------------------------------------------

def test_write_after_comment_line_is_checked(server):
    # A root outside /tmp: /tmp is a safe write path, so a project rooted
    # there (a scratch copy) would make every relative write allowed.
    root = os.path.expanduser("~/.ziya-test-no-such-project")
    server.write_checker.set_project_root(root)
    try:
        # Positive control: the cp on its own is refused...
        ok, _ = server.write_checker.check(
            "cp /tmp/a app/main.py", server._split_by_shell_operators)
        assert ok is False
        # ...and so it must be after a comment with an apostrophe, which
        # previously made the whole command one '#' line and skipped it.
        ok, reason = server.write_checker.check(
            "# it's\ncp /tmp/a app/main.py", server._split_by_shell_operators)
        assert ok is False, reason
        ok, _ = server.write_checker.check(
            "# it's\ncp /tmp/a /tmp/b", server._split_by_shell_operators)
        assert ok is True
    finally:
        server.write_checker.clear_project_root()


# -- execution -------------------------------------------------------------

def test_executor_skips_comments(server):
    out = server._execute_pipeline(
        "# don't run this\necho hi # it's\necho there",
        timeout=20, cwd=os.getcwd())
    assert out.returncode == 0
    assert out.stdout == "hi\nthere\n"
    assert "command not found" not in (out.stderr or "")


def test_executor_heredoc_after_comment(server):
    out = server._execute_pipeline(
        "# don't\ncat <<'EOF'\nbody's line\nEOF", timeout=20, cwd=os.getcwd())
    assert out.returncode == 0
    assert out.stdout == "body's line\n"
