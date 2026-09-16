"""Command-substitution bodies are commands: validated and executed as such.

Two defects, one seam.

**Write policy never saw substitution bodies.**  ``is_command_allowed``
recursed into ``$(...)``/backtick bodies, but ``ShellWriteChecker.check`` did
not, and the allowlist deliberately admits ``cp``/``rm``/``sed`` on the
assumption that the write gate vets their targets.  So::

    echo $(cp /etc/hosts app/main.py)
    echo `rm -rf app`
    x=$(sed -i s/a/b/ app/main.py)

passed both gates and the destructive command ran.  The checker now takes a
substitution extractor (``ShellWriteChecker.subst_fn``, wired by the server)
and validates every body as a full command, starting from the enclosing
segment's working directory.

**Execution tokenized the body as one argv.**  ``_run_substitution`` ran the
body through ``_expand_and_tokenize`` and a single ``_popen_group``, so
``$(false || echo fallback)`` ran ``false`` with ``||``, ``echo``,
``fallback`` as arguments.  Bodies now go through ``_execute_pipeline``
(operators, in-process ``cd``, redirections, pipeline-local variables).
"""
from __future__ import annotations

import os

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers import shell_server as ss
from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import ShellWriteChecker


@pytest.fixture
def project_root(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".ziya").mkdir(parents=True)
    (proj / "app").mkdir()
    (proj / "app" / "main.py").write_text("x = 1\n")
    return str(proj)


@pytest.fixture
def server():
    return ShellServer()


@pytest.fixture
def checker(server, project_root):
    """The server's own checker, anchored to a scratch project."""
    c = server.write_checker
    c.pm.load_for_project("t", project_root)
    c.set_project_root(project_root)
    yield c
    c.clear_project_root()


def _check(server, checker, cmd):
    return checker.check(cmd, server._split_by_shell_operators)


# ── validation ─────────────────────────────────────────────────────────────

class TestWritePolicySeesBodies:
    @pytest.mark.parametrize("cmd", [
        "echo $(cp /etc/hosts app/main.py)",
        "echo `rm -rf app`",
        "x=$(sed -i s/a/b/ app/main.py)",
        "echo $(mv app/main.py app/gone.py)",
        # Nested body.
        "echo $(echo $(cp /etc/hosts app/main.py))",
        # Body after an operator inside the substitution.
        "echo $(true && cp /etc/hosts app/main.py)",
        # Substitution as an argument of a later segment.
        "ls && echo $(rm app/main.py)",
    ])
    def test_destructive_body_is_refused(self, server, checker, cmd):
        ok, reason = _check(server, checker, cmd)
        assert not ok, cmd
        assert "command substitution" in reason, reason

    def test_redirection_inside_body_is_refused(self, server, checker):
        # Already caught before this change by the enclosing segment's
        # character-level redirection scan; kept so the body recursion
        # cannot regress it.
        ok, _ = _check(server, checker, "echo $(echo hi > app/x)")
        assert not ok

    @pytest.mark.parametrize("cmd", [
        "echo $(cat app/main.py)",
        "echo $(cp app/main.py /tmp/copy.py)",
        "echo $(cp app/main.py .ziya/copy.py)",
        "x=$(git rev-parse HEAD)",
        "echo $(true || false)",
        "echo `ls app`",
    ])
    def test_read_only_or_in_scope_body_is_allowed(self, server, checker, cmd):
        ok, reason = _check(server, checker, cmd)
        assert ok, reason

    def test_server_wires_the_extractor(self, server):
        """The seam: a fresh server's checker must recurse without the caller
        having to pass anything -- every direct ``write_checker.check`` site
        in the tree calls it with two arguments."""
        assert server.write_checker.subst_fn is ss._extract_command_substitutions

    def test_checker_without_extractor_does_not_recurse(self, project_root):
        """Documented limitation of a bare checker (no server): bodies are
        opaque.  If this ever starts refusing, the default changed and the
        seam test above is no longer what proves wiring."""
        c = ShellWriteChecker(WritePolicyManager())
        c.set_project_root(project_root)
        try:
            ok, _ = c.check("echo $(cp /etc/hosts app/main.py)", lambda s: [("", s)])
            assert ok
        finally:
            c.clear_project_root()


class TestBodyInheritsSegmentCwd:
    def test_body_relative_target_uses_enclosing_cd(self, server, checker):
        ok, reason = _check(server, checker, "cd /tmp && echo $(cp a b)")
        assert ok, reason

    def test_cd_inside_body_is_honoured(self, server, checker):
        ok, reason = _check(server, checker, "echo $(cd /tmp && cp a b)")
        assert ok, reason

    def test_cd_inside_body_cannot_escape(self, server, checker):
        ok, reason = _check(server, checker, "echo $(cd ~ && cp x .ziya/y)")
        assert not ok
        assert "command substitution" in reason

    def test_cd_inside_body_does_not_leak_to_enclosing_command(self, server, checker):
        # The body runs in a subshell; the outer ``cp`` is still in the
        # project root and its target is app/ -> refused.
        ok, _ = _check(server, checker, "echo $(cd /tmp) && cp x app/y")
        assert not ok

    def test_unknown_cwd_in_body_refuses_relative_write(self, server, checker):
        ok, reason = _check(server, checker, "echo $(cd $D && cp a b)")
        assert not ok
        assert "not statically known" in reason


# ── execution ──────────────────────────────────────────────────────────────

def _run(server, cmd, cwd):
    return server._execute_pipeline(cmd, timeout=10, cwd=str(cwd))


class TestBodyIsAPipeline:
    def test_or_fallback(self, server, tmp_path):
        r = _run(server, "echo $(false || echo fallback)", tmp_path)
        assert r.stdout.strip() == "fallback"

    def test_semicolon_sequence(self, server, tmp_path):
        r = _run(server, "echo $(echo a; echo b)", tmp_path)
        assert r.stdout.split() == ["a", "b"]

    def test_pipe_inside_body(self, server, tmp_path):
        r = _run(server, "echo $(printf 'x\\ny\\n' | wc -l)", tmp_path)
        assert r.stdout.strip() == "2"

    def test_cd_inside_body_changes_where_it_runs(self, server, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        r = _run(server, "echo $(cd sub && pwd)", tmp_path)
        assert os.path.realpath(r.stdout.strip()) == os.path.realpath(str(sub))

    def test_cd_inside_body_does_not_leak(self, server, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        r = _run(server, "echo $(cd sub && pwd) && pwd", tmp_path)
        lines = [os.path.realpath(l) for l in r.stdout.split()]
        assert lines == [os.path.realpath(str(sub)), os.path.realpath(str(tmp_path))]

    def test_body_runs_in_segment_cwd(self, server, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "marker").write_text("")
        r = _run(server, "cd sub && echo $(ls)", tmp_path)
        assert r.stdout.strip() == "marker"

    def test_dollar_pwd_inside_body_follows_body_cd(self, server, tmp_path):
        # The enclosing segment's variable view carries PWD; the body must
        # not inherit it over its own post-``cd`` value.
        sub = tmp_path / "sub"
        sub.mkdir()
        r = _run(server, "echo $(cd sub && echo $PWD)", tmp_path)
        assert os.path.realpath(r.stdout.strip()) == os.path.realpath(str(sub))

    def test_nested_bodies(self, server, tmp_path):
        r = _run(server, "echo $(echo $(echo inner))", tmp_path)
        assert r.stdout.strip() == "inner"

    def test_body_sees_pipeline_local_variable(self, server, tmp_path):
        r = _run(server, "X=5; echo $(echo $X)", tmp_path)
        assert r.stdout.strip() == "5"

    def test_body_assignment_does_not_leak(self, server, tmp_path):
        r = _run(server, "X=1; echo $(X=2; echo $X) $X", tmp_path)
        assert r.stdout.split() == ["2", "1"]

    def test_backtick_body_is_a_pipeline(self, server, tmp_path):
        r = _run(server, "echo `false || echo bt`", tmp_path)
        assert r.stdout.strip() == "bt"

    def test_failed_body_yields_empty_string(self, server, tmp_path):
        r = _run(server, "echo [$(false)]", tmp_path)
        assert r.stdout.strip() == "[]"
