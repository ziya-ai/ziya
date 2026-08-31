"""The human-in-the-loop Ask block: validation, idempotency, restart survival.

An Ask holds a run at a block boundary with status ``awaiting_input`` until a
human answers, then binds the answer into the run the way a State block binds
its literals.

The design rests on one decision, and most of these tests exist to pin it: the
ANSWER lives on the run record, keyed by block id, not in the executor frame.
That is what makes an Ask idempotent in the two places it has to be —

  * a resume walk re-executes an Ask rather than replaying its artifact, so a
    settled answer is re-applied and the operator is not asked twice;
  * a server restart reconciles a waiting run to ``held`` rather than
    ``failed``, so it can be answered and then resumed.

The second is the reason a gate cannot simply be a flag on pause.  A manual
pause lasts seconds; an ask lasts as long as a human takes.
``reconcile_stale_runs`` maps ``paused`` to ``failed`` on the (correct)
premise that a paused run's executor died with the server — applied to an ask,
that discards a run which had done all of its work and was one answer from
finishing.  For a release sweep, it means the commits are made and the tag is
not.

Tests that need the ``ask`` block type to exist in ``Block``'s Literal are
marked with a seam assertion rather than working around it with
``model_construct``: a validator that accepts a type the executor refuses is
the exact failure the local ``KNOWN_BLOCK_TYPES`` set exists to prevent, so
the union and the set have to be checked against each other directly.
"""

import time
from typing import get_args

import pytest

from app.models.task_card import Block
from app.models.task_run import BlockStatus, RunStatus, TaskRun, TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.utils.task_card_validation import KNOWN_BLOCK_TYPES, validate_card_tree


# ── helpers ────────────────────────────────────────────────────────────────

def ask(**kw) -> Block:
    """An ask block with the required field filled unless overridden."""
    kw.setdefault("block_type", "ask")
    kw.setdefault("ask_question", "Proceed?")
    return Block.model_validate(kw)


def errors_for(root: Block) -> list:
    return [f.message for f in validate_card_tree(root).errors]


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage) -> TaskRun:
    return storage.create(TaskRunCreate(card_id="card-1"))


# ── 1. the seam: validator and executor must agree the type exists ────────

def test_ask_is_a_real_block_type_in_both_the_model_and_the_validator():
    """A validator that accepts a type the executor refuses is worse than one
    that refuses a type the executor accepts: the first launches a card that
    dies mid-run, the second fails before anything happens.
    """
    literal = set(get_args(Block.model_fields["block_type"].annotation))
    assert "ask" in literal, "Block.block_type does not admit 'ask'"
    assert "ask" in KNOWN_BLOCK_TYPES, "the validator does not know 'ask'"
    assert literal == KNOWN_BLOCK_TYPES, (
        f"the model's block types and the validator's set have drifted: "
        f"model-only={sorted(literal - KNOWN_BLOCK_TYPES)}, "
        f"validator-only={sorted(KNOWN_BLOCK_TYPES - literal)}"
    )


def test_awaiting_input_is_a_run_status_and_a_block_status():
    assert "awaiting_input" in get_args(RunStatus)
    assert "awaiting_input" in get_args(BlockStatus), (
        "the run can be awaiting_input but the block cannot, so the run map "
        "has no way to show WHERE the question is"
    )


# ── 2. validation ─────────────────────────────────────────────────────────

def test_an_ask_with_no_question_is_refused():
    problems = errors_for(ask(ask_question=""))
    assert any("ask_question" in p for p in problems), problems


def test_a_well_formed_ask_produces_no_findings():
    """Positive control for every refusal below."""
    result = validate_card_tree(
        ask(name="Ship it?", ask_variable="go_ahead",
            ask_choices=["ship", "hold"])
    )
    assert result.ok, result.summary()
    assert result.warnings == [], result.summary()


def test_an_ask_with_a_body_is_refused():
    """Ask is a leaf.  A body would silently never run."""
    problems = errors_for(ask(body=[Block(block_type="task",
                                          instructions="x")]))
    assert any("leaf" in p for p in problems), problems


@pytest.mark.parametrize("name", ["has space", "1leading", "with-dash", "a.b"])
def test_an_unreachable_ask_variable_is_refused(name):
    """Decidable now, so it is refused now: an answer bound to a name no
    {{var.NAME}} can reference is an answer the card cannot read.
    """
    problems = errors_for(ask(ask_variable=name))
    assert any("ask_variable" in p for p in problems), problems


@pytest.mark.parametrize("name", ["go", "go_ahead", "_x", "A1"])
def test_a_usable_ask_variable_is_accepted(name):
    assert validate_card_tree(ask(ask_variable=name)).ok


def test_duplicate_and_blank_choices_are_refused():
    assert any("repeats" in p for p in errors_for(
        ask(ask_choices=["yes", "yes"])))
    assert any("blank" in p for p in errors_for(
        ask(ask_choices=["yes", "  "])))
    assert any("empty" in p for p in errors_for(ask(ask_choices=[])))


def test_an_ask_inside_a_parallel_container_is_refused():
    """A run records ONE open question.  Concurrent asks would overwrite each
    other and the run would proceed on whichever answer landed last.
    """
    root = Block(block_type="parallel", body=[ask(), ask()])
    problems = errors_for(root)
    assert any("parallel" in p for p in problems), problems


def test_an_ask_inside_a_loop_body_is_refused():
    """Answers are keyed per block, so iteration 2 would silently reuse
    iteration 1's answer.  Refused rather than warned: reusing a stale
    approval across 378 iterations is precisely the silent-scope-loss shape
    this codebase keeps closing.
    """
    root = Block(block_type="repeat", repeat_mode="count", repeat_count=3,
                 body=[ask()])
    problems = errors_for(root)
    assert any("loop" in p for p in problems), problems


def test_an_ask_inside_a_parallel_repeat_is_refused_on_both_counts():
    root = Block(block_type="repeat", repeat_mode="count", repeat_count=3,
                 repeat_parallel=True, body=[ask()])
    problems = errors_for(root)
    assert any("parallel" in p for p in problems), problems
    assert any("loop" in p for p in problems), problems


def test_an_ask_above_a_loop_is_accepted():
    """Positive control for the two enclosure refusals.

    Without it, a validator that refused every ask outright would satisfy
    them both.  This is also the shape the refusals point authors toward.
    """
    root = Block(block_type="group", body=[
        ask(name="Approve the batch?"),
        Block(block_type="repeat", repeat_mode="count", repeat_count=3,
              body=[Block(block_type="task", instructions="work")]),
    ])
    result = validate_card_tree(root)
    assert result.ok, result.summary()


def test_a_sibling_ask_after_a_loop_is_not_treated_as_inside_it():
    """The enclosure tags must not leak across siblings.

    A tuple accumulated by mutation rather than by rebinding would mark
    everything after the first loop as loop-enclosed, refusing valid cards.
    """
    root = Block(block_type="group", body=[
        Block(block_type="repeat", repeat_mode="count", repeat_count=2,
              body=[Block(block_type="task", instructions="work")]),
        ask(name="Now may I push?"),
    ])
    assert validate_card_tree(root).ok, validate_card_tree(root).summary()


# ── 3. the answer record ──────────────────────────────────────────────────

def test_opening_an_ask_flips_status_and_records_the_question(storage, run):
    storage.open_ask(run.id, "b1", "Ship it?", ["ship", "hold"])
    reread = storage.get(run.id)
    assert reread.status == "awaiting_input"
    assert reread.pending_ask["block_id"] == "b1"
    assert reread.pending_ask["question"] == "Ship it?"
    assert reread.pending_ask["choices"] == ["ship", "hold"]


def test_closing_an_ask_clears_the_question_and_resumes(storage, run):
    storage.open_ask(run.id, "b1", "Ship it?")
    storage.close_ask(run.id)
    reread = storage.get(run.id)
    assert reread.pending_ask is None
    assert reread.status == "running"


def test_closing_an_ask_does_not_revive_a_cancelled_run(storage, run):
    """close_ask runs in a finally, including on the cancel path.

    Walking the status back to running there would report a cancelled run as
    live on its way out — the same "asserts progress that is not happening"
    failure the animation map is careful about.
    """
    storage.open_ask(run.id, "b1", "Ship it?")
    storage.update_status(run.id, "cancelled")
    storage.close_ask(run.id)
    assert storage.get(run.id).status == "cancelled"
    assert storage.get(run.id).pending_ask is None


def test_the_first_answer_wins(storage, run):
    """A second answer must not be able to change what the run was told.

    Either outcome of allowing it is bad: silently discarded is confusing,
    and silently overriding a decision already acted upon is worse.
    """
    storage.open_ask(run.id, "b1", "Ship it?")
    storage.record_ask_answer(run.id, "b1", "approve", "go", "dcohn")
    storage.record_ask_answer(run.id, "b1", "reject", "changed my mind", "x")
    recorded = storage.get(run.id).ask_answers["b1"]
    assert recorded["decision"] == "approve"
    assert recorded["answer"] == "go"
    assert recorded["answered_by"] == "dcohn"


def test_answers_for_different_blocks_coexist(storage, run):
    """Positive control for first-answer-wins: it must key on the block, not
    latch globally after any answer at all.
    """
    storage.record_ask_answer(run.id, "b1", "approve", "one")
    storage.record_ask_answer(run.id, "b2", "reject", "two")
    answers = storage.get(run.id).ask_answers
    assert answers["b1"]["answer"] == "one"
    assert answers["b2"]["decision"] == "reject"


def test_an_answer_survives_a_fresh_storage_instance(storage, run, tmp_path):
    """The answer has to be on DISK, not in the instance.

    The restart story below depends entirely on this, and an in-memory dict
    would pass every other test in this file.
    """
    storage.record_ask_answer(run.id, "b1", "approve", "go")
    assert TaskRunStorage(tmp_path).get(run.id).ask_answers["b1"]["answer"] == "go"


# ── 4. restart survival ───────────────────────────────────────────────────

def test_a_waiting_run_reconciles_to_held_not_failed(storage, run):
    """The load-bearing test of the whole design.

    A run waiting on a human is not a crashed run, and an ask is expected to
    outlive a server lifetime.  Marking it failed discards work that was one
    answer from finishing.
    """
    storage.open_ask(run.id, "b-tag", "Tag and push v0.8.7.0?")
    assert storage.reconcile_stale_runs() == 1

    reread = storage.get(run.id)
    assert reread.status == "held", (
        "a run waiting on a human was reported as failed after a restart"
    )
    assert reread.held_at_block_id == "b-tag", (
        "the hold does not name the block, so resume-from-block has nowhere "
        "to go and the banner cannot say where it stopped"
    )
    assert reread.held_reason == "awaiting_human_input"
    assert reread.pending_ask is not None, (
        "the question was discarded, so the operator has no way to know what "
        "they were being asked"
    )


def test_a_paused_run_still_reconciles_to_failed(storage):
    """Negative control.

    Without this, a change that simply stopped reconciling anything would
    satisfy the test above.  Pause keeps its old behaviour deliberately: a
    paused run's executor really did die with the server.
    """
    run = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(run.id, "paused")
    assert storage.reconcile_stale_runs() == 1
    assert storage.get(run.id).status == "failed"


def test_reconcile_is_idempotent_for_a_waiting_run(storage, run):
    """A second startup must not re-reconcile, or a held run would be
    rewritten on every restart and its completed_at would keep moving.
    """
    storage.open_ask(run.id, "b1", "Proceed?")
    assert storage.reconcile_stale_runs() == 1
    assert storage.reconcile_stale_runs() == 0
    assert storage.get(run.id).status == "held"


def test_answer_then_resume_finds_the_ask_already_settled(storage, run):
    """The sequence the restart path exists to support.

    After reconciliation the run is held with its question intact; the
    operator answers, and the answer is on record BEFORE the resumed run
    replays to the Ask block.  That is what makes the replay silent instead
    of a second interruption.
    """
    storage.open_ask(run.id, "b-tag", "Tag and push?")
    storage.reconcile_stale_runs()
    held = storage.get(run.id)
    assert held.status == "held"

    storage.record_ask_answer(run.id, held.pending_ask["block_id"],
                              "approve", "yes, ship it", "dcohn")

    settled = storage.get(run.id).ask_answers["b-tag"]
    assert settled["decision"] == "approve"
    assert settled["answer"] == "yes, ship it"


# ── 5. what the status must NOT be confused with ──────────────────────────

def test_awaiting_input_is_not_in_the_frontend_terminal_lists():
    """A waiting run is LIVE: its executor frame is alive in a sleep loop.

    The hook's two lists answer different questions — TERMINAL is "is this
    the run's final word", EXECUTOR_STOPPED is "can anything still be
    producing output" — and awaiting_input must be absent from both, or the
    socket is torn down and the answer never reaches the frame.  Read out of
    the real source rather than reimplemented, because a copy of the list in
    a test agrees with itself and certifies nothing about the file that ships.
    """
    from pathlib import Path
    src = Path("frontend/src/hooks/useTaskRunStream.ts").read_text()

    for const in ("TERMINAL", "EXECUTOR_STOPPED"):
        marker = f"const {const}: ReadonlyArray<TaskRun['status']> ="
        assert marker in src, f"{const} is no longer declared as expected"
        body = src.split(marker, 1)[1].split(";", 1)[0]
        assert "awaiting_input" not in body, (
            f"awaiting_input joined {const}, which tears down the transport "
            f"for a run that is still alive and waiting for an answer"
        )
    # Positive control: the parse really did find non-empty lists.
    assert "'held'" in src.split("EXECUTOR_STOPPED", 1)[1][:400], (
        "the EXECUTOR_STOPPED parse found nothing recognisable, so the "
        "absence assertion above proves nothing"
    )
