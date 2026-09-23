"""Template resolution, for the record.

The live ``task_bindings`` event (tests/test_task_bindings_event.py) is
gone once the relay's ring buffer rolls; this is the durable half — a
``TemplateResolution`` on the artifact, read back from storage the way
the run map's focused-iteration panel reads it.

Reconstructed from the 2026-08-27 design ("Resolved for iteration #N"):
the card says "Wave 3 ({{item}})", so per-iteration truth has to be
recorded per iteration.  Also pins the "trivially fixed" half of that
conversation — executor-generated decision lines built from a block's
NAME now render the placeholder instead of quoting the template.
"""

import pytest
from unittest.mock import patch

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block, TemplateResolution
from app.models.task_run import TaskRunBlockState, TaskRunCreate
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    return storage.create(TaskRunCreate(card_id="card-1"))


@pytest.fixture
def quiet_relay():
    async def _noop(run_id, event):
        pass
    with patch("app.agents.task_run_stream_relay.safe_push", _noop):
        yield


def _seed(storage, run_id, block):
    if block.id:
        storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type,
        ))
    for c in block.body or []:
        _seed(storage, run_id, c)


def _stub(summaries=None, fail_on=None):
    n = {"i": 0}
    canned = summaries or []
    fail_on = set(fail_on or ())

    async def _run(block, project_root=None, project_id=None, **kw):
        i = n["i"]
        n["i"] += 1
        s = canned[i] if i < len(canned) else f"summary-{i}"
        return Artifact(summary=s, duration_ms=1, failed=(i in fail_on))
    return _run


def _loop(*body, name="Loop", parallel=False, on_failure=None):
    return Block(
        block_type="repeat", id="loop-1", name=name,
        repeat_mode="for_each", repeat_propagate="last",
        repeat_for_each_source='["graphviz", "mermaid"]',
        repeat_parallel=parallel, on_failure=on_failure,
        body=list(body),
    )


class TestIterationArtifactCarriesResolution:
    @pytest.mark.asyncio
    async def test_persisted_iteration_artifact_has_resolved_name_and_bindings(
        self, storage, run, quiet_relay,
    ):
        task = Block(block_type="task", id="t1", name="Wave 3 ({{item}})",
                     instructions="Engine under test: {{item}}. Prior: {{previous.summary}}")
        loop = _loop(task)
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block",
                   _stub(["did graphviz"])):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))

        # Read back through storage — the same path the /iterations API
        # serves the focused-iteration panel from.
        a1 = storage.read_iteration_artifact(run.id, "loop-1", 1)
        assert a1 is not None
        assert len(a1.template_resolutions) == 1
        r = a1.template_resolutions[0]
        assert isinstance(r, TemplateResolution)
        assert r.task_block_id == "t1"
        assert r.authored_name == "Wave 3 ({{item}})"
        assert r.resolved_name == "Wave 3 (mermaid)"
        assert r.resolved_instructions == (
            "Engine under test: mermaid. Prior: did graphviz"
        )
        by = {b.placeholder: b for b in r.bindings}
        assert by["item"].value == "mermaid" and by["item"].resolved
        assert by["previous.summary"].value == "did graphviz"

        a0 = storage.read_iteration_artifact(run.id, "loop-1", 0)
        r0 = a0.template_resolutions[0]
        assert r0.resolved_name == "Wave 3 (graphviz)"
        # Known placeholder, no data on iteration 0: resolved, empty —
        # the renderer's line, not "unresolved".
        b0 = {b.placeholder: b for b in r0.bindings}["previous.summary"]
        assert b0.resolved and b0.value == ""

    @pytest.mark.asyncio
    async def test_multi_task_body_records_one_entry_per_task(
        self, storage, run, quiet_relay,
    ):
        t1 = Block(block_type="task", id="t1", name="Plan {{item}}", instructions="plan {{item}}")
        t2 = Block(block_type="task", id="t2", name="Do", instructions="do {{item}}")
        loop = _loop(t1, t2)
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))
        a = storage.read_iteration_artifact(run.id, "loop-1", 0)
        assert [r.task_block_id for r in a.template_resolutions] == ["t1", "t2"]
        assert a.template_resolutions[0].resolved_name == "Plan graphviz"
        # An untemplated name records no resolved form.
        assert a.template_resolutions[1].resolved_name is None
        assert a.template_resolutions[1].resolved_instructions == "do graphviz"

    @pytest.mark.asyncio
    async def test_parallel_iterations_do_not_share_records(
        self, storage, run, quiet_relay,
    ):
        task = Block(block_type="task", id="t1", name="W ({{item}})", instructions="x {{item}}")
        loop = _loop(task, parallel=True)
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))
        names = sorted(
            storage.read_iteration_artifact(run.id, "loop-1", i)
            .template_resolutions[0].resolved_name
            for i in (0, 1)
        )
        assert names == ["W (graphviz)", "W (mermaid)"]
        for i in (0, 1):
            assert len(storage.read_iteration_artifact(
                run.id, "loop-1", i).template_resolutions) == 1

    @pytest.mark.asyncio
    async def test_untemplated_task_records_nothing(self, storage, run, quiet_relay):
        task = Block(block_type="task", id="t1", name="Plain", instructions="no braces")
        loop = _loop(task)
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))
        a = storage.read_iteration_artifact(run.id, "loop-1", 0)
        assert a.template_resolutions == []


class TestBareTask:
    @pytest.mark.asyncio
    async def test_block_state_artifact_carries_var_resolution(
        self, storage, run, quiet_relay,
    ):
        state = Block(block_type="state", id="s1", name="S",
                      state_variables={"DEPTH": "deep"})
        task = Block(block_type="task", id="t1", name="Review",
                     instructions="depth={{var.DEPTH}}")
        root = Block(block_type="group", id="g1", name="G", body=[state, task])
        _seed(storage, run.id, root)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(root, ExecutionContext(run_id=run.id, storage=storage))
        st = storage.get(run.id).block_states["t1"]
        assert st.artifact is not None
        [r] = st.artifact.template_resolutions
        assert r.resolved_instructions == "depth=deep"
        assert r.resolved_name is None

    @pytest.mark.asyncio
    async def test_loop_scoped_placeholder_outside_loop_is_unresolved(
        self, storage, run, quiet_relay,
    ):
        # Rendering is skipped entirely here, so the model saw the braces;
        # the record must say so rather than claim index=0.
        task = Block(block_type="task", id="t1", name="T", instructions="n={{index}}")
        _seed(storage, run.id, task)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(task, ExecutionContext(run_id=run.id, storage=storage))
        [r] = storage.get(run.id).block_states["t1"].artifact.template_resolutions
        assert r.resolved_instructions is None
        assert [(b.placeholder, b.resolved, b.value) for b in r.bindings] == [
            ("index", False, None),
        ]


class TestGeneratedProseRendersNames:
    @pytest.mark.asyncio
    async def test_on_failure_stop_decision_names_the_item(
        self, storage, run, quiet_relay,
    ):
        # The "trivially fixed" leak: "sequence stopped: step 1/2 (Wave
        # ({{item}})) failed" is a sentence about THIS iteration.
        t1 = Block(block_type="task", id="t1", name="Wave ({{item}})", instructions="a {{item}}")
        t2 = Block(block_type="task", id="t2", name="After", instructions="b")
        loop = _loop(t1, t2, on_failure="stop")
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block",
                   _stub(fail_on={0})):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))
        a0 = storage.read_iteration_artifact(run.id, "loop-1", 0)
        stop_lines = [d for d in a0.decisions if d.startswith("sequence stopped")]
        assert stop_lines, a0.decisions
        assert "(Wave (graphviz))" in stop_lines[0]
        assert "{{item}}" not in stop_lines[0]
        # Stage evidence uses the same label.
        assert a0.stages and a0.stages[0]["label"] == "Wave (graphviz)"

    @pytest.mark.asyncio
    async def test_raising_child_summary_names_the_item(
        self, storage, run, quiet_relay,
    ):
        t1 = Block(block_type="task", id="t1", name="Wave ({{item}})", instructions="a")

        async def _boom(block, **kw):
            raise RuntimeError("kaput")

        loop = _loop(t1)
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block", _boom):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))
        a0 = storage.read_iteration_artifact(run.id, "loop-1", 0)
        assert a0.failed
        assert a0.summary.startswith("Wave (graphviz) raised RuntimeError")


class TestSummaryOutlivesArtifact:
    """The resolved name rides on the always-retained IterationSummary.

    Past PASS_ARTIFACT_RETENTION_CAP a passing iteration's artifact is
    not written, so the panel cannot show its TemplateResolution — but a
    200-iteration for_each is exactly where a reader needs "#57 · Wave 3
    (svg-w57)" most.  Asserted through storage read-back, the path the
    REST snapshot and the run map's dot strip use.
    """

    @pytest.mark.asyncio
    async def test_every_summary_carries_resolved_name_past_the_cap(
        self, storage, run, quiet_relay,
    ):
        from app.agents.block_executor import PASS_ARTIFACT_RETENTION_CAP
        n = PASS_ARTIFACT_RETENTION_CAP + 3
        items = [f"eng-{i}" for i in range(n)]
        task = Block(block_type="task", id="t1", name="Wave ({{item}})",
                     instructions="engine {{item}}")
        loop = Block(
            block_type="repeat", id="loop-1", name="Loop",
            repeat_mode="for_each", repeat_propagate="none",
            repeat_for_each_source=__import__("json").dumps(items),
            body=[task],
        )
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))

        state = storage.get(run.id).block_states["loop-1"]
        summaries = {s.index: s for s in state.iteration_summaries}
        assert len(summaries) == n
        last = summaries[n - 1]
        # The artifact for this iteration was dropped by retention...
        assert last.has_artifact is False
        assert storage.read_iteration_artifact(run.id, "loop-1", n - 1) is None
        # ...but the summary still names it.
        assert last.resolved_name == f"Wave (eng-{n - 1})"
        # And a retained one agrees with its own artifact.
        first = summaries[0]
        assert first.has_artifact is True
        art = storage.read_iteration_artifact(run.id, "loop-1", 0)
        assert first.resolved_name == art.template_resolutions[0].resolved_name == "Wave (eng-0)"

    @pytest.mark.asyncio
    async def test_untemplated_name_leaves_summary_name_none(
        self, storage, run, quiet_relay,
    ):
        # Instructions templated, name not: bindings exist on the
        # artifact, but there is no resolved NAME to lift.
        task = Block(block_type="task", id="t1", name="Plain",
                     instructions="engine {{item}}")
        loop = _loop(task)
        _seed(storage, run.id, loop)
        with patch("app.agents.block_executor.execute_task_block", _stub()):
            await execute_block(loop, ExecutionContext(run_id=run.id, storage=storage))
        state = storage.get(run.id).block_states["loop-1"]
        assert all(s.resolved_name is None for s in state.iteration_summaries)
        assert storage.read_iteration_artifact(run.id, "loop-1", 0).template_resolutions
