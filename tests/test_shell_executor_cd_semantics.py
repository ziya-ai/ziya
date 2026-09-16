"""``cd`` in ``_execute_pipeline`` must follow shell semantics.

Two divergences were found while building the cwd-aware write policy:

1. The in-process ``cd`` handler ran *before* the ``&&``/``||`` skip check,
   so ``false && cd DIR && cp a b`` changed directory and then ran ``cp`` —
   a shell skips both.  The skip now happens at the top of the loop, before
   any per-segment work (including command substitution, which a shell
   also does not evaluate for a skipped segment).

2. A ``cd`` that is a member of a pipeline changed the directory for later
   segments.  A shell runs each pipeline element in a subshell, so a
   pipelined ``cd`` has no effect on the parent.  Both execution routes
   (the in-process orchestrator and ``sh -c``) now agree.

The write-policy simulation (``_simulate_cwds``) mirrors these rules; the
parity tests here assert the two stay in lockstep by checking where the
executor actually put the file.
"""
from __future__ import annotations

import pytest

from app.mcp_servers import write_policy as wp
from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def server():
    return ShellServer()


def _run(server, cmd, cwd):
    return server._execute_pipeline(cmd, timeout=10, cwd=str(cwd))


def _segments(server, cmd):
    return [(op or "", seg) for op, seg in server._split_by_shell_operators(cmd)]


class TestConditionalSkip:
    def test_false_and_cd_skips_cd_and_following(self, tmp_path, server):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        _run(server, f"false && cd {sub} && cp {src} out", tmp_path)
        assert not (sub / "out").exists(), "cd ran despite a failed && predecessor"
        assert not (tmp_path / "out").exists(), "cp ran despite a failed && chain"

    def test_true_or_cd_skips_cd(self, tmp_path, server):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        _run(server, f"true || cd {sub}; cp {src} out", tmp_path)
        assert (tmp_path / "out").exists(), "cp should run in the original cwd"
        assert not (sub / "out").exists(), "cd ran despite a successful || predecessor"

    def test_skipped_segment_does_not_run_substitution(self, tmp_path, server):
        # A shell never evaluates the substitution of a skipped segment.
        marker = tmp_path / "marker"
        _run(server, f"false && echo $(touch {marker})", tmp_path)
        assert not marker.exists()

    def test_skipped_segment_preserves_exit_status(self, tmp_path, server):
        r = _run(server, "false && echo skipped; echo rc=$?", tmp_path)
        assert "rc=1" in r.stdout
        assert "skipped" not in r.stdout

    def test_positive_and_chain_still_changes_directory(self, tmp_path, server):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        _run(server, f"true && cd {sub} && cp {src} out", tmp_path)
        assert (sub / "out").exists()


class TestPipelineCd:
    def test_cd_as_pipeline_head_has_no_effect(self, tmp_path, server):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        _run(server, f"cd {sub} | cp {src} piped", tmp_path)
        assert (tmp_path / "piped").exists(), "pipelined cd leaked into the parent cwd"
        assert not (sub / "piped").exists()

    def test_cd_as_pipeline_tail_has_no_effect(self, tmp_path, server):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        _run(server, f"echo x | cd {sub}; cp {src} after", tmp_path)
        assert (tmp_path / "after").exists()
        assert not (sub / "after").exists()

    def test_pipelined_cd_still_reports_status(self, tmp_path, server):
        r = _run(server, f"cd {tmp_path / 'nope'} | cat; echo rc=$?", tmp_path)
        # Pipeline status is that of its last element (cat → 0).
        assert "rc=0" in r.stdout
        assert "No such file" in r.stderr

    def test_sequential_cd_after_pipeline_still_applies(self, tmp_path, server):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        _run(server, f"echo x | cat; cd {sub} && cp {src} seq", tmp_path)
        assert (sub / "seq").exists()


class TestValidatorParity:
    """The simulation's single-candidate answer must match the executor."""

    @pytest.mark.parametrize("template,expect_rel", [
        ("cd {sub} | cp {src} out", "out"),
        ("echo x | cd {sub}; cp {src} out", "out"),
        ("cd {sub} && cd .. && cp {src} out", "out"),
        ("cd {sub} && cp {src} out", "sub/out"),
    ])
    def test_single_candidate_matches_executor(self, tmp_path, server, template, expect_rel):
        src = tmp_path / "src.txt"
        src.write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        cmd = template.format(sub=sub, src=src)
        states = wp._simulate_cwds(_segments(server, cmd), str(tmp_path))
        final = states[-1].cands
        assert final is not None and len(final) == 1, final
        _run(server, cmd, tmp_path)
        expected = tmp_path / expect_rel
        assert expected.exists()
        assert (next(iter(final)) + "/out") == str(expected)
