"""Glob expansion must follow the in-process ``cd``; assignment values must
be expanded when assigned.

Two divergences from shell semantics surfaced in one session:

1. ``cd /abs/pkg && cp src/*.ts /tmp/out`` -- ``_expand_and_tokenize``
   globbed ``src/*.ts`` against the *server process's* cwd, not the
   ``effective_cwd`` the executor had just switched to.  With no match the
   literal pattern reached ``cp`` and it failed with "No such file".

2. ``P=$HOME/pkg; cp $P/a.txt /tmp/out`` -- ``_peel_env_prefix`` recorded
   the raw RHS (``$HOME/pkg``).  Later ``$P`` expansion is a single pass, so
   ``cp`` received a path that still contained the literal ``$HOME``.

Both are asserted end-to-end on the filesystem effect of ``cp``.
"""
from __future__ import annotations

import os

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


def _run(server, cmd, cwd):
    return server._execute_pipeline(cmd, timeout=10, cwd=str(cwd))


@pytest.fixture
def pkg(tmp_path):
    """A package dir with two .ts sources and one .js decoy, plus an out dir."""
    src = tmp_path / "pkg" / "src"
    src.mkdir(parents=True)
    (src / "a.ts").write_text("a")
    (src / "b.ts").write_text("b")
    (src / "c.js").write_text("c")
    (tmp_path / "pkg" / "package.json").write_text("{}")
    (tmp_path / "out").mkdir()
    return tmp_path


class TestGlobFollowsCd:
    def test_glob_after_cd_in_chain(self, pkg, server, monkeypatch):
        # Start the *executor* somewhere unrelated so a glob against the
        # wrong cwd cannot accidentally match.
        elsewhere = pkg / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        out = pkg / "out"
        _run(server, f"cd {pkg / 'pkg'} && cp src/*.ts {out}/", elsewhere)
        assert (out / "a.ts").exists() and (out / "b.ts").exists(), \
            "glob was not expanded relative to the cd'd directory"
        assert not (out / "c.js").exists()

    def test_glob_relative_to_initial_cwd_not_process_cwd(self, pkg, server, monkeypatch):
        # Even with no cd, the glob must use the pipeline's cwd argument,
        # not os.getcwd() of the server process.
        elsewhere = pkg / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        out = pkg / "out"
        _run(server, f"cp src/*.ts {out}/", pkg / "pkg")
        assert (out / "a.ts").exists() and (out / "b.ts").exists()

    def test_no_match_passes_literal(self, pkg, server):
        # Bash behaviour: an unmatched glob is passed through verbatim.
        args = server._expand_and_tokenize("ls src/*.nope", cwd=str(pkg / "pkg"))
        assert args == ["ls", "src/*.nope"]

    def test_absolute_pattern_unaffected_by_cwd(self, pkg, server):
        pattern = str(pkg / "pkg" / "src" / "*.ts")
        args = server._expand_and_tokenize(f"ls {pattern}", cwd=str(pkg / "out"))
        assert args == ["ls", str(pkg / "pkg" / "src" / "a.ts"),
                        str(pkg / "pkg" / "src" / "b.ts")]


class TestAssignmentValueExpansion:
    def test_var_in_assignment_rhs_is_expanded(self, pkg, server, monkeypatch):
        monkeypatch.setenv("ROOT", str(pkg))
        out = pkg / "out"
        result = _run(server, f"P=$ROOT/pkg; cp $P/package.json {out}/", pkg)
        assert (out / "package.json").exists(), result.stderr

    def test_var_and_glob_in_later_segment(self, pkg, server, monkeypatch):
        monkeypatch.setenv("ROOT", str(pkg))
        out = pkg / "out"
        result = _run(server, f"P=$ROOT/pkg; cp $P/src/*.ts {out}/", pkg)
        assert (out / "a.ts").exists() and (out / "b.ts").exists(), result.stderr

    def test_tilde_in_assignment_rhs_is_expanded(self, pkg, server, monkeypatch):
        monkeypatch.setenv("HOME", str(pkg))
        out = pkg / "out"
        result = _run(server, f"P=~/pkg; cp $P/package.json {out}/", pkg)
        assert (out / "package.json").exists(), result.stderr

    def test_assignment_sees_earlier_assignment(self, pkg, server, monkeypatch):
        monkeypatch.setenv("ROOT", str(pkg))
        out = pkg / "out"
        result = _run(server, f"A=$ROOT; B=$A/pkg; cp $B/package.json {out}/", pkg)
        assert (out / "package.json").exists(), result.stderr

    def test_unknown_var_left_literal(self, server):
        # Consistent with os.path.expandvars: unknown names are untouched.
        _, env, _ = server._peel_env_prefix("X=$__ZIYA_NOPE__/q echo")
        assert env == {"X": "$__ZIYA_NOPE__/q"}
