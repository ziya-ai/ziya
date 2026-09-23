"""IterationSummary carries a compact per-stage digest of the loop body.

Why: blocks inside a loop body have no durable per-block state -- by
design, ``_mark_block_status`` skips them while a binding frame is active
so a 10k-iteration loop does not rewrite the run file per inner block.
After a reload the run map therefore painted every body block ``queued``,
whatever had happened.  GFX Stage 2 run 3068d3d0 rendered a ``done``
until-loop over four never-run children ("Rebuild the frontend bundle",
"Run the unit tests", ...) as four bare queued rows.  The per-stage
outcomes existed, but only in the iteration artifact FILE the map does
not read.

The digest (index, label, status, tool_calls) now rides on the
IterationSummary in the run record, so the map can match a body block by
position to the latest iteration's stage.  These tests assert the seam
from a body block's execution to the persisted summary; the frontend
half (runMapModel deriving a body block's status from it) is covered in
frontend/src/components/TaskCard/__tests__/runMapBodyStatus.test.ts.
"""

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.storage.task_runs import TaskRunStorage
from app.agents.block_executor import ExecutionContext, execute_block
from app.utils import self_improve as si


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="gfx2"))
    storage.set_block_state(r.id, TaskRunBlockState(
        block_id="verify", block_type="until",
    ))
    return r


def _task(id: str, name: str) -> Block:
    return Block(block_type="task", id=id, name=name, instructions=f"do {name}")


def _leaf(summary: str, tool_calls: int, failed: bool = False) -> Artifact:
    return Artifact(summary=summary, tool_calls=tool_calls, failed=failed,
                    tokens=5, duration_ms=1)


class TestDigest:
    def test_digest_is_compact_and_positional(self):
        stages = [
            si.stage_evidence("Rebuild the frontend bundle", _leaf("built", 0), index=0),
            si.stage_evidence("Run the unit tests", _leaf("ran", 4), index=1),
        ]
        d = si.stage_digest(stages)
        assert d == [
            {"index": 0, "label": "Rebuild the frontend bundle",
             "status": "passed", "tool_calls": 0},
            {"index": 1, "label": "Run the unit tests",
             "status": "passed", "tool_calls": 4},
        ]
        # No summary / self_assessment: this is a digest, not the evidence.
        assert all("summary" not in e for e in d)

    def test_digest_omits_tool_calls_for_container_stages(self):
        container = Artifact(summary="group", stages=[{"index": 0}])
        d = si.stage_digest([si.stage_evidence("inner loop", container, index=0)])
        assert "tool_calls" not in d[0]

    def test_digest_caps_length_and_labels(self):
        stages = [si.stage_evidence("x" * 200, _leaf("s", 1), index=i)
                  for i in range(si.STAGE_DIGEST_CAP + 10)]
        d = si.stage_digest(stages)
        assert len(d) == si.STAGE_DIGEST_CAP
        assert len(d[0]["label"]) == si.STAGE_DIGEST_LABEL_CAP + 1  # + ellipsis

    def test_digest_is_none_for_a_leaf(self):
        assert si.stage_digest(None) is None
        assert si.stage_digest([]) is None


class TestSeam:
    @pytest.mark.asyncio
    async def test_until_iteration_summary_carries_body_stages(self, storage, run):
        """The 3068d3d0 shape: a body block 'passed' with zero tool calls
        must be visible in the run record, not only in the artifact file."""
        block = Block(
            block_type="until", id="verify", name="verify",
            until_mode="model", until_condition="everything verified",
            until_max=1,
            body=[
                _task("b-build", "Rebuild the frontend bundle"),
                _task("b-test", "Run the unit tests for touched code"),
            ],
        )
        answers = iter([_leaf("Built the bundle.", 0), _leaf("62 passed", 3)])

        async def stub(b, **kw):
            return next(answers)

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model",
                   return_value=True):
            await execute_block(block, ctx)

        summaries = storage.get(run.id).block_states["verify"].iteration_summaries
        assert len(summaries) == 1
        stages = summaries[0].stages
        assert stages is not None, "digest missing from the persisted summary"
        assert [s["label"] for s in stages] == [
            "Rebuild the frontend bundle", "Run the unit tests for touched code"]
        assert [s["index"] for s in stages] == [0, 1]
        assert stages[0]["tool_calls"] == 0
        assert stages[1]["tool_calls"] == 3
        # Body blocks still have no durable state of their own -- the digest
        # is what stands in for it.
        assert "b-build" not in storage.get(run.id).block_states

    @pytest.mark.asyncio
    async def test_skipped_sibling_is_in_the_digest(self, storage, run):
        """on_failure=stop: the never-run sibling appears as 'skipped' at
        its own body position, so the map can say so instead of queued."""
        block = Block(
            block_type="until", id="verify", name="verify",
            until_mode="model", until_condition="c", until_max=1,
            on_failure="stop",
            body=[_task("b-a", "first"), _task("b-b", "second"), _task("b-c", "third")],
        )

        async def stub(b, **kw):
            return _leaf("boom", 1, failed=True)

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model",
                   return_value=False):
            await execute_block(block, ctx)

        stages = storage.get(run.id).block_states["verify"].iteration_summaries[-1].stages
        by_index = {s["index"]: s["status"] for s in stages}
        assert by_index == {0: "failed", 1: "skipped", 2: "skipped"}

    def test_summary_without_digest_still_loads(self, storage, run):
        """Records written before the field existed have no key at all."""
        from app.models.task_run import IterationSummary
        s = IterationSummary(index=0, status="passed")
        assert s.stages is None
        assert "stages" not in {k for k, v in s.model_dump().items() if v is not None}
