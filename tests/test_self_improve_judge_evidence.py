"""
Fix 2 of the self-improvement audit: the judge must see per-stage
evidence, not the last iteration's self-report.

Observed (GFX Stage 1 card, 10 verdicts): the 20-engine repeat block's
artifact carried ``summary = last iteration's summary``; the judge was
asked whether EVERY engine met the criterion while being shown one
engine's prose and nothing about the other nineteen.  A group inherited
the same single summary.  ``self_assessment`` is leaf-only so read
``(none)`` at every improving level; ``outputs`` never reached the
prompt at all.  Zero revise verdicts in 20 records.

The contract under test:
  * container artifacts carry ``stages`` — one entry per iteration
    (repeat/until/parallel) or per child (group) with status + summary;
  * a group's stages are ITS children, not inherited from a nested
    repeat's iterations;
  * the judge prompt lists every failed stage and the output part
    names, and says so when a stage failed even though the container's
    ``failed`` flag (last-wins) is False;
  * the ledger record carries the stage counts.

Every test in the executor/evaluator classes fails against the
unpatched code (no ``stages`` on Artifact; prompt shows one summary).
"""
import asyncio
from unittest.mock import patch

import pytest

from app.models.task_card import Artifact, ArtifactPart, Block
from app.utils import self_improve as si


def _task(id_: str) -> Block:
    return Block(block_type="task", id=id_, name=id_, instructions="do it")


def _art(summary="ok", failed=False, **kw) -> Artifact:
    return Artifact(summary=summary, failed=failed, tokens=1,
                    duration_ms=1, **kw)


def _stub(responses):
    it = iter(responses)

    async def _s(block, project_root=None, project_id=None, run_id=None):
        return next(it)
    return _s


def _ctx():
    from app.agents.block_executor import ExecutionContext
    return ExecutionContext(run_id="r", project_id=None, storage=None)


# ── 1. Pure helpers (writable half — pass before the diffs) ─────

class TestStageHelpers:
    def test_stage_evidence_caps_summary_and_reads_status(self):
        long = "x" * (si.STAGE_SUMMARY_CAP + 50)
        s = si.stage_evidence("eng", _art(long, failed=True), index=3)
        assert s["status"] == "failed" and s["index"] == 3
        assert s["label"] == "eng"
        assert len(s["summary"]) <= si.STAGE_SUMMARY_CAP + 6
        assert s["summary"].endswith("[…]")

    def test_status_override_for_cancelled(self):
        s = si.stage_evidence("e", _art(), status="cancelled")
        assert s["status"] == "cancelled"
        assert si.stage_counts([s]) == {
            "total": 1, "passed": 0, "failed": 0, "other": 1}

    def test_render_lists_every_failure_even_past_the_cap(self):
        stages = [si.stage_evidence(f"p{i}", _art(f"pass {i}"), index=i)
                  for i in range(si.JUDGE_STAGE_LIST_CAP + 10)]
        stages.append(si.stage_evidence(
            "broken", _art("render timeout on wave 3", failed=True),
            index=99))
        text = si.render_stages_for_judge(stages)
        assert "1 failed" in text
        assert "FAILED broken: render timeout on wave 3" in text
        assert "more passed stage(s) not listed" in text

    def test_render_shows_leaf_self_assessment(self):
        s = si.stage_evidence("e", Artifact(
            summary="s", self_assessment={
                "objective_met": "partial", "rationale": "wave 4 skipped"}))
        text = si.render_stages_for_judge([s])
        assert "objective_met=partial" in text and "wave 4 skipped" in text

    def test_render_outputs_names_files(self):
        outs = [ArtifactPart(part_type="file",
                             file_uri=".ziya/gfx-sweep/triage/mermaid.json"),
                ArtifactPart(part_type="text", text="hello"),
                ArtifactPart(part_type="data", data={"a": 1})]
        line = si.render_outputs_for_judge(outs)
        assert "3 part(s)" in line
        assert "triage/mermaid.json" in line
        assert "1 text" in line and "1 data" in line
        assert si.render_outputs_for_judge([]) == "(none)"


# ── 2. Executor: container artifacts carry stages ───────────────

class TestRepeatStages:
    @pytest.mark.asyncio
    async def test_serial_repeat_records_every_iteration(self):
        block = Block(block_type="repeat", id="rep", name="r",
                      repeat_mode="count", repeat_count=3,
                      body=[_task("inner")])
        stub = _stub([_art("a"), _art("b: broke", failed=True), _art("c")])
        from app.agents import block_executor as bx
        with patch("app.agents.block_executor.execute_task_block", stub):
            art = await bx.execute_block(block, _ctx())
        # Last-wins semantics of summary/failed are unchanged …
        assert art.summary == "c" and art.failed is False
        # … but the evidence of iteration 1's failure now survives.
        stages = getattr(art, "stages", None)
        assert stages and len(stages) == 3
        assert [s["status"] for s in stages] == ["passed", "failed", "passed"]
        assert stages[1]["summary"] == "b: broke"
        assert stages[1]["index"] == 1

    @pytest.mark.asyncio
    async def test_for_each_stage_label_is_the_item_key(self):
        block = Block(block_type="repeat", id="rep", name="r",
                      repeat_mode="for_each",
                      repeat_for_each_source='["mermaid", "plotly"]',
                      body=[_task("inner")])
        stub = _stub([_art("m"), _art("p")])
        from app.agents import block_executor as bx
        with patch("app.agents.block_executor.execute_task_block", stub):
            art = await bx.execute_block(block, _ctx())
        labels = [s["label"] for s in art.stages]
        assert labels == ["mermaid", "plotly"]

    @pytest.mark.asyncio
    async def test_parallel_repeat_records_every_iteration(self):
        block = Block(block_type="repeat", id="rep", name="r",
                      repeat_mode="count", repeat_count=3,
                      repeat_parallel=True, body=[_task("inner")])
        stub = _stub([_art("a"), _art("b", failed=True), _art("c")])
        from app.agents import block_executor as bx
        with patch("app.agents.block_executor.execute_task_block", stub):
            art = await bx.execute_block(block, _ctx())
        assert len(art.stages) == 3
        assert sorted(s["index"] for s in art.stages) == [0, 1, 2]
        assert sum(1 for s in art.stages if s["status"] == "failed") == 1


class TestGroupStages:
    @pytest.mark.asyncio
    async def test_group_stages_are_its_children_not_nested_iterations(self):
        # A group whose last child is a 3-iteration repeat.  The group's
        # stages must be [task, repeat] — not the repeat's 3 iterations
        # leaking upward through the last-wins model_copy.
        rep = Block(block_type="repeat", id="rep", name="sweep",
                    repeat_mode="count", repeat_count=3,
                    body=[_task("inner")])
        grp = Block(block_type="group", id="grp", name="g",
                    body=[_task("verify"), rep])
        stub = _stub([_art("verified"), _art("i0"), _art("i1", failed=True),
                      _art("i2")])
        from app.agents import block_executor as bx
        with patch("app.agents.block_executor.execute_task_block", stub):
            art = await bx.execute_block(grp, _ctx())
        assert [s["label"] for s in art.stages] == ["verify", "sweep"]
        assert art.stages[0]["summary"] == "verified"
        # The nested repeat's own failure count is what the group's
        # second stage reports through its summary/status, not lost.
        assert art.stages[1]["status"] == "passed"  # last-wins, unchanged

    @pytest.mark.asyncio
    async def test_group_stop_marks_skipped_children(self):
        grp = Block(block_type="group", id="grp", name="g", on_failure="stop",
                    body=[_task("a"), _task("b"), _task("c")])
        stub = _stub([_art("a ok"), _art("b broke", failed=True)])
        from app.agents import block_executor as bx
        with patch("app.agents.block_executor.execute_task_block", stub):
            art = await bx.execute_block(grp, _ctx())
        assert [s["status"] for s in art.stages] == [
            "passed", "failed", "skipped"]


class TestParallelAndUntilStages:
    @pytest.mark.asyncio
    async def test_parallel_block_records_each_branch(self):
        par = Block(block_type="parallel", id="par", name="p",
                    body=[_task("x"), _task("y")])
        stub = _stub([_art("x ok"), _art("y broke", failed=True)])
        from app.agents import block_executor as bx
        with patch("app.agents.block_executor.execute_task_block", stub):
            art = await bx.execute_block(par, _ctx())
        assert sorted(s["label"] for s in art.stages) == ["x", "y"]
        assert {s["label"]: s["status"] for s in art.stages}["y"] == "failed"

    @pytest.mark.asyncio
    async def test_until_records_each_iteration(self):
        blk = Block(block_type="until", id="u", name="u",
                    until_condition="never", until_max=2,
                    body=[_task("inner")])
        stub = _stub([_art("try 1", failed=True), _art("try 2")])
        from app.agents import block_executor as bx

        async def fake_eval(*a, **k):
            return False
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch.object(bx, "_evaluate_until_condition_with_model", fake_eval):
            art = await bx.execute_block(blk, _ctx())
        assert [s["status"] for s in art.stages] == ["failed", "passed"]


# ── 3. Evaluator prompt carries the evidence ────────────────────

class TestJudgePromptEvidence:
    def _prompt(self, artifact):
        from app.agents.improve_evaluator import _build_user_message
        block = Block(block_type="group", id="g", name="", body=[
            Block(block_type="task", id="t", name="", instructions="x")])
        return _build_user_message(block, artifact, "", "conservative", [], 0)

    def test_failed_stage_is_in_the_prompt_despite_failed_false(self):
        art = Artifact(summary="all good (says the last engine)")
        art.stages = [
            si.stage_evidence("mermaid", _art("fine"), index=0),
            si.stage_evidence("plotly", _art("timed out; 0/60 specs",
                                             failed=True), index=1),
        ]
        msg = self._prompt(art)
        assert "1 failed" in msg
        assert "FAILED plotly: timed out; 0/60 specs" in msg
        # The last-wins flag is False; the prompt must not let that
        # stand alone as the verdict on the level.
        assert "failed: False" in msg
        assert "stage(s) failed" in msg.lower() or "1 failed" in msg

    def test_outputs_are_named(self):
        art = Artifact(summary="s", outputs=[ArtifactPart(
            part_type="file", file_uri=".ziya/gfx-sweep/triage/d3.json")])
        assert "triage/d3.json" in self._prompt(art)

    def test_legacy_artifact_without_stages_still_renders(self):
        msg = self._prompt(Artifact(summary="single"))
        assert "single" in msg
        assert "no per-stage evidence" in msg


# ── 4. Seam: executor → judge → ledger ──────────────────────────

class TestSeam:
    def test_judge_receives_stage_failures_and_ledger_counts_them(
            self, monkeypatch):
        from app.agents import block_executor as bx
        import app.services.model_resolver as mr
        seen = {}

        async def fake_call(category, system_prompt, user_message, **kw):
            seen["msg"] = user_message
            return ('{"verdict": "stop", "rationale": "env", '
                    '"lesson": "l", "patch": {}}')
        monkeypatch.setattr(mr, "call_service_model", fake_call)

        async def inner(block, ctx):
            a = Artifact(summary="last engine fine")
            a.stages = [
                si.stage_evidence("ok-engine", _art("fine"), index=0),
                si.stage_evidence("bad-engine", _art("crashed at wave 2",
                                                     failed=True), index=1),
            ]
            return a

        recorded = []

        class FakeLedger:
            def for_block(self, *a, **k):
                return []

            def seen_patch_hash(self, *a, **k):
                return False

            def record(self, rec):
                recorded.append(rec)

        monkeypatch.setattr(si, "LessonLedger", lambda *a, **k: FakeLedger())
        blk = Block(block_type="group", id="g", name="lvl", self_improve=True,
                    body=[Block(block_type="task", id="t", name="t",
                                instructions="x")])
        from app.agents.block_executor import ExecutionContext
        ctx = ExecutionContext(run_id="r", project_id="p", storage=None)
        asyncio.run(bx._maybe_self_improve(blk, ctx, inner))
        assert "FAILED bad-engine: crashed at wave 2" in seen["msg"]
        assert recorded and recorded[0]["stages"] == {
            "total": 2, "passed": 1, "failed": 1, "other": 0}
