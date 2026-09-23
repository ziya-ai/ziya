"""
ConsentLedger — the shared mailbox behind task-run Ask blocks and chat
tool consents (design/consent-runtime.md, step 2).

Three layers:

1. Store contract, parametrized over BOTH stores.  The executor relies on
   first-answer-wins and answer-survives-close identically whichever store
   is behind the ledger, so a divergence between the two is a bug here.
2. Store-specific seams.  The task-run store must leave the run record in
   the exact shape the reconciler / answer endpoint / tile read; the file
   store must answer ``open_for`` after a fresh instance (the reconnect
   case) and prune only what is finished.
3. The ledger's wait loop: settles on answer, cancels, expires as a reject.
"""

import asyncio
import json

import pytest

from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.utils.consent_ledger import (
    TIMED_OUT_REASON,
    ConsentCancelled,
    ConsentLedger,
    FileConsentStore,
    TaskRunConsentStore,
    conversation_request_id,
    make_answer,
    run_request_id,
)


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def run_storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(run_storage):
    return run_storage.create(TaskRunCreate(card_id="card-1"))


@pytest.fixture(params=["run", "file"])
def ledger_and_id(request, tmp_path, run_storage, run):
    """(ledger, request_id, owner_id) for each store kind."""
    if request.param == "run":
        return (ConsentLedger(TaskRunConsentStore(run_storage), poll_seconds=0.01),
                run_request_id(run.id, "b1"), run.id)
    return (ConsentLedger(FileConsentStore(tmp_path), poll_seconds=0.01),
            conversation_request_id("conv-A", "call-1"), "conv-A")


QUESTION = {"kind": "question", "text": "Ship it?", "choices": ["ship", "hold"]}


# ── 1. store contract (both stores) ───────────────────────────────────────

def test_open_then_get_round_trips_and_is_listed_as_open(ledger_and_id):
    ledger, rid, owner = ledger_and_id
    rec = ledger.open(rid, QUESTION, owner_id=owner)
    assert rec.request_id == rid and rec.owner_id == owner
    got = ledger.get(rid)
    assert got is not None and not got.settled and not got.closed
    assert got.payload["text"] == "Ship it?" and got.payload["choices"] == ["ship", "hold"]
    assert [r.request_id for r in ledger.open_for(owner)] == [rid]


def test_first_answer_wins(ledger_and_id):
    ledger, rid, owner = ledger_and_id
    ledger.open(rid, QUESTION, owner_id=owner)
    first = ledger.record_answer(rid, "approve", scope="conversation", answered_by="ann")
    second = ledger.record_answer(rid, "reject", answer="no!", answered_by="bob")
    assert first.answer["decision"] == "approve"
    assert second.answer["decision"] == "approve", "second answer must not override"
    assert second.answer["answered_by"] == "ann"
    assert second.answer["scope"] == "conversation"


def test_answer_survives_close_and_settled_is_not_listed_as_open(ledger_and_id):
    ledger, rid, owner = ledger_and_id
    ledger.open(rid, QUESTION, owner_id=owner)
    ledger.record_answer(rid, "reject", answer="not today")
    closed = ledger.close(rid)
    assert closed.closed is True
    assert closed.answer["decision"] == "reject" and closed.answer["answer"] == "not today"
    assert ledger.open_for(owner) == []
    # a resume that re-reads finds it settled without re-asking
    assert ledger.get(rid).settled


def test_unknown_request_is_none(ledger_and_id):
    ledger, rid, owner = ledger_and_id
    assert ledger.get(rid) is None
    assert ledger.record_answer(rid, "approve") is None
    assert ledger.open_for(owner) == []


def test_reject_forces_scope_once():
    a = make_answer("reject", scope="always")
    assert a["scope"] == "once", "a rejection is never sticky"
    with pytest.raises(ValueError):
        make_answer("maybe")
    with pytest.raises(ValueError):
        make_answer("approve", scope="forever")


# ── 2a. task-run store: the run record keeps its pre-ledger shape ─────────

def test_run_store_projects_onto_pending_ask_and_ask_answers(run_storage, run):
    ledger = ConsentLedger(TaskRunConsentStore(run_storage))
    rid = run_request_id(run.id, "b1")
    ledger.open(rid, QUESTION, owner_id=run.id)

    reread = run_storage.get(run.id)
    assert reread.status == "awaiting_input"
    assert reread.pending_ask["block_id"] == "b1"
    assert reread.pending_ask["question"] == "Ship it?"
    assert reread.pending_ask["choices"] == ["ship", "hold"]
    assert "opened_at" in reread.pending_ask

    ledger.record_answer(rid, "approve", scope="session", answer="go", answered_by="ann")
    reread = run_storage.get(run.id)
    entry = reread.ask_answers["b1"]
    assert entry["decision"] == "approve" and entry["answer"] == "go"
    assert entry["answered_by"] == "ann" and "answered_at" in entry
    assert entry["scope"] == "session"

    ledger.close(rid)
    reread = run_storage.get(run.id)
    assert reread.pending_ask is None and reread.status == "running"
    assert reread.ask_answers["b1"]["decision"] == "approve"


def test_run_store_does_not_stamp_scope_onto_a_settled_answer(run_storage, run):
    """Legacy path (endpoint) answers first; ledger's later answer must not
    add its scope to an entry it did not create."""
    run_storage.open_ask(run.id, "b1", "Q?", [])
    run_storage.record_ask_answer(run.id, "b1", "reject", "nope", "endpoint")
    ledger = ConsentLedger(TaskRunConsentStore(run_storage))
    rec = ledger.record_answer(run_request_id(run.id, "b1"), "approve", scope="always")
    assert rec.answer["decision"] == "reject"
    assert "scope" not in run_storage.get(run.id).ask_answers["b1"]


def test_run_store_get_after_close_reports_closed_with_answer(run_storage, run):
    ledger = ConsentLedger(TaskRunConsentStore(run_storage))
    rid = run_request_id(run.id, "b1")
    ledger.open(rid, QUESTION, owner_id=run.id)
    ledger.record_answer(rid, "approve")
    ledger.close(rid)
    rec = ledger.get(rid)
    assert rec.closed and rec.settled


def test_run_store_missing_run_raises_on_open(run_storage):
    ledger = ConsentLedger(TaskRunConsentStore(run_storage))
    with pytest.raises(KeyError):
        ledger.open(run_request_id("nope", "b1"), QUESTION, owner_id="nope")


# ── 2b. file store: reconnect and prune ───────────────────────────────────

def test_file_store_open_for_survives_a_fresh_instance(tmp_path):
    a = ConsentLedger(FileConsentStore(tmp_path))
    rid = conversation_request_id("conv-A", "call-9")
    a.open(rid, {"kind": "tool_call", "tool": "git", "op": "commit",
                 "args": {"message": "x"}, "preflight": {"staged": ["a.py"]}},
           owner_id="conv-A")
    a.open(conversation_request_id("conv-B", "call-1"), QUESTION, owner_id="conv-B")

    b = ConsentLedger(FileConsentStore(tmp_path))
    open_a = b.open_for("conv-A")
    assert [r.request_id for r in open_a] == [rid]
    assert open_a[0].kind == "tool_call"
    assert open_a[0].payload["preflight"] == {"staged": ["a.py"]}
    assert open_a[0].public()["payload"]["op"] == "commit"


def test_file_store_reopen_is_idempotent(tmp_path):
    ledger = ConsentLedger(FileConsentStore(tmp_path))
    rid = conversation_request_id("conv-A", "call-1")
    ledger.open(rid, QUESTION, owner_id="conv-A")
    ledger.record_answer(rid, "approve")
    again = ledger.open(rid, {"kind": "question", "text": "different"}, owner_id="conv-A")
    assert again.settled and again.payload["text"] == "Ship it?", \
        "re-open on the resume path must not discard the recorded answer"


def test_file_store_prunes_only_closed_settled_old_records(tmp_path):
    store = FileConsentStore(tmp_path, retain_ms=1000)
    ledger = ConsentLedger(store)
    old_done = conversation_request_id("c", "done")
    old_open = conversation_request_id("c", "open")
    ledger.open(old_done, QUESTION, owner_id="c")
    ledger.record_answer(old_done, "approve")
    ledger.close(old_done)
    ledger.open(old_open, QUESTION, owner_id="c")
    # age both on disk
    for rid in (old_done, old_open):
        p = store._path(rid)
        d = json.loads(p.read_text()); d["opened_at"] -= 10_000
        p.write_text(json.dumps(d))
    ledger.open(conversation_request_id("c", "new"), QUESTION, owner_id="c")  # triggers prune
    assert ledger.get(old_done) is None, "finished + old → pruned"
    assert ledger.get(old_open) is not None, "still open → kept regardless of age"


# ── 3. the wait loop ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_await_answer_returns_when_answer_lands(ledger_and_id):
    ledger, rid, owner = ledger_and_id
    ledger.open(rid, QUESTION, owner_id=owner)

    async def answer_later():
        await asyncio.sleep(0.03)
        ledger.record_answer(rid, "approve", scope="conversation", answered_by="ann")

    asyncio.create_task(answer_later())
    got = await asyncio.wait_for(ledger.await_answer(rid), timeout=2)
    assert got["decision"] == "approve" and got["scope"] == "conversation"


@pytest.mark.asyncio
async def test_await_answer_honours_cancel(ledger_and_id):
    ledger, rid, owner = ledger_and_id
    ledger.open(rid, QUESTION, owner_id=owner)
    flag = {"cancel": False}

    async def cancel_later():
        await asyncio.sleep(0.03)
        flag["cancel"] = True

    asyncio.create_task(cancel_later())
    with pytest.raises(ConsentCancelled):
        await asyncio.wait_for(ledger.await_answer(rid, lambda: flag["cancel"]), timeout=2)
    assert not ledger.get(rid).settled, "cancel must not fabricate an answer"


@pytest.mark.asyncio
async def test_await_answer_expiry_is_a_recorded_reject(tmp_path):
    ledger = ConsentLedger(FileConsentStore(tmp_path), poll_seconds=0.01)
    rid = conversation_request_id("conv-A", "call-1")
    ledger.open(rid, QUESTION, owner_id="conv-A", ttl_ms=20)
    got = await asyncio.wait_for(ledger.await_answer(rid), timeout=2)
    assert got["decision"] == "reject" and got["answer"] == TIMED_OUT_REASON
    assert ledger.get(rid).answer["answered_by"] == "system"


@pytest.mark.asyncio
async def test_await_answer_on_vanished_request_raises(tmp_path):
    ledger = ConsentLedger(FileConsentStore(tmp_path), poll_seconds=0.01)
    with pytest.raises(KeyError):
        await ledger.await_answer(conversation_request_id("x", "y"))
