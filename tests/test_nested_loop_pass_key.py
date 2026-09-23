"""A loop nested inside another loop keeps every pass, keyed by the outer
iteration that produced it.

Why: ``append_iteration_summary`` only appends, so a 20-engine Repeat
inside an Until (GFX Stage 2 run 5d0b198c) recorded 0..19, 0..19 — the
run map showed "30/20" and clicking engine 3 lit two dots.  The artifact
FILES were worse: ``{block}_{index}`` was overwritten each pass, so pass
0's output was already gone.

The record is not reset (that loses which engines failed on which pass,
the one thing a repair loop's history is for).  Instead each summary
carries ``pass_key`` — the enclosing frames' indices — and artifacts are
filed per pass.  ``index`` is unique only within a pass; the frontend
groups on the key (runMapModel.buildDotPasses).

A TOP-LEVEL loop records no key and files artifacts under the old name,
so every existing run and every resume path (which is top-level only) is
untouched.
"""

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate, TaskRunBlockState, IterationSummary
from app.storage.task_runs import TaskRunStorage
from app.agents.block_executor import ExecutionContext, execute_block


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="gfx2"))
    for bid, bt in (("verify", "until"), ("engines", "repeat")):
        storage.set_block_state(r.id, TaskRunBlockState(block_id=bid, block_type=bt))
    return r


def _leaf(summary: str, failed: bool = False) -> Artifact:
    return Artifact(summary=summary, tool_calls=1, tokens=1, duration_ms=1,
                    failed=failed)


def _nested(engines):
    return Block(
        block_type="until", id="verify", name="verify",
        until_mode="model", until_condition="ledger clean", until_max=2,
        body=[Block(
            block_type="repeat", id="engines", name="Re-render per engine",
            repeat_mode="for_each",
            repeat_for_each_source=engines,
            body=[Block(block_type="task", id="judge", instructions="judge {{item}}")],
        )],
    )


class TestStorage:
    def test_artifacts_are_filed_per_pass(self, storage, run):
        storage.write_iteration_artifact(run.id, "engines", 0, _leaf("pass0"), pass_key="0")
        storage.write_iteration_artifact(run.id, "engines", 0, _leaf("pass1"), pass_key="1")
        assert storage.read_iteration_artifact(run.id, "engines", 0, pass_key="0").summary == "pass0"
        assert storage.read_iteration_artifact(run.id, "engines", 0, pass_key="1").summary == "pass1"
        # No key: the top-level name, which neither pass wrote.
        assert storage.read_iteration_artifact(run.id, "engines", 0) is None

    def test_top_level_name_is_unchanged(self, storage, run):
        storage.write_iteration_artifact(run.id, "engines", 3, _leaf("top"))
        assert (storage._iteration_dir(run.id) / "engines_3.json").exists()
        assert storage.read_iteration_artifact(run.id, "engines", 3).summary == "top"

    def test_summary_round_trips_pass_key(self, storage, run):
        storage.append_iteration_summary(
            run.id, "engines", IterationSummary(index=0, status="passed", pass_key="2"))
        st = storage.get(run.id).block_states["engines"]
        assert st.iteration_summaries[0].pass_key == "2"


class TestSeam:
    @pytest.mark.asyncio
    async def test_second_outer_pass_is_kept_and_keyed(self, storage, run):
        engines = '["mermaid", "graphviz"]'
        outer_passes = iter([False, True])
        # graphviz fails on pass 0 and passes on pass 1 — the history the
        # record must be able to tell.
        verdicts = iter([False, True, False, False])

        async def stub(b, **kw):
            return _leaf(b.instructions, failed=next(verdicts))

        async def cond(*a, **kw):
            return next(outer_passes)

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model",
                   side_effect=cond):
            await execute_block(_nested(engines), ctx)

        states = storage.get(run.id).block_states
        # Outer loop: top-level, so no key.
        assert [(s.index, s.pass_key) for s in states["verify"].iteration_summaries] \
            == [(0, None), (1, None)]
        # Inner loop: both passes retained, each keyed by the outer index.
        inner = states["engines"].iteration_summaries
        assert [(s.index, s.pass_key, s.status) for s in inner] == [
            (0, "0", "passed"), (1, "0", "failed"),
            (0, "1", "passed"), (1, "1", "passed"),
        ]
        # Within a pass the indices are unique; across passes they repeat.
        by_pass = {}
        for s in inner:
            by_pass.setdefault(s.pass_key, []).append(s.index)
        assert by_pass == {"0": [0, 1], "1": [0, 1]}
        assert states["engines"].planned_iterations == 2
        # Every iteration kept its artifact (retention budget is per pass),
        # and the files are distinct per pass.
        assert all(s.has_artifact for s in inner)
        p0 = storage.read_iteration_artifact(run.id, "engines", 1, pass_key="0")
        p1 = storage.read_iteration_artifact(run.id, "engines", 1, pass_key="1")
        assert p0 is not None and p0.failed
        assert p1 is not None and not p1.failed

    @pytest.mark.asyncio
    async def test_retention_budget_is_per_pass(self, storage, run):
        """A nested loop's 50-pass artifact cap must not be spent across
        outer passes, or a later pass would keep nothing."""
        from app.agents import block_executor as be
        engines = '["a", "b", "c"]'
        outer_passes = iter([False, True])

        async def stub(b, **kw):
            return _leaf(b.instructions)

        async def cond(*a, **kw):
            return next(outer_passes)

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch.object(be, "PASS_ARTIFACT_RETENTION_CAP", 2), \
             patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model",
                   side_effect=cond):
            await execute_block(_nested(engines), ctx)

        inner = storage.get(run.id).block_states["engines"].iteration_summaries
        kept = [(s.pass_key, s.index) for s in inner if s.has_artifact]
        # Two kept per pass, not two total.
        assert kept == [("0", 0), ("0", 1), ("1", 0), ("1", 1)]

    @pytest.mark.asyncio
    async def test_top_level_loop_records_no_key_and_keeps_seeded_prefix(self, storage, run):
        seeded = IterationSummary(index=0, status="passed", replayed=True,
                                  has_artifact=True)
        storage.seed_replayed_iterations(run.id, "engines", [seeded])
        block = Block(
            block_type="repeat", id="engines", repeat_mode="for_each",
            repeat_for_each_source='["a", "b"]',
            body=[Block(block_type="task", id="judge", instructions="judge {{item}}")],
        )

        async def stub(b, **kw):
            return _leaf(b.instructions)

        ctx = ExecutionContext(
            run_id=run.id, storage=storage,
            resume_from_block_id="engines", resume_from_iteration=1,
            resume_iteration_artifacts={0: _leaf("replayed a")},
        )
        with patch("app.agents.block_executor.execute_task_block", stub):
            await execute_block(block, ctx)

        sums = storage.get(run.id).block_states["engines"].iteration_summaries
        assert [(s.index, s.replayed, s.pass_key) for s in sums] \
            == [(0, True, None), (1, False, None)]
        assert (storage._iteration_dir(run.id) / "engines_1.json").exists()
