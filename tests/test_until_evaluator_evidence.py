"""The until-condition evaluator must see evidence, not just the agent's prose.

GFX Stage 2 run 3068d3d0: the until block's condition was "``reconcile``
then ``show`` reports no defect with status still-broken or regression".
The evaluator received only the iteration's SUMMARY and five decisions;
``stages`` (four children, all recorded PASSED with zero tool calls) and
``outputs`` were invisible.  It judged a claim.  These tests pin that the
evaluator's prompt carries the stage list with the no-tool-calls flag,
the data parts' contents, and an instruction that a summary is a claim.
"""

import pytest

from app.models.task_card import Artifact, ArtifactPart
from app.utils import self_improve as si


def _leaf(summary: str, tool_calls: int, failed: bool = False) -> Artifact:
    return Artifact(summary=summary, tool_calls=tool_calls, failed=failed)


def _gfx2_iteration() -> Artifact:
    """Shape of 3068d3d0 b-5cc1081c iteration 2: a group artifact whose
    four children all 'passed' without running anything."""
    children = [
        ("Rebuild the frontend bundle", _leaf("this step has no build", 0)),
        ("Run the unit tests for touched code", _leaf("tests deferred", 0)),
        ("Re-render and judge per engine (sequential)",
         _leaf("render-ready deferral recorded", 0)),
        ("Repair the residue", _leaf("retired D-156 and D-181", 3)),
    ]
    stages = [si.stage_evidence(label, art) for label, art in children]
    return Artifact(
        summary="Done. All regression and still-broken defects are now "
                "dispositioned; the ledger reflects it.",
        decisions=["retired D-156", "retired D-181"],
        stages=stages,
        outputs=[
            ArtifactPart(part_type="data", data={
                "still_broken": 85, "regression": 8, "wont_fix": 83}),
            ArtifactPart(part_type="file",
                         file_uri=".ziya/task-runs/x/regression-triage.json"),
        ],
    )


class TestStageEvidenceToolCalls:
    def test_leaf_records_tool_calls(self):
        s = si.stage_evidence("build", _leaf("built", 0))
        assert s["tool_calls"] == 0
        s2 = si.stage_evidence("build", _leaf("built", 7))
        assert s2["tool_calls"] == 7

    def test_container_does_not_record_tool_calls(self):
        """A group artifact always reads tool_calls=0; flagging it would
        mark every container as having run nothing."""
        inner = si.stage_evidence("x", _leaf("ok", 2))
        container = Artifact(summary="grp", stages=[inner])
        s = si.stage_evidence("grp", container)
        assert "tool_calls" not in s

    def test_renderer_flags_zero_tool_calls_only(self):
        stages = [
            si.stage_evidence("build", _leaf("built the bundle", 0)),
            si.stage_evidence("test", _leaf("ran tests", 4)),
        ]
        txt = si.render_stages_for_judge(stages)
        lines = [ln for ln in txt.splitlines() if "PASSED" in ln]
        assert len(lines) == 2
        assert "NO TOOL CALLS" in lines[0]
        assert "NO TOOL CALLS" not in lines[1]


class TestDataPartRendering:
    def test_data_contents_are_shown(self):
        art = _gfx2_iteration()
        txt = si.render_data_parts_for_judge(art.outputs)
        assert '"still_broken": 85' in txt
        assert '"regression": 8' in txt

    def test_no_data_parts(self):
        assert si.render_data_parts_for_judge([]) == "(none)"
        only_file = [ArtifactPart(part_type="file", file_uri="a.json")]
        assert si.render_data_parts_for_judge(only_file) == "(none)"

    def test_large_payload_is_truncated(self):
        big = [ArtifactPart(part_type="data", data={"k": "x" * 5000})]
        txt = si.render_data_parts_for_judge(big)
        assert len(txt) < 5000
        assert txt.endswith("[…truncated]")


class TestEvaluatorPrompt:
    """The seam: what the evaluator actually sends to the model."""

    def _msg(self, art: Artifact) -> str:
        from app.agents.until_evaluator import _build_user_message
        return _build_user_message(
            "show reports no defect with status still-broken or regression",
            art,
        )

    def test_stages_reach_the_prompt_with_tool_flag(self):
        msg = self._msg(_gfx2_iteration())
        assert "STAGES" in msg
        assert "Rebuild the frontend bundle" in msg
        assert "NO TOOL CALLS" in msg

    def test_data_parts_reach_the_prompt(self):
        msg = self._msg(_gfx2_iteration())
        assert '"still_broken": 85' in msg

    def test_summary_is_labelled_a_claim(self):
        msg = self._msg(_gfx2_iteration())
        low = msg.lower()
        assert "claim" in low
        # The agent's "Done." summary is still present -- it is evidence
        # of what the agent believes, just not the only evidence.
        assert "dispositioned" in msg

    def test_system_prompt_names_the_rule(self):
        from app.agents.until_evaluator import _SYSTEM_PROMPT
        low = _SYSTEM_PROMPT.lower()
        assert "claim" in low
        assert "tool" in low

    def test_legacy_artifact_without_stages_still_builds(self):
        """A leaf-task until body has no stages and no data; the prompt
        must degrade to the old shape, not crash."""
        msg = self._msg(Artifact(summary="did the thing"))
        assert "did the thing" in msg
        assert "(no per-stage evidence" in msg


def _sweep_p5_iteration() -> Artifact:
    """Shape of sweep run cli-c72ef56d7591, P5 iteration 2: a leaf task
    whose only emitted part is the contracted ``ziya:text`` residue list.
    The judge said no three times for "DATA is empty" although the list
    the condition names was right there in the text part."""
    residue = (
        "No commits made; the only planned group is already HEAD e688f496.\n"
        "Deferred (15): app/cli.py, app/shadow/sock_server.py ... (shadow "
        "allow-control entry, no attribution); pyproject.toml (bump hunk "
        "mixed with dependency edits); CHANGELOG.1.md (changelog stage).\n"
        "Reserved (1): CHANGELOG.md, left for the bump stage.\n"
        "Excluded (30): untracked in-progress tests and scripts, no entry."
    )
    return Artifact(
        summary="No commits were made this iteration, and none were possible.",
        tool_calls=3,
        outputs=[ArtifactPart(part_type="text", text=residue, label="residue")],
    )


class TestTextPartEvidence:
    """The gap the sweep card hit: a contracted text part is the pass's
    deliverable, and the judge must be shown its content, not a count."""

    def test_text_part_content_is_rendered_with_its_label(self):
        txt = si.render_text_parts_for_judge(_sweep_p5_iteration().outputs)
        assert "[residue]" in txt
        assert "CHANGELOG.1.md (changelog stage)" in txt

    def test_no_text_parts_renders_none(self):
        assert si.render_text_parts_for_judge([]) == "(none)"
        assert si.render_text_parts_for_judge(
            _gfx2_iteration().outputs) == "(none)"   # data + file only
        assert si.render_text_parts_for_judge(
            [ArtifactPart(part_type="text", text="   ")]) == "(none)"

    def test_long_text_part_is_capped(self):
        big = ArtifactPart(part_type="text", text="x" * (si.JUDGE_TEXT_CHARS_CAP + 500))
        txt = si.render_text_parts_for_judge([big])
        assert txt.endswith("[…truncated]")
        assert len(txt) < si.JUDGE_TEXT_CHARS_CAP + 40

    def test_text_part_reaches_the_evaluator_prompt(self):
        from app.agents.until_evaluator import _build_user_message
        msg = _build_user_message(
            "every path still showing as modified or untracked is present in "
            "the exclusion list or the plan's deferred list with a stated reason",
            _sweep_p5_iteration(),
        )
        assert "TEXT PARTS" in msg
        assert "Reserved (1): CHANGELOG.md" in msg
        # The old shape still appears, so a data-only condition is unchanged.
        assert "DATA (emitted by the pass):\n(none)" in msg

    def test_system_prompt_admits_text_parts_and_state_conditions(self):
        from app.agents.until_evaluator import _SYSTEM_PROMPT
        assert "TEXT PARTS" in _SYSTEM_PROMPT
        # Iteration 1 of the same run was refused because "no commits were
        # made this iteration" -- the condition says "is committed", a state.
        assert "state at the end of the pass" in _SYSTEM_PROMPT
