"""Shell allowlist: the ``read`` builtin, and quoted / absolute command words.

Two refusals hit in real use:

* ``printf 'a\\nb' | while read l; do ...; done`` -> ``'read' is not allowed``.
  ``read`` only assigns variables from stdin, and both ``sh -c`` routes run
  with stdin=DEVNULL, so it can only consume data the command itself pipes
  or redirects in.

* ``"/bin/ls" -la`` and ``"ls" -la`` were refused although ``ls`` is
  allowlisted: patterns are anchored on the literal first word, quotes
  included.  A path with spaces was also reported truncated
  (``'"/Applications/Google' is not allowed``).

The command word is now unquoted, and an absolute path is accepted only
when it is the very file PATH lookup would pick for its basename -- a copy
of ``ls`` written elsewhere (/tmp/ls) is still refused.  Normalising happens
before the curl/aws/IaC guards so an absolute path cannot route around them.
"""
import os
import shutil
import stat

import pytest

from app.mcp_servers.shell_server import ShellServer, _normalize_command_word


@pytest.fixture(scope="module")
def server():
    return ShellServer()


LS = shutil.which("ls")
CURL = shutil.which("curl")


# ── read ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "printf 'a\\nb\\n' | while read line; do echo \"<$line>\"; done",
    "while read -r a b; do echo $a; done < /tmp/does-not-matter",
    'read x <<< "a b"',
])
def test_read_is_allowed(server, cmd):
    assert server.is_command_allowed(cmd) == (True, "")


def test_while_read_loop_runs_and_reads_only_its_own_pipe(server):
    cmd = "printf 'a\\nb\\n' | while read line; do echo \"<$line>\"; done"
    assert server.is_command_allowed(cmd)[0]
    out = server._execute_pipeline(cmd, timeout=20, cwd=os.getcwd())
    assert out.returncode == 0
    assert out.stdout.split() == ["<a>", "<b>"]


def test_read_does_not_open_unlisted_commands(server):
    ok, reason = server.is_command_allowed("printf x | while read l; do nosuchcmd $l; done")
    assert not ok and "nosuchcmd" in reason


# ── quoted / absolute command word ──────────────────────────────────────

def test_quoted_allowlisted_word_is_accepted(server):
    assert server.is_command_allowed('"ls" -la') == (True, "")
    assert server.is_command_allowed("'ls' -la") == (True, "")


@pytest.mark.skipif(not LS, reason="ls not on PATH")
def test_absolute_path_that_path_lookup_picks_is_accepted(server):
    assert server.is_command_allowed(f"{LS} -la") == (True, "")
    assert server.is_command_allowed(f'"{LS}" -la | wc -l') == (True, "")
    assert server.is_command_allowed(f'for f in a; do "{LS}" -d .; done') == (True, "")


def test_lookalike_binary_elsewhere_is_refused(server, tmp_path):
    fake = tmp_path / "ls"
    fake.write_text("#!/bin/sh\necho pwned\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    ok, reason = server.is_command_allowed(f'"{fake}" -la')
    assert not ok
    assert str(fake) in reason
    ok, _ = server.is_command_allowed(f'for f in a; do "{fake}"; done')
    assert not ok


def test_expansion_in_command_word_is_left_alone():
    seg = '"$HOME/ls" -la'
    assert _normalize_command_word(seg)[0] == seg


def test_refusal_names_the_whole_unquoted_path(server, tmp_path):
    d = tmp_path / "My App.app" / "Contents"
    d.mkdir(parents=True)
    exe = d / "My Tool"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    ok, reason = server.is_command_allowed(f'"{exe}" --version')
    assert not ok
    assert reason == f"'{exe}' is not allowed"


@pytest.mark.skipif(not CURL, reason="curl not on PATH")
def test_absolute_path_cannot_route_around_the_curl_guard(server):
    url = "http://169.254.169.254/latest/meta-data/"
    plain = server.is_command_allowed(f"curl {url}")
    via_path = server.is_command_allowed(f'"{CURL}" {url}')
    assert plain[0] is False and via_path == plain
    # Positive: an ordinary fetch through the absolute path passes.
    assert server.is_command_allowed(f'"{CURL}" -s https://example.com')[0]
