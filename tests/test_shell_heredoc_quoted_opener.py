"""
Regression: a ``<<`` inside a quoted argument is not a heredoc opener.

``python3 -c "... flag=' <<<' ..."`` (a multi-line quoted script) made
``_HEREDOC_OPENER_RE`` match ``<<`` + bare delimiter ``<'`` and run to the
next newline -- a newline that is inside the double quotes.  No terminator
existed, so nothing was stripped; the quote-aware line split then returned
the whole command as one line, and the heredoc branch of
``is_command_allowed`` re-validated the identical string forever
(RecursionError, surfaced to the user as "Internal error: maximum recursion
depth exceeded").

Two independent fixes, each tested here:
  1. detection ignores openers inside quotes and ``#`` comments;
  2. the heredoc branch never re-validates its own input unchanged
     (covers a real unquoted ``<<<`` followed by a quoted multi-line arg).
"""
import os

import pytest

from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import (
    _has_heredoc, _heredoc_spans, _strip_heredoc_bodies,
)


@pytest.fixture(scope="module")
def server():
    return ShellServer()


# The exact command from the user report (paths are only validated, not run).
REPORTED = r'''python3 ~/.ziya/skills/kuiper-telemetry/scripts/graf_q.py "$(cat /tmp/ggt499.sql)" --nocache --json /tmp/ggt499.json >/dev/null 2>/tmp/ggt499.err; tail -2 /tmp/ggt499.err; python3 -c "
import json
rows=json.load(open('/tmp/ggt499.json'))
cur=None
for r in rows:
    if r['entityId']!=cur:
        cur=r['entityId']; print('==',cur)
    t=r['t'][11:19]
    flag=' <<<' if ('06:03:10'<=t<'06:03:18') or ('06:04:09'<=t<'06:04:18') else ''
    print(t, r['n'], r['rx_mbps'], r['tx_mbps'], r['rssi'], r['snr'], flag)
"'''


def test_reported_command_validates_without_recursion(server):
    assert server.is_command_allowed(REPORTED) == (True, "")


def test_quoted_opener_is_not_a_heredoc():
    cmd = 'python3 -c "\nprint(\'a <<EOF\')\nprint(1)\n"'
    assert not _has_heredoc(cmd)
    assert _heredoc_spans(cmd) == []


def test_quoted_herestring_token_is_not_a_heredoc():
    assert not _has_heredoc("python3 -c \"\nx=' <<<'\nprint(x)\n\"")


def test_comment_apostrophe_does_not_hide_real_heredoc(server):
    # The apostrophe in the comment must not open a quote for the scanner,
    # nor for the unterminated-quote probe (tests/test_shell_comment_quotes.py).
    cmd = "# don't worry\ncat <<'EOF'\nrm -rf /\nEOF"
    assert _has_heredoc(cmd)
    assert 'rm -rf /' not in _strip_heredoc_bodies(cmd)
    assert server.is_command_allowed(cmd) == (True, "")


def test_real_heredoc_after_quoted_multiline_still_detected(server):
    cmd = 'python3 -c "a=1\nb=2"; cat <<EOF\nnot a command\nEOF'
    assert _has_heredoc(cmd)
    assert 'not a command' not in _strip_heredoc_bodies(cmd)
    assert server.is_command_allowed(cmd) == (True, "")


def test_command_after_terminator_still_validated(server):
    cmd = 'python3 -c "a=1\nb=2"; cat <<EOF\nx\nEOF\nsudo ls'
    ok, _reason = server.is_command_allowed(cmd)
    assert ok is False


def test_unquoted_herestring_then_quoted_multiline_no_recursion(server):
    # Detection still (conservatively) treats an unquoted <<< as an opener,
    # so this exercises the no-progress guard in the heredoc branch.
    cmd = 'cat <<< hi; python3 -c "a=1\nprint(a)"'
    assert server.is_command_allowed(cmd) == (True, "")
    bad = 'cat <<< hi; sudo python3 -c "a=1\nprint(a)"'
    assert server.is_command_allowed(bad)[0] is False


def test_quoted_opener_script_executes(server):
    cmd = 'python3 -c "\nprint(\'a <<<\')\n"'
    assert server.is_command_allowed(cmd) == (True, "")
    out = server._execute_pipeline(cmd, timeout=20, cwd=os.getcwd())
    assert out.returncode == 0
    assert out.stdout.strip() == "a <<<"
