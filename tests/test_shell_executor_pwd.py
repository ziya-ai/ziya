"""``$PWD`` and command substitutions must track the segment's working directory.

The shell tool runs every request in a per-request ``cwd`` (the project
root, then whatever an in-process ``cd`` moves it to), but three things
still reflected the *server process's* launch directory:

1. ``$PWD`` expanded in Python from ``os.environ`` -- the directory the MCP
   server happened to be started in, which is unrelated to the request.
   ``pwd`` (the binary, which calls getcwd) and ``$PWD`` therefore disagreed.
2. Children inherited that stale ``PWD`` in their environment, so anything
   reading the variable directly (``printenv PWD``, ``os.environ['PWD']``)
   saw the wrong directory.  (bash resets PWD at startup when it does not
   match getcwd, which masked this on the ``sh -c`` route; ``printenv`` has
   no such correction and is used here for that reason.)
3. ``$(...)`` / backtick substitutions ran in the *request* cwd rather than
   the segment's ``effective_cwd``, so ``cd sub && echo $(pwd)`` printed the
   parent.

All three are per-segment properties of ``effective_cwd`` and are derived
from it now.
"""
from __future__ import annotations

import os

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "sub").mkdir()
    return tmp_path


def _run(server, cmd, cwd):
    return server._execute_pipeline(cmd, timeout=10, cwd=str(cwd))


def _real(p) -> str:
    return os.path.realpath(str(p))


@pytest.fixture(autouse=True)
def _stale_server_pwd(monkeypatch, tmp_path):
    """Make the process's inherited PWD provably unrelated to the request cwd.

    Without this, a test process launched from a directory that happens to
    equal ``cwd`` would pass on the unfixed code.
    """
    stale = tmp_path / "stale-launch-dir"
    stale.mkdir()
    monkeypatch.setenv("PWD", str(stale))
    return stale


class TestDollarPwdExpansion:
    def test_pwd_expands_to_request_cwd(self, server, sandbox, _stale_server_pwd):
        r = _run(server, "echo $PWD", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox)
        assert str(_stale_server_pwd) not in r.stdout

    def test_braced_pwd_expands_to_request_cwd(self, server, sandbox):
        r = _run(server, "echo ${PWD}", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox)

    def test_pwd_follows_in_process_cd(self, server, sandbox):
        r = _run(server, "cd sub && echo $PWD", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox / "sub")

    def test_pwd_and_pwd_binary_agree(self, server, sandbox):
        r = _run(server, "cd sub && echo $PWD && pwd", sandbox)
        a, b = [_real(l) for l in r.stdout.strip().splitlines()]
        assert a == b == _real(sandbox / "sub")

    def test_pwd_reverts_when_cd_is_skipped(self, server, sandbox):
        # cd is skipped by the failed && predecessor; $PWD must not move.
        r = _run(server, "false && cd sub; echo $PWD", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox)


class TestChildEnvironment:
    def test_child_env_pwd_is_request_cwd(self, server, sandbox, _stale_server_pwd):
        r = _run(server, "printenv PWD", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox)
        assert str(_stale_server_pwd) not in r.stdout

    def test_child_env_pwd_follows_cd(self, server, sandbox):
        r = _run(server, "cd sub && printenv PWD", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox / "sub")

    def test_sh_route_child_env_pwd(self, server, sandbox, _stale_server_pwd):
        # Compound command -> sh -c route.  printenv reads the variable as
        # handed down, not sh's own corrected view.
        r = _run(server, "for x in 1; do printenv PWD; done", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox)

    def test_explicit_assignment_still_wins(self, server, sandbox):
        # A user-supplied VAR=value prefix takes precedence, as in a shell.
        r = _run(server, "PWD=/elsewhere printenv PWD", sandbox)
        assert r.stdout.strip() == "/elsewhere"


class TestSubstitutionCwd:
    def test_substitution_runs_in_effective_cwd(self, server, sandbox):
        r = _run(server, "cd sub && echo $(pwd)", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox / "sub")

    def test_backtick_substitution_runs_in_effective_cwd(self, server, sandbox):
        r = _run(server, "cd sub && echo `pwd`", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox / "sub")

    def test_substitution_sees_pwd_variable(self, server, sandbox):
        r = _run(server, "cd sub && echo $(echo $PWD)", sandbox)
        assert _real(r.stdout.strip()) == _real(sandbox / "sub")

    def test_substitution_cwd_moves_with_cd(self, server, sandbox):
        (sandbox / "outer.txt").write_text("")
        (sandbox / "sub" / "inner.txt").write_text("")
        r = _run(server, "echo $(ls) && cd sub && echo $(ls)", sandbox)
        lines = r.stdout.strip().splitlines()
        assert "outer.txt" in lines[0].split() and "inner.txt" not in lines[0]
        assert lines[1] == "inner.txt"
