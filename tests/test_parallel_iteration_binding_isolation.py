"""Concurrent iterations of a parallel Repeat must not see each other's
iteration bindings.

The defect this file exists for produced WRONG ANSWERS silently, which is
the worst failure mode in the task system: nothing errored, every
iteration reported ``passed``, and the artifacts were internally
plausible.

Mechanism.  ``_run_one`` appends its per-iteration bindings to
``ctx.binding_stack`` and ``_execute_sequence`` pushes a slot onto
``ctx.sibling_stack`` — both plain lists on the ONE ``ExecutionContext``
shared by every iteration.  Templating then resolves ``{{item}}`` from
``ctx.binding_stack[-1]`` and ``{{previous_sibling}}`` from
``ctx.sibling_stack[-1]`` (``_render_block``).  Under
``repeat_parallel=True`` the iterations are asyncio Tasks that interleave
at every await, so ``[-1]`` is whichever iteration most recently pushed —
not the caller's own.

Observed in the field on a 60-wide two-stage fan-out: iteration 59's
Stage B was handed a ``{{previous_sibling.summary}}`` describing a
DIFFERENT capability than its own ``{{item}}``, and wrote its output file
under the wrong capability id.  A reviewer reading that file has no way
to tell it apart from a correct one.

These tests assert the fix, so they must FAIL against the unpatched
executor.  ``test_stage_b_sees_its_own_stage_a`` is the one that pins the
reported symptom directly.
"""

import asyncio
import re

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block, TaskScope

WIDTH = 8
ITEMS = [f"cap-{i:02d}" for i in range(WIDTH)]


@pytest.fixture
def interleaving_tasks(monkeypatch):
    """A task executor that yields control, forcing real interleaving.

    Two suspension points are simulated, and BOTH are needed to reproduce
    the defect rather than merely to run concurrently:

    * inside the task body (``execute_task_block``), which is where a real
      streaming model call spends its time; and
    * inside ``_emit``, which every block transition awaits to publish to
      the SSE relay.

    The second one is the load-bearing one and was missed on the first
    attempt at this fixture.  ``_execute_sequence`` writes the completed
    child into ``ctx.sibling_stack[-1]`` and the NEXT child's templating
    reads it back; if nothing suspends in between, each iteration reads
    back its own write and the shared slot is never observed to be
    corrupt.  Production always suspends there (``execute_block`` ->
    ``_mark_block_status`` -> ``_emit`` -> relay), so a fixture that does
    not is testing a timing that does not occur.

    Returning the RENDERED instructions as the summary is what lets a
    later assertion see which bindings each task actually resolved.
    """
    calls = []

    async def _fake(block, **kwargs):
        # Suspend twice, before and after recording, so a sibling is
        # guaranteed to push its own bindings mid-flight.
        await asyncio.sleep(0)
        calls.append({
            "name": block.name,
            "instructions": block.instructions or "",
        })
        await asyncio.sleep(0)
        return Artifact(
            summary=(block.instructions or block.name or ""),
            created_at=0.0,
        )

    async def _yielding_emit(ctx, event):
        await asyncio.sleep(0)

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    monkeypatch.setattr(bx, "_emit", _yielding_emit)
    return calls


def _two_stage_fanout(parallel: bool = True) -> Block:
    """The reported shape: per-item Stage A, then Stage B reading it."""
    return Block(
        block_type="repeat", id="fanout", name="Per-gap second look",
        repeat_mode="for_each",
        repeat_for_each_source=str(ITEMS).replace("'", '"'),
        repeat_parallel=parallel, repeat_propagate="none",
        repeat_max=WIDTH,
        body=[
            Block(block_type="task", id="stage-a", name="Stage A",
                  instructions="AUDIT item={{item}}"),
            Block(block_type="task", id="stage-b", name="Stage B",
                  instructions=(
                      "DISPOSE item={{item}} "
                      "prior={{previous_sibling.summary}}"
                  )),
        ],
    )


def _ctx():
    return ExecutionContext(run_id="")


def _stage(calls, name):
    return [c for c in calls if c["name"] == name]


_ITEM_RE = re.compile(r"item=(cap-\d\d)")


def _own_item(instructions: str) -> str:
    """The item THIS task was templated with."""
    m = _ITEM_RE.search(instructions)
    return m.group(1) if m else ""


def _prior_item(instructions: str) -> str:
    """The item named inside the previous_sibling summary it received."""
    after = instructions.split("prior=", 1)
    if len(after) != 2:
        return ""
    m = _ITEM_RE.search(after[1])
    return m.group(1) if m else ""


class TestTheFixtureActuallyInterleaves:
    """Without this, a green suite could mean "never reproduced"."""

    async def test_every_iteration_ran(self, interleaving_tasks):
        await execute_block(_two_stage_fanout(), _ctx())
        assert len(_stage(interleaving_tasks, "Stage A")) == WIDTH
        assert len(_stage(interleaving_tasks, "Stage B")) == WIDTH

    async def test_the_stages_are_not_serialized(self, interleaving_tasks):
        """Every iteration must be in flight before any of them finishes.

        The observable signature of that is lockstep: all WIDTH Stage A
        calls land before the first Stage B, because each iteration
        suspends inside Stage A and the scheduler admits the next one.  If
        the calls came out A,B,A,B,... the iterations would be running to
        completion one at a time and nothing in this file would be
        exercising concurrency at all.
        """
        await execute_block(_two_stage_fanout(), _ctx())
        names = [c["name"] for c in interleaving_tasks]
        assert names[:WIDTH] == ["Stage A"] * WIDTH, (
            f"iterations did not overlap; got {names[:4]}... — the fixture "
            f"no longer reproduces the concurrency this file is about"
        )
        assert names[WIDTH:] == ["Stage B"] * WIDTH


class TestEachIterationSeesItsOwnBindings:
    """``{{item}}`` must be the iteration's own item, always."""

    async def test_stage_a_items_are_the_full_roster_exactly_once(
            self, interleaving_tasks):
        await execute_block(_two_stage_fanout(), _ctx())
        got = sorted(_own_item(c["instructions"])
                     for c in _stage(interleaving_tasks, "Stage A"))
        assert got == sorted(ITEMS), (
            f"Stage A did not see each item exactly once: {got}"
        )

    async def test_stage_b_items_are_the_full_roster_exactly_once(
            self, interleaving_tasks):
        """The second stage is where a shared stack bites hardest.

        By the time Stage B is templated, every sibling iteration has had
        the chance to push its own bindings, so a shared ``[-1]`` read
        yields duplicates and omissions rather than the roster.
        """
        await execute_block(_two_stage_fanout(), _ctx())
        got = sorted(_own_item(c["instructions"])
                     for c in _stage(interleaving_tasks, "Stage B"))
        assert got == sorted(ITEMS), (
            f"Stage B did not see each item exactly once: {got} — an item "
            f"appearing twice means two iterations both believed they were "
            f"processing it, and the missing one was never disposed"
        )


class TestStageBReceivesItsOwnPredecessor:
    """The reported symptom, pinned directly."""

    async def test_stage_b_sees_its_own_stage_a(self, interleaving_tasks):
        await execute_block(_two_stage_fanout(), _ctx())
        mismatched = [
            (_own_item(c["instructions"]), _prior_item(c["instructions"]))
            for c in _stage(interleaving_tasks, "Stage B")
            if _own_item(c["instructions"]) != _prior_item(c["instructions"])
        ]
        assert not mismatched, (
            f"{len(mismatched)} of {WIDTH} Stage B tasks were handed another "
            f"iteration's Stage A result as {{{{previous_sibling}}}}: "
            f"{mismatched[:4]} — each would reason about one capability "
            f"while writing its verdict under another"
        )

    async def test_every_stage_b_got_some_predecessor(
            self, interleaving_tasks):
        """Paired positive: the ``previous_sibling`` path is live.

        Without this, the assertion above would also pass if templating
        silently resolved every ``{{previous_sibling}}`` to nothing.
        """
        await execute_block(_two_stage_fanout(), _ctx())
        empty = [c for c in _stage(interleaving_tasks, "Stage B")
                 if not _prior_item(c["instructions"])]
        assert not empty, (
            f"{len(empty)} Stage B tasks received no previous_sibling at "
            f"all; the isolation must not be implemented by dropping it"
        )


class TestTheViewRebindsMethodsToItself:
    """The delegation hazard the scope tests caught end to end.

    ``_IterationScope`` stores the four tree-position stacks in slots and
    forwards everything else to the parent.  A METHOD forwarded that way
    arrives as the PARENT's bound method, so ``self`` inside it is the
    parent and any method reading a private store reads the shared copy
    rather than the iteration's own.

    Measured before the re-bind: ``effective_scope()`` returned ``None``
    for a view whose own ``scope_stack`` held the leaf's scope.  A ``None``
    scope is not an escalation but a silent stripping — task_executor
    reads ``scope.paths`` / ``scope.tools`` / ``scope.model_tier`` behind
    ``if scope else``, so the leaf would have lost its writable paths, run
    at the tool floor, and dropped a declared ``model_tier: large`` onto
    the default model with nothing recorded.

    Asserted here at the object level as well as end to end, because the
    end-to-end tests only cover the methods those fixtures happen to call;
    a future method reading a private store would slip past them.
    """

    def _view(self):
        parent = ExecutionContext(run_id="r")
        return parent, bx._IterationScope(parent)

    def test_effective_scope_reads_the_views_own_stack(self):
        parent, view = self._view()
        view.scope_stack.append(TaskScope(tools=["private_tool"]))

        got = view.effective_scope()
        assert got is not None, (
            "effective_scope() saw an empty stack though the view's own "
            "scope_stack is populated — the method is still bound to the "
            "parent, so every leaf in a parallel iteration runs with no "
            "scope at all"
        )
        assert "private_tool" in (got.tools or [])

    def test_the_parents_stack_is_untouched(self):
        """Proves the test above is about binding, not about sharing.

        Without this, an implementation that simply aliased the parent's
        list would satisfy the assertion above while reintroducing the
        cross-iteration leak this whole file exists to prevent.
        """
        parent, view = self._view()
        view.scope_stack.append(TaskScope(tools=["private_tool"]))
        assert parent.scope_stack == []

    def test_a_shared_field_still_resolves_to_the_parent(self):
        """The other half: run-scoped state must NOT be copied."""
        parent, view = self._view()
        parent.variables["k"] = "v"
        assert view.variables["k"] == "v"
        assert view.run_id == "r"

    def test_a_method_writing_shared_scalars_still_writes_through(self):
        """Re-binding must not redirect writes onto the discarded view.

        ``infra_gate_closed`` latches ``infra_gated_reason``; if that
        landed on the view it would vanish when the iteration ends and the
        gate would never close for the fan-out.
        """
        parent, view = self._view()
        view.infra_gated_reason = "authentication_error"
        assert parent.infra_gated_reason == "authentication_error"


class TestAncestorScopeDoesNotLeakBetweenIterations:
    """``scope_stack`` is the same shared stack, and it grants permissions.

    ``execute_block`` pushes each block's own scope onto
    ``ctx.scope_stack`` and ``effective_scope()`` merges the whole stack.
    That merge is ADDITIVE by design (a more specific layer may only ever
    add grants), so a concurrent sibling's entry can only ever WIDEN a
    leaf task's permissions -- it cannot narrow them, which is why this is
    a permissions concern and not merely a correctness one.

    The stagger matters: with uniform timing the iterations run in
    lockstep, every Stage A pops before any Stage B is templated, and the
    overlap that exposes the leak never happens.  Odd iterations are held
    open longer so their Stage A is still on the stack while an even
    iteration's Stage B computes its effective scope.
    """

    @pytest.fixture
    def staggered_tasks(self, monkeypatch):
        calls = []

        async def _fake(block, **kwargs):
            item = _own_item(block.instructions or "")
            # Odd items linger, so their frames overlap even items' later
            # stages instead of every iteration advancing in lockstep.
            spins = 5 if (item and int(item[-2:]) % 2) else 1
            for _ in range(spins):
                await asyncio.sleep(0)
            calls.append({
                "name": block.name,
                "item": item,
                "tools": list((block.scope.tools if block.scope else []) or []),
            })
            await asyncio.sleep(0)
            return Artifact(summary=block.instructions or "", created_at=0.0)

        async def _yielding_emit(ctx, event):
            await asyncio.sleep(0)

        monkeypatch.setattr(bx, "execute_task_block", _fake)
        monkeypatch.setattr(bx, "_emit", _yielding_emit)
        return calls

    def _scoped_fanout(self):
        from app.models.task_card import TaskScope
        return Block(
            block_type="repeat", id="fanout", name="Scoped fan-out",
            repeat_mode="for_each",
            repeat_for_each_source=str(ITEMS).replace("'", '"'),
            repeat_parallel=True, repeat_propagate="none",
            repeat_max=WIDTH,
            body=[
                Block(block_type="task", id="stage-a", name="Stage A",
                      instructions="AUDIT item={{item}}",
                      scope=TaskScope(tools=["only_for_stage_a"])),
                Block(block_type="task", id="stage-b", name="Stage B",
                      instructions="DISPOSE item={{item}}",
                      scope=TaskScope(tools=["only_for_stage_b"])),
            ],
        )

    async def test_stage_b_never_receives_stage_as_tool(self, staggered_tasks):
        await execute_block(self._scoped_fanout(), _ctx())
        leaked = [c for c in staggered_tasks
                  if c["name"] == "Stage B"
                  and "only_for_stage_a" in c["tools"]]
        assert not leaked, (
            f"{len(leaked)} Stage B tasks were granted Stage A's tool via a "
            f"concurrent iteration's ancestor scope: {leaked[:3]}"
        )

    async def test_stage_a_never_receives_stage_bs_tool(self, staggered_tasks):
        await execute_block(self._scoped_fanout(), _ctx())
        leaked = [c for c in staggered_tasks
                  if c["name"] == "Stage A"
                  and "only_for_stage_b" in c["tools"]]
        assert not leaked, (
            f"{len(leaked)} Stage A tasks were granted Stage B's tool: "
            f"{leaked[:3]}"
        )

    async def test_each_task_did_get_its_own_tool(self, staggered_tasks):
        """Paired positive: scope resolution ran at all.

        Without this, both assertions above would pass against a build
        that handed every task an empty tool list.
        """
        await execute_block(self._scoped_fanout(), _ctx())
        assert staggered_tasks, "no tasks ran"
        for c in staggered_tasks:
            want = ("only_for_stage_a" if c["name"] == "Stage A"
                    else "only_for_stage_b")
            assert want in c["tools"], (
                f"{c['name']} did not receive its own declared tool: {c}"
            )


class TestSerialLoopsAreUnaffected:
    """A serial loop never interleaved, so its semantics must not move."""

    async def test_serial_stage_b_still_sees_its_own_stage_a(
            self, interleaving_tasks):
        await execute_block(_two_stage_fanout(parallel=False), _ctx())
        for c in _stage(interleaving_tasks, "Stage B"):
            assert _own_item(c["instructions"]) == _prior_item(
                c["instructions"])

    async def test_serial_roster_is_complete(self, interleaving_tasks):
        await execute_block(_two_stage_fanout(parallel=False), _ctx())
        got = sorted(_own_item(c["instructions"])
                     for c in _stage(interleaving_tasks, "Stage B"))
        assert got == sorted(ITEMS)
