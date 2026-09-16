"""The shell write policy judges relative targets against the segment's cwd.

``shell_server._execute_pipeline`` applies ``cd`` in-process, so a relative
write target in a later segment resolves against the new directory.  The
validator used to ignore ``cd`` and resolve every relative target against the
project root, which failed in both directions:

* ``cd /tmp && cp a b`` was refused although it writes ``/tmp/b``;
* ``cd ~ && cp x .ziya/settings.json`` was allowed although it writes the
  global ``~/.ziya/settings.json``, not the project's ``.ziya/``.

The fix simulates cwd per segment (``_simulate_cwds``) and resolves relative
targets there.  Tests here cover the simulation, the ``check`` verdicts, the
denial wording, and — the seam that matters — parity between where the
validator believes a file lands and where the executor actually puts it.
"""
from __future__ import annotations

import os

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers import write_policy as wp
from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import ShellWriteChecker


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def project_root(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / ".ziya").mkdir()
    (proj / "src").mkdir()
    (proj / "src" / "a.txt").write_text("a")
    return str(proj)


@pytest.fixture
def server():
    return ShellServer()


@pytest.fixture
def split(server):
    return server._split_by_shell_operators


@pytest.fixture
def checker(project_root):
    pm = WritePolicyManager()
    pm.load_for_project("test-project", project_root)
    c = ShellWriteChecker(pm)
    c.set_project_root(project_root)
    try:
        yield c
    finally:
        c.clear_project_root()


def _segments(split, cmd):
    return [(op or "", seg) for op, seg in split(cmd)]


def _cands(states, i):
    return states[i].cands


# --------------------------------------------------------------------------- #
# _simulate_cwds
# --------------------------------------------------------------------------- #

class TestSimulateCwds:
    def test_and_chain_narrows_to_new_directory(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd /tmp && cp a b"), project_root)
        assert _cands(st, 0) == frozenset({project_root})
        assert _cands(st, 1) == frozenset({os.path.normpath("/tmp")})

    def test_relative_cd_joins_project_root(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd src && cp a b"), project_root)
        assert _cands(st, 1) == frozenset({os.path.join(project_root, "src")})

    def test_dotdot_is_normalised(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd src/../.ziya && cp a b"), project_root)
        assert _cands(st, 1) == frozenset({os.path.join(project_root, ".ziya")})

    def test_semicolon_unions_pre_and_post(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd /tmp; cp a b"), project_root)
        assert _cands(st, 1) == frozenset({project_root, "/tmp"})

    def test_or_before_cd_unions(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "false || cd /tmp && cp a b"), project_root)
        assert _cands(st, 2) == frozenset({project_root, "/tmp"})

    def test_or_after_cd_unions(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd /tmp && cp a b || cp c d"), project_root)
        assert _cands(st, 1) == frozenset({"/tmp"})
        assert _cands(st, 2) == frozenset({project_root, "/tmp"})

    def test_cd_in_pipeline_has_no_effect(self, split, project_root):
        # A pipelined cd runs in a subshell; both execution routes agree
        # (see test_shell_executor_cd_semantics.py).
        st = wp._simulate_cwds(_segments(split, "cd /tmp | cp a b"), project_root)
        assert _cands(st, 1) == frozenset({project_root})
        st = wp._simulate_cwds(_segments(split, "cd /tmp | cat && cp a b"), project_root)
        assert _cands(st, 2) == frozenset({project_root})
        st = wp._simulate_cwds(_segments(split, "echo x | cd /tmp; cp a b"), project_root)
        assert _cands(st, 2) == frozenset({project_root})

    def test_pipeline_after_cd_inherits(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd /tmp && cat x | tee out"), project_root)
        assert _cands(st, 2) == frozenset({"/tmp"})

    @pytest.mark.parametrize("cd", ["cd $HOME", "cd \"$X\"", "cd -", "cd `pwd`", "cd a*"])
    def test_dynamic_target_is_unknown(self, split, project_root, cd):
        st = wp._simulate_cwds(_segments(split, f"{cd} && cp a b"), project_root)
        assert _cands(st, 1) is None
        assert st[1].note == cd

    def test_bare_cd_and_tilde_go_home(self, split, project_root):
        home = os.path.expanduser("~")
        for cmd in ("cd && cp a b", "cd ~ && cp a b", "cd ~/ && cp a b"):
            st = wp._simulate_cwds(_segments(split, cmd), project_root)
            assert _cands(st, 1) == frozenset({home}), cmd

    def test_cd_flags_are_skipped(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd -P -- /tmp && cp a b"), project_root)
        assert _cands(st, 1) == frozenset({"/tmp"})

    def test_cd_not_in_command_position_is_unknown(self, split, project_root):
        for cmd in ("for d in x; do cd $d; cp a b; done",
                    "{ cd /tmp; } && cp a b",
                    "(cd /tmp) && cp a b"):
            st = wp._simulate_cwds(_segments(split, cmd), project_root)
            cp_index = next(i for i, (_o, s) in enumerate(_segments(split, cmd)) if s.startswith("cp"))
            assert _cands(st, cp_index) is None, cmd

    def test_unknown_is_sticky(self, split, project_root):
        st = wp._simulate_cwds(_segments(split, "cd $X; cd /tmp && cp a b"), project_root)
        assert _cands(st, 2) is None

    def test_none_operator_tolerated(self, project_root):
        # tests elsewhere use a split_fn that yields (None, cmd)
        st = wp._simulate_cwds([(None, "cp a b")], project_root)
        assert _cands(st, 0) == frozenset({project_root})


# --------------------------------------------------------------------------- #
# check() verdicts
# --------------------------------------------------------------------------- #

class TestCheckVerdicts:
    def test_user_reported_command_is_allowed(self, checker, split):
        # The exact shape that was refused: copy into /tmp, cd there, cp
        # relative-to-relative, then a heredoc'd python.
        cmd = ("cp src/a.txt /tmp/cc_orig.tsx && cd /tmp && "
               "cp cc_orig.tsx cc_patched.tsx && python3 - <<'EOF'\n"
               "print('x')\nEOF")
        ok, reason = checker.check(cmd, split)
        assert ok, reason

    def test_relative_cp_after_cd_tmp_allowed(self, checker, split):
        assert checker.check("cd /tmp && cp a b", split) == (True, "")

    def test_redirection_after_cd_tmp_allowed(self, checker, split):
        assert checker.check("cd /tmp && echo hi > out.txt", split) == (True, "")

    def test_inplace_edit_after_cd_tmp_allowed(self, checker, split):
        assert checker.check("cd /tmp && sed -i s/a/b/ x.txt", split) == (True, "")

    def test_cd_into_project_ziya_then_relative_write(self, checker, split):
        assert checker.check("cd .ziya && cp /tmp/x state.json", split) == (True, "")

    def test_home_escape_via_cd_is_denied(self, checker, split):
        # Passed before the fix: ``.ziya/settings.json`` matched the project's
        # safe path while the executor wrote ~/.ziya/settings.json.
        ok, reason = checker.check("cd ~ && cp /tmp/payload .ziya/settings.json", split)
        assert not ok
        assert os.path.join(os.path.expanduser("~"), ".ziya", "settings.json") in reason

    def test_home_escape_via_dollar_home_is_denied(self, checker, split):
        ok, reason = checker.check("cd $HOME && cp /tmp/payload .ziya/x", split)
        assert not ok
        assert "not statically known" in reason
        assert "cd $HOME" in reason

    def test_dotdot_escape_via_cd_is_denied(self, checker, split, project_root):
        ok, reason = checker.check("cd .. && cp /tmp/payload project/.ziya/../../evil", split)
        assert not ok

    def test_semicolon_union_denies(self, checker, split):
        ok, reason = checker.check("cd /tmp; cp a b", split)
        assert not ok
        assert "may resolve to any of" in reason

    def test_newline_is_sequential(self, checker, split):
        ok, _ = checker.check("cd /tmp\ncp a b", split)
        assert not ok

    def test_or_chain_union_denies(self, checker, split):
        ok, _ = checker.check("false || cd /tmp && cp a b", split)
        assert not ok

    def test_cd_in_pipeline_does_not_unlock(self, checker, split):
        ok, _ = checker.check("cd /tmp | cp a b", split)
        assert not ok

    def test_loop_body_cd_denies_relative_write(self, checker, split):
        ok, reason = checker.check("for d in x; do cd $d; cp a b; done", split)
        assert not ok
        assert "not statically known" in reason

    def test_absolute_target_unaffected_by_unknown_cwd(self, checker, split):
        assert checker.check("cd $X && cp a /tmp/b", split) == (True, "")

    def test_denial_names_resolved_path(self, checker, split, project_root):
        ok, reason = checker.check("cp a b", split)
        assert not ok
        assert f"(resolves to {os.path.join(project_root, 'b')})" in reason

    def test_mkdir_cd_cp_chain(self, checker, split):
        # ``cd`` into a directory that does not exist yet must still be
        # modelled — validation runs before mkdir does.
        assert checker.check("mkdir -p .ziya/new && cd .ziya/new && cp /tmp/x y", split) == (True, "")

    def test_relative_cd_out_of_safe_dir_denies(self, checker, split):
        ok, _ = checker.check("cd .ziya && cd .. && cp /tmp/x y", split)
        assert not ok

    def test_interpreter_literal_write_follows_cwd(self, checker, split):
        ok, reason = checker.check(
            "cd /tmp && python3 -c \"open('out.txt','w').write('x')\"", split)
        assert ok, reason
        ok, _ = checker.check(
            "cd ~ && python3 -c \"open('.ziya/x','w').write('x')\"", split)
        assert not ok

    def test_state_is_cleared_after_check(self, checker, split):
        checker.check("cd /tmp && cp a b", split)
        assert wp._CWD_STATE.get() is None

    def test_direct_call_outside_check_resolves_against_root(self, checker):
        # Callers that bypass check() keep the historical behaviour.
        assert checker._is_write_allowed(".ziya/state.json")
        assert not checker._is_write_allowed("src/a.txt")


# --------------------------------------------------------------------------- #
# Seam: validator resolution == executor placement
# --------------------------------------------------------------------------- #

class TestExecutorParity:
    """Where the validator says a relative target lands is where
    ``_execute_pipeline`` actually writes it."""

    @pytest.mark.parametrize("template", [
        "cd {sandbox} && cp {src} out1",
        "cd {sandbox}/sub && cp {src} out2",
        "cd {sandbox} && cd sub && cp {src} out3",
        "cd {sandbox} && echo hi > out4",
        "mkdir -p {sandbox}/made && cd {sandbox}/made && cp {src} out5",
    ])
    def test_single_candidate_matches_executor(self, tmp_path, server, split, template):
        sandbox = tmp_path / "sandbox"
        (sandbox / "sub").mkdir(parents=True)
        src = tmp_path / "src.txt"
        src.write_text("payload")
        cmd = template.format(sandbox=sandbox, src=src)

        segments = _segments(split, cmd)
        states = wp._simulate_cwds(segments, str(tmp_path))
        last_seg = segments[-1][1]
        target = last_seg.split()[-1]
        (cand,) = states[-1].cands
        predicted = os.path.normpath(os.path.join(cand, target))

        before = {p for p in tmp_path.rglob("*") if p.is_file()}
        server._execute_pipeline(cmd, timeout=10, cwd=str(tmp_path))
        created = {str(p) for p in tmp_path.rglob("*") if p.is_file()} - {str(p) for p in before}
        assert created == {predicted}, (cmd, created, predicted)

    def test_cd_in_pipeline_parity(self, tmp_path, server, split):
        src = tmp_path / "src.txt"
        src.write_text("payload")
        sub = tmp_path / "sub"
        sub.mkdir()
        cmd = f"cd {sub} | cp {src} piped"
        states = wp._simulate_cwds(_segments(split, cmd), str(tmp_path))
        predicted = {os.path.join(c, "piped") for c in states[1].cands}
        assert predicted == {str(tmp_path / "piped")}
        server._execute_pipeline(cmd, timeout=10, cwd=str(tmp_path))
        created = {str(p) for p in tmp_path.rglob("piped")}
        assert created == predicted, (created, predicted)
