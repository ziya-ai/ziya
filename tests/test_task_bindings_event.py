"""The ``task_bindings`` event: what each ``{{placeholder}}`` in a task's
instructions expanded to, emitted at task dispatch so the run inspector
can head an iteration with the resolved values.

Three seams are pinned here:

  1. ``task_templating.resolve_placeholders`` agrees with ``render``
     placeholder-for-placeholder -- the event must never claim a value
     the model did not see.
  2. The block executor actually emits the event, once per task dispatch,
     tagged with the ITERATION OWNER's block_id and index (the same
     re-tagging the streaming deltas get) so the frontend routes it into
     the iteration's bucket rather than a phantom one keyed to the task.
  3. When rendering is skipped for a dispatch (a bare task outside any
     loop), every placeholder is reported unresolved -- because that is
     what happened: the model got the literal braces.
"""

import pytest
from unittest.mock import patch

from app.agents import task_templating
from app.agents.task_templating import (
    IterationBindings, list_placeholders, render, resolve_placeholders,
)
from app.agents.block_executor import (
    ExecutionContext, execute_block, _BINDING_VALUE_CAP,
)
from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunBlockState, TaskRunCreate
from app.storage.task_runs import TaskRunStorage


# ── 1. resolver agrees with render ──────────────────────────────────────

class TestListPlaceholders:
    def test_first_appearance_order_and_dedupe(self):
        got = list_placeholders("a {{item}} b {{ index }} c {{item}} d {{var.X}}")
        assert got == ["item", "index", "var.X"]

    def test_none_and_no_braces(self):
        assert list_placeholders(None) == []
        assert list_placeholders("") == []
        assert list_placeholders("nothing here") == []

    def test_single_brace_is_not_a_placeholder(self):
        # The engine is double-brace only; {item} is literal text.
        assert list_placeholders("run {item} then {{item}}") == ["item"]


class TestResolvePlaceholders:
    def _bindings(self, **kw):
        return IterationBindings(**kw)

    def test_each_value_matches_render(self):
        tpl = "i={{index}} it={{item}} prev={{previous.summary}} bad={{bogus}} v={{var.K}}"
        b = self._bindings(
            index=2, item="graphviz",
            previous=Artifact(summary="prior text"),
            variables={"K": "deep"},
        )
        entries = {e["placeholder"]: e for e in resolve_placeholders(tpl, b)}
        rendered = render(tpl, b)
        assert rendered == "i=2 it=graphviz prev=prior text bad={{bogus}} v=deep"
        assert entries["index"] == {"placeholder": "index", "value": "2", "resolved": True}
        assert entries["item"]["value"] == "graphviz"
        assert entries["previous.summary"]["value"] == "prior text"
        assert entries["var.K"]["value"] == "deep"
        # Unknown head: render left it literal, so resolved is False and
        # there is no value to claim.
        assert entries["bogus"] == {"placeholder": "bogus", "value": None, "resolved": False}

    def test_known_but_missing_is_resolved_empty(self):
        # {{previous.summary}} on iteration 0 renders "" -- resolved, empty.
        entries = resolve_placeholders("{{previous.summary}}", self._bindings(index=0))
        assert entries == [{"placeholder": "previous.summary", "value": "", "resolved": True}]

    def test_order_and_dedupe_follow_list_placeholders(self):
        entries = resolve_placeholders("{{item}} {{index}} {{item}}", self._bindings(item="x"))
        assert [e["placeholder"] for e in entries] == ["item", "index"]

    def test_empty_template(self):
        assert resolve_placeholders(None, self._bindings()) == []
        assert resolve_placeholders("plain", self._bindings()) == []


# ── 2/3. the executor emits it, tagged like the deltas ─────────────────

@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    return storage.create(TaskRunCreate(card_id="card-1"))


@pytest.fixture
def captured_events():
    events = []

    async def _fake_safe_push(run_id, event):
        events.append(event)

    with patch("app.agents.task_run_stream_relay.safe_push", _fake_safe_push):
        yield events


def _seed(storage, run_id, block):
    if block.id:
        storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type,
        ))
    for c in block.body or []:
        _seed(storage, run_id, c)


def _stub(captured_instructions, summaries=None):
    n = {"i": 0}
    canned = summaries or []

    async def _run(block, project_root=None, project_id=None, **kwargs):
        captured_instructions.append(block.instructions or "")
        i = n["i"]
        n["i"] += 1
        return Artifact(summary=canned[i] if i < len(canned) else f"iter-{i}",
                        duration_ms=1)
    return _run


def _bindings_events(events):
    return [e for e in events if e.get("type") == "task_bindings"]


class TestEmittedInsideRepeat:
    @pytest.mark.asyncio
    async def test_one_event_per_iteration_tagged_with_loop_owner(
        self, storage, run, captured_events,
    ):
        task = Block(block_type="task", id="task-1", name="T",
                     instructions="file={{item}} prev={{previous.summary}} oops={{nope}}")
        loop = Block(
            block_type="repeat", id="repeat-1", name="Loop",
            repeat_mode="for_each", repeat_propagate="last",
            repeat_for_each_source='["a.py", "b.py"]',
            body=[task],
        )
        _seed(storage, run.id, loop)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        got_instr = []
        with patch("app.agents.block_executor.execute_task_block",
                   _stub(got_instr, ["first summary"])):
            await execute_block(loop, ctx)

        evs = _bindings_events(captured_events)
        assert len(evs) == 2, [e.get("type") for e in captured_events]
        # Tagged with the REPEAT's id and the iteration ordinal, not the
        # inner task id -- that is what the frontend buckets on.
        assert [(e["block_id"], e["index"]) for e in evs] == [("repeat-1", 0), ("repeat-1", 1)]
        assert all(e["task_block_id"] == "task-1" for e in evs)

        by0 = {b["placeholder"]: b for b in evs[0]["bindings"]}
        by1 = {b["placeholder"]: b for b in evs[1]["bindings"]}
        assert by0["item"] == {"placeholder": "item", "value": "a.py", "resolved": True}
        assert by1["item"]["value"] == "b.py"
        # Iteration 0 has no previous: resolved, empty.  Iteration 1 sees
        # iteration 0's summary.
        assert by0["previous.summary"] == {"placeholder": "previous.summary", "value": "", "resolved": True}
        assert by1["previous.summary"]["value"] == "first summary"
        # Typo surfaces as unresolved rather than vanishing.
        assert by0["nope"]["resolved"] is False and by0["nope"]["value"] is None

        # Faithfulness: the event's values are exactly what the model got.
        assert got_instr[1].endswith("file=b.py prev=first summary oops={{nope}}")

    @pytest.mark.asyncio
    async def test_no_event_when_instructions_have_no_placeholder(
        self, storage, run, captured_events,
    ):
        task = Block(block_type="task", id="task-1", name="T", instructions="just do it")
        loop = Block(block_type="repeat", id="repeat-1", name="Loop",
                     repeat_mode="count", repeat_count=2, body=[task])
        _seed(storage, run.id, loop)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", _stub([])):
            await execute_block(loop, ctx)
        assert _bindings_events(captured_events) == []
        # ...but the iterations themselves still ran (positive control).
        assert sum(1 for e in captured_events if e.get("type") == "iteration_started") == 2

    @pytest.mark.asyncio
    async def test_long_values_are_capped_with_length(
        self, storage, run, captured_events,
    ):
        big = "x" * (_BINDING_VALUE_CAP + 500)
        task = Block(block_type="task", id="task-1", name="T",
                     instructions="p={{previous.summary}}")
        loop = Block(block_type="repeat", id="repeat-1", name="Loop",
                     repeat_mode="count", repeat_count=2, repeat_propagate="last",
                     body=[task])
        _seed(storage, run.id, loop)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", _stub([], [big])):
            await execute_block(loop, ctx)
        evs = _bindings_events(captured_events)
        prev = evs[1]["bindings"][0]
        assert prev["truncated"] is True
        assert prev["length"] == len(big)
        assert len(prev["value"]) == _BINDING_VALUE_CAP
        # The cap is a wire concern only; the model still got the whole text.
        assert "truncated" not in evs[0]["bindings"][0]


class TestBareTask:
    @pytest.mark.asyncio
    async def test_skipped_render_reports_everything_unresolved(
        self, storage, run, captured_events,
    ):
        # Outside any loop, with no state, rendering is skipped so the
        # model sees "plain {{index}}" literally.  The event must say so
        # rather than claim index resolved to "0".
        task = Block(block_type="task", id="task-1", name="T", instructions="plain {{index}}")
        _seed(storage, run.id, task)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        got = []
        with patch("app.agents.block_executor.execute_task_block", _stub(got)):
            await execute_block(task, ctx)
        assert got == ["plain {{index}}"]
        evs = _bindings_events(captured_events)
        assert len(evs) == 1
        assert evs[0]["block_id"] == "task-1"
        assert "index" not in evs[0]  # no iteration ordinal outside a loop
        assert evs[0]["bindings"] == [{"placeholder": "index", "value": None, "resolved": False}]

    @pytest.mark.asyncio
    async def test_bare_task_with_state_var_resolves(
        self, storage, run, captured_events,
    ):
        task = Block(block_type="task", id="task-1", name="T",
                     instructions="depth={{var.DEPTH}} run={{run.short}}")
        _seed(storage, run.id, task)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        ctx.variables["DEPTH"] = "deep"
        with patch("app.agents.block_executor.execute_task_block", _stub([])):
            await execute_block(task, ctx)
        evs = _bindings_events(captured_events)
        by = {b["placeholder"]: b for b in evs[0]["bindings"]}
        assert by["var.DEPTH"]["value"] == "deep"
        assert by["run.short"]["value"] == run.id[:8]
