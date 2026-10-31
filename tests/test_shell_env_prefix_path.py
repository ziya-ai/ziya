"""Env-assignment prefixes cannot redirect or hide the command they precede.

Two holes, found together:

1. ``PATH=/tmp ls`` was allowed: the allowlist matched ``ls`` but the child
   resolved argv[0] through the prefixed PATH, so a model-written /tmp/ls
   ran as ``ls``.
2. Inside a compound construct (if/while/for body) a segment starting with
   ``NAME=value`` was skipped entirely as "a variable assignment", so
   ``if true; then FOO=1 anycmd; fi`` ran anything and
   ``LD_PRELOAD=`` escaped the loader-hijack block.

A PATH prefix is allowed when it resolves the command to the same file the
normal PATH does (``PATH=/opt/x:$PATH ls`` with no /opt/x/ls).
"""
import os
import stat

import pytest

from app.mcp_servers.shell_server import ShellServer, _env_prefix_denial


@pytest.fixture
def server():
    return ShellServer()


@pytest.fixture
def fake_ls(tmp_path):
    p = tmp_path / "ls"
    p.write_text("#!/bin/sh\necho pwned\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_path_prefix_redirecting_command_is_refused(server, fake_ls):
    ok, reason = server.is_command_allowed(f"PATH={fake_ls} ls")
    assert ok is False
    assert "PATH" in reason


def test_path_prefix_that_keeps_resolution_is_allowed(server, tmp_path):
    # Positive: the legitimate prepend form still works.
    assert server.is_command_allowed(f"PATH={tmp_path}:$PATH ls") == (True, "")
    assert server.is_command_allowed("ls") == (True, "")


def test_path_prefix_with_unknown_expansion_is_refused(server):
    ok, reason = server.is_command_allowed("PATH=$HOME/bin ls")
    assert ok is False and "PATH" in reason


def test_path_prefix_on_absolute_command_is_irrelevant(server, tmp_path):
    # PATH lookup is not used for a command containing '/'.
    assert server.is_command_allowed(f"PATH={tmp_path} /bin/ls") == (True, "")


def test_compound_body_assignment_does_not_hide_command(server):
    # Positive control: the same command without a prefix is refused.
    assert server.is_command_allowed("if true; then nosuchcmd x; fi")[0] is False
    ok, reason = server.is_command_allowed("if true; then FOO=1 nosuchcmd x; fi")
    assert ok is False and "nosuchcmd" in reason
    # And an allowed command behind a harmless prefix still passes.
    assert server.is_command_allowed("if true; then FOO=1 ls; fi") == (True, "")


def test_compound_body_loader_hijack_is_refused(server):
    ok, reason = server.is_command_allowed("for f in a; do LD_PRELOAD=/tmp/x.so ls; done")
    assert ok is False and "LD_PRELOAD" in reason


def test_compound_body_path_redirect_is_refused(server, fake_ls):
    ok, reason = server.is_command_allowed(f"for f in a; do PATH={fake_ls} ls; done")
    assert ok is False and "PATH" in reason


def test_pure_assignment_in_compound_body_still_allowed(server):
    assert server.is_command_allowed("if true; then x=1; fi") == (True, "")


def test_helper_direct(fake_ls):
    assert _env_prefix_denial("ls", {}) == ""
    assert _env_prefix_denial("ls", {"FOO": "1"}) == ""
    assert _env_prefix_denial("ls", {"PATH": str(fake_ls)}) != ""
    assert _env_prefix_denial("ls", {"PATH": f"{fake_ls}x:$PATH"}) == ""
