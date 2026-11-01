"""
A write-policy denial must explain itself and say how to widen the policy.

A new user saw ``cp`` refused and concluded the command was broken.  The
denial now states the posture (shell writes are confined to safe paths;
project files change through reviewed diffs) and names the Project Settings
entry that would admit the target.  The seam test at the end applies that
suggestion through the real spawn channel (ZIYA_PROJECT_WRITE_PATHS) and
checks the same command is then allowed, so the hint cannot drift into
advice that does not work.
"""

import os

import pytest

from app.config.scope_canonical import PROJECT_WRITE_PATHS_ENV_KEY
from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "project"
    (p / ".ziya").mkdir(parents=True)
    (p / "src" / "pkg").mkdir(parents=True)
    return str(p)


def _checker(proj, project_env=None):
    pm = WritePolicyManager()
    pm.load_for_project("denial-hint", proj)
    if project_env:
        pm.merge_env_overrides({PROJECT_WRITE_PATHS_ENV_KEY: project_env})
    c = ShellWriteChecker(pm)
    c.set_project_root(proj)
    return c


def _one(cmd):
    return [(None, cmd)]


def _split(cmd):
    # Minimal operator split, enough for the cd-chain case below.
    out, op = [], None
    for part in cmd.split("&&"):
        out.append((op, part.strip()))
        op = "&&"
    return out


POSTURE = "Shell writes are limited to safe paths"
WHERE = "in Project Settings (Manage Projects), then restart the shell server"


@pytest.mark.parametrize("cmd,entry", [
    ("cp /tmp/a src/x.py", "'src/' under Additional Safe Write Paths"),
    ("cp /tmp/a src/pkg/m.py", "'src/pkg/' under Additional Safe Write Paths"),
    ("cp -t src/ /tmp/a", "'src/' under Additional Safe Write Paths"),
    ("mv src/x.py /tmp/b", "'src/' under Additional Safe Write Paths"),
    ("cp /tmp/a README.md", "'README.md' under Allowed Write Patterns"),
    ("sed -i s/a/b/ src/x.py", "'src/' under Additional Safe Write Paths"),
    ("echo hi > src/out.txt", "'src/' under Additional Safe Write Paths"),
])
def test_denial_explains_posture_and_names_entry(proj, cmd, entry):
    ok, reason = _checker(proj).check(cmd, _one)
    assert not ok
    assert POSTURE in reason and ".ziya/" in reason and "git diffs" in reason
    assert f"add {entry} {WHERE}" in reason


def test_entry_follows_the_simulated_cwd(proj):
    ok, reason = _checker(proj).check("cd src && cp /tmp/a m.py", _split)
    assert not ok
    assert "add 'src/' under Additional Safe Write Paths" in reason


def test_unknown_cwd_gets_generic_entry(proj):
    ok, reason = _checker(proj).check("cd $X && cp /tmp/a m.py", _split)
    assert not ok
    assert "add the target's directory under Additional Safe Write Paths" in reason


def test_non_path_refusal_has_no_hint(proj):
    ok, reason = _checker(proj).check("sudo ls", _one)
    assert not ok
    assert POSTURE not in reason


@pytest.mark.parametrize("cmd,suggested", [
    ("cp /tmp/a src/x.py", "src/"),
    ("cp /tmp/a src/pkg/m.py", "src/pkg/"),
    ("mv src/x.py /tmp/b", "src/"),
    ("cp /tmp/a README.md", "README.md"),
])
def test_following_the_hint_admits_the_command(proj, cmd, suggested):
    ok, reason = _checker(proj).check(cmd, _one)
    assert not ok and f"'{suggested}'" in reason
    ok, reason = _checker(proj, project_env=suggested).check(cmd, _one)
    assert ok, reason


@pytest.mark.asyncio
async def test_hint_reaches_the_tool_error(proj):
    from app.mcp_servers.shell_server import ShellServer
    response = await ShellServer().handle_request({
        "method": "tools/call", "id": 1,
        "params": {"name": "run_shell_command",
                   "arguments": {"command": "cp /tmp/a src/x.py",
                                 "_project_root": proj}},
    })
    msg = response["error"]["message"]
    assert msg.startswith("🚫 WRITE BLOCKED")
    assert "add 'src/' under Additional Safe Write Paths" in msg
