"""Seam test: ``block_executor._execute_ask`` is an adapter over
``ConsentLedger`` (design/consent-runtime.md step 2).

The storage-level Ask tests (test_task_card_ask_gate.py) pin the mailbox;
these pin the adapter's observable behaviour through the real executor
function: it opens on the run record, blocks until an answer lands, applies
the answer (variable, context note, artifact), re-applies a settled answer
without re-asking, and maps a cancel request to BlockExecutionCancelled.
"""

import asyncio

import pytest

from app.agents import block_executor as be
from app.models.task_card import Block
from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.utils import consent_ledger as cl


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    return storage.create(TaskRunCreate(card_id="card-1"))


@pytest.fixture
def ctx(storage, run):
    return be.ExecutionContext(
        run_id=run.id, project_id="p", project_root="/tmp", storage=storage,
    )


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    # The executor constructs the ledger with the module default; shrink it
    # so these tests wait milliseconds rather than half-seconds.
    monkeypatch.setattr(cl, "DEFAULT_POLL_SECONDS", 0.01)


def _ask(**kw) -> Block:
    return Block(block_type="ask", id="b1", name="Ship it?",
                 ask_question="Ship it?", ask_choices=["ship", "hold"], **kw)


@pytest.mark.asyncio
async def test_ask_opens_on_the_run_record_and_holds_until_answered(storage, run, ctx):
    task = asyncio.create_task(be._execute_ask(_ask(ask_variable="verdict"), ctx))
    # Let it open.
    for _ in range(100):
        await asyncio.sleep(0.01)
        if storage.get(run.id).status == "awaiting_input":
            break
    reread = storage.get(run.id)
    assert reread.status == "awaiting_input", "adapter must flip the run to awaiting_input"
    assert reread.pending_ask["block_id"] == "b1"
    assert reread.pending_ask["question"] == "Ship it?"
    assert not task.done(), "must hold until a human answers"

    # Answer through the same path the HTTP endpoint uses.
    storage.record_ask_answer(run.id, "b1", "approve", "go", "alice")
    artifact = await asyncio.wait_for(task, 5)

    assert not artifact.failed
    assert "approved by alice" in artifact.summary
    assert ctx.variables["verdict"] == "go"
    assert "alice" in ctx.context_notes["b1"]
    after = storage.get(run.id)
    assert after.pending_ask is None, "close must clear the open question"
    assert after.status == "running"


@pytest.mark.asyncio
async def test_a_rejection_is_a_failed_artifact(storage, run, ctx):
    task = asyncio.create_task(be._execute_ask(_ask(), ctx))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if storage.get(run.id).pending_ask:
            break
    storage.record_ask_answer(run.id, "b1", "reject", "not yet", "bob")
    artifact = await asyncio.wait_for(task, 5)
    assert artifact.failed
    assert "rejected by bob" in artifact.summary


@pytest.mark.asyncio
async def test_a_settled_answer_is_applied_without_asking_again(storage, run, ctx):
    # Simulates resume / answer-then-resume: answer already on record.
    storage.record_ask_answer(run.id, "b1", "approve", "yes", "carol")
    artifact = await asyncio.wait_for(be._execute_ask(_ask(ask_variable="v"), ctx), 2)
    assert not artifact.failed
    assert ctx.variables["v"] == "yes"
    assert storage.get(run.id).pending_ask is None, "must not have re-opened the question"
    assert storage.get(run.id).status != "awaiting_input"


@pytest.mark.asyncio
async def test_cancel_while_waiting_raises_block_execution_cancelled(storage, run, ctx):
    task = asyncio.create_task(be._execute_ask(_ask(), ctx))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if storage.get(run.id).pending_ask:
            break
    storage.request_cancel(run.id)
    with pytest.raises(be.BlockExecutionCancelled):
        await asyncio.wait_for(task, 5)
    after = storage.get(run.id)
    assert after.pending_ask is None, "close must run on the cancel path too"


@pytest.mark.asyncio
async def test_no_storage_is_refused_rather_than_hanging(run):
    ctx = be.ExecutionContext(run_id=run.id, project_id="p", project_root="/tmp")
    with pytest.raises(be.TaskExecutorError):
        await be._execute_ask(_ask(), ctx)
