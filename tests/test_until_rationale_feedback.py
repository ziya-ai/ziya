"""The Until evaluator's verdict is recorded, explained, and fed back.

Run 7dfb90f6 (card "Chunnel skill pack", KuiperEAR-switch-mgr, 2026-10-10):
an Until with ``until_mode: model``, ``until_max: 3``.  All three
iterations passed; the evaluator said "no" three times; the block failed
with ``failure_reason: "failed"``.  Nothing recorded WHY the evaluator
objected, so the body re-did iteration 1 twice (identical files, 51 s and
47 s) and the loop burned its budget without a chance of converging.

Hops this change crosses, each asserted here:

  evaluator prompt allows a reason   -> parse_verdict carries it
    -> executor emits ``until_evaluated`` {index, satisfied, rationale}
    -> the rationale is a decision on the block
    -> task_run_read.failure_reason_of names it (not "failed")
    -> the NEXT iteration's instructions carry it (auto preamble and
       ``{{until.last_rationale}}``)
    -> a body that was told the reason and changed nothing stops the
       loop early as "condition unreachable"

The last rule is deliberately narrow.  tests/test_until_stall_breaker.py
pins that with an explicit condition ONLY the evaluator decides and a
repeated summary alone must never end the loop; a stub evaluator that
returns a bare bool gives the body no reason, so the rule must not fire
for it.  Both directions are pinned below.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents import block_executor as bx
from app.agents import until_evaluator as ue
from app.agents.block_executor import ExecutionContext, execute_block
from app.agents.until_evaluator import UntilVerdict
from app.models.task_card import Artifact, ArtifactPart, Block
from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.utils.task_run_read import (
    _VERDICT_EVENT_TYPES, failure_reason_of, read_journal_events,
)

EVALUATOR = "app.agents.block_executor._evaluate_until_condition_with_model"
BODY = "app.agents.block_executor.execute_task_block"
REASON = "the test log records one failed entry (fixed:false), so failures are not zero"


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(r.id, "running")
    return storage.get(r.id)


def _until(n_max: int, instructions: str = "run the smoke test") -> Block:
    return Block(
        block_type="until", id="until-1", name="smoke",
        until_mode="model", until_condition="the test log records zero failures",
        until_max=n_max,
        body=[Block(block_type="task", id="inner", name="Run and fix",
                    instructions=instructions)],
    )


def _capturing_body(summary_fn, data_fn=None):
    """Body stub that records the instructions each dispatch received."""
    seen: list = []

    async def stub(b, project_root=None, project_id=None, run_id=None):
        seen.append(b.instructions)
        i = len(seen) - 1
        outputs = []
        if data_fn is not None:
            outputs = [ArtifactPart(part_type="data", data=data_fn(i))]
        return Artifact(summary=summary_fn(i), outputs=outputs, tokens=5)
    return stub, seen


def _always_no(reason=REASON):
    async def ev(condition, artifact):
        return UntilVerdict(False, reason)
    return ev


async def _collect_events(run_id, coro):
    events: list = []

    async def push(rid, ev):
        events.append(ev)
    with patch("app.agents.task_run_stream_relay.safe_push", push):
        result = await coro
    return result, events


# ── evaluator: a reason can be returned and is parsed ───────────────────────

class TestParseVerdict:
    def test_two_line_no_carries_the_reason(self):
        v = ue.parse_verdict("no\nThe log has one failed entry marked fixed:false.")
        assert v == UntilVerdict(False, "The log has one failed entry marked fixed:false.")

    def test_inline_reason_after_the_token(self):
        assert ue.parse_verdict("no - counts show 44 passed, 1 failed").rationale \
            == "counts show 44 passed, 1 failed"
        assert ue.parse_verdict("No: smoke-state.json is not in DATA").rationale \
            == "smoke-state.json is not in DATA"

    def test_bare_token_still_parses(self):
        assert ue.parse_verdict("yes") == UntilVerdict(True, "")
        assert ue.parse_verdict("no") == UntilVerdict(False, "")
        assert ue.parse_verdict("") == UntilVerdict(False, "")

    def test_yes_never_carries_a_rationale(self):
        assert ue.parse_verdict("yes\nall 44 passed").rationale == ""

    def test_long_reason_is_clipped(self):
        v = ue.parse_verdict("no\n" + "x" * 1000)
        assert len(v.rationale) <= ue._RATIONALE_MAX_CHARS


@pytest.mark.asyncio
async def test_evaluate_condition_verdict_returns_the_models_reason():
    art = Artifact(summary="ran smoke", failed=False)
    call = AsyncMock(return_value="no\n" + REASON)
    with patch("app.services.model_resolver.call_service_model", call):
        v = await ue.evaluate_condition_verdict("zero failures", art)
    assert v == UntilVerdict(False, REASON)
    # The reason is only possible if the request leaves room for it; the
    # previous cap of 4 tokens made the two-line format unreturnable.
    assert call.call_args.kwargs["max_tokens"] >= 60
    assert "one sentence" in call.call_args.kwargs["system_prompt"].lower()
    # The bool facade still exists for callers that only want the verdict.
    with patch("app.services.model_resolver.call_service_model",
               AsyncMock(return_value="no\n" + REASON)):
        assert await ue.evaluate_condition("zero failures", art) is False


@pytest.mark.asyncio
async def test_transport_failure_is_an_unmet_verdict_with_no_reason():
    with patch("app.services.model_resolver.call_service_model",
               AsyncMock(side_effect=RuntimeError("down"))):
        assert await ue.evaluate_condition_verdict("c", Artifact(summary="x")) \
            == UntilVerdict(False, "")


def test_coerce_accepts_every_stub_shape():
    assert bx._coerce_until_verdict(True) == UntilVerdict(True, "")
    assert bx._coerce_until_verdict(False) == UntilVerdict(False, "")
    assert bx._coerce_until_verdict((False, "r")) == UntilVerdict(False, "r")
    assert bx._coerce_until_verdict(UntilVerdict(False, "r")) == UntilVerdict(False, "r")


# ── executor: recorded three ways ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_verdict_is_an_event_a_decision_and_the_failure_reason(storage, run):
    # Distinct summaries so the unreachable rule stays out of the way:
    # this test is about recording, exhaustion is reached at until_max.
    body, _ = _capturing_body(lambda i: f"pass {i}: 44/44 plus one env failure")
    ctx = ExecutionContext(run_id=run.id, storage=storage)
    with patch(BODY, body), patch(EVALUATOR, _always_no()):
        result, events = await _collect_events(run.id, execute_block(_until(3), ctx))

    judged = [e for e in events if e.get("type") == "until_evaluated"]
    assert [(e["index"], e["satisfied"]) for e in judged] == [(0, False), (1, False), (2, False)]
    assert all(e["rationale"] == REASON and e["block_id"] == "until-1" for e in judged)

    assert result.failed is True
    per_iter = [d for d in result.decisions if d.startswith("until condition not satisfied at iter")]
    assert len(per_iter) == 3 and all(REASON in d for d in per_iter)
    reason = failure_reason_of(result)
    assert reason != "failed"
    assert reason.startswith("until condition not satisfied after 3 iteration(s)")
    assert REASON in reason


@pytest.mark.asyncio
async def test_a_satisfied_verdict_is_recorded_too(storage, run):
    body, _ = _capturing_body(lambda i: f"pass {i}")
    answers = iter([UntilVerdict(False, "not yet: 1 failure"), UntilVerdict(True, "")])

    async def ev(condition, artifact):
        return next(answers)
    ctx = ExecutionContext(run_id=run.id, storage=storage)
    with patch(BODY, body), patch(EVALUATOR, ev):
        result, events = await _collect_events(run.id, execute_block(_until(5), ctx))
    judged = [(e["index"], e["satisfied"], e["rationale"])
              for e in events if e.get("type") == "until_evaluated"]
    assert judged == [(0, False, "not yet: 1 failure"), (1, True, None)]
    assert result.failed is False and result.signature is None
    assert failure_reason_of(result) is None


def test_until_evaluated_is_a_verdict_event_for_run_readers(tmp_path):
    """task_run_read returns it alongside task_finished / iteration_completed."""
    assert "until_evaluated" in _VERDICT_EVENT_TYPES
    journal = tmp_path / "events.jsonl"
    journal.write_text("\n".join(json.dumps(e) for e in [
        {"type": "task_text_delta", "content": "x"},
        {"type": "until_evaluated", "block_id": "u", "index": 0,
         "satisfied": False, "rationale": REASON},
        {"type": "iteration_completed", "block_id": "u", "index": 0, "status": "passed"},
    ]) + "\n")
    kept, counts = read_journal_events(journal)
    assert [e["type"] for e in kept] == ["until_evaluated", "iteration_completed"]
    assert kept[0]["rationale"] == REASON


# ── executor: fed into the next iteration ───────────────────────────────────

@pytest.mark.asyncio
async def test_next_iteration_is_told_the_evaluators_reason(storage, run):
    body, seen = _capturing_body(lambda i: f"pass {i}")
    ctx = ExecutionContext(run_id=run.id, storage=storage)
    with patch(BODY, body), patch(EVALUATOR, _always_no()):
        await execute_block(_until(2), ctx)
    assert len(seen) == 2
    # Iteration 0 has no verdict to report yet.
    assert REASON not in seen[0]
    assert "did NOT satisfy" not in seen[0]
    # Iteration 1 gets the condition, the reason, and the ask -- without
    # the card using any templating.
    assert "This loop exits when: the test log records zero failures" in seen[1]
    assert f"Evaluator's reason: {REASON}" in seen[1]
    assert "Address that reason" in seen[1]
    assert seen[1].rstrip().endswith("run the smoke test")


@pytest.mark.asyncio
async def test_until_placeholders_render(storage, run):
    body, seen = _capturing_body(lambda i: f"pass {i}")
    ctx = ExecutionContext(run_id=run.id, storage=storage)
    tpl = "Condition: <<{{until.condition}}>> Last objection: <<{{until.last_rationale}}>>"
    with patch(BODY, body), patch(EVALUATOR, _always_no()):
        await execute_block(_until(2, instructions=tpl), ctx)
    assert "Condition: <<the test log records zero failures>> Last objection: <<>>" in seen[0]
    assert f"Last objection: <<{REASON}>>" in seen[1]


@pytest.mark.asyncio
async def test_a_bool_only_evaluator_gives_the_body_nothing_to_act_on(storage, run):
    """No reason -> no preamble line claiming one, and no literal braces."""
    body, seen = _capturing_body(lambda i: f"pass {i}")

    async def bare(condition, artifact):
        return False
    ctx = ExecutionContext(run_id=run.id, storage=storage)
    with patch(BODY, body), patch(EVALUATOR, bare):
        await execute_block(_until(2, instructions="x {{until.last_rationale}} y"), ctx)
    assert "Evaluator's reason" not in seen[1]
    assert "x  y" in seen[1] and "{{" not in seen[1]


# ── executor: unreachable condition stops early ─────────────────────────────

class TestUnreachableCondition:
    @pytest.mark.asyncio
    async def test_told_the_reason_and_unchanged_output_stops_the_loop(self, storage, run):
        body, seen = _capturing_body(
            lambda i: "ran smoke: 44 passed, 1 env failure",
            lambda i: {"passed": 44, "failed": 1},
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch(BODY, body), patch(EVALUATOR, _always_no()):
            result = await execute_block(_until(5), ctx)
        # Iteration 0 was never told anything; iteration 1 was, and
        # changed nothing -- that is the earliest the rule may fire.
        assert len(seen) == 2
        assert result.failed is True
        unreachable = [d for d in result.decisions if d.startswith("condition unreachable")]
        assert unreachable and "output unchanged at iter 1" in unreachable[0]
        assert REASON in failure_reason_of(result)
        assert result.signature == bx.UNTIL_CONDITION_UNMET

    @pytest.mark.asyncio
    async def test_without_a_reason_the_loop_runs_to_until_max(self, storage, run):
        """The contract test_until_stall_breaker pins: a repeated summary
        alone never ends an explicitly-conditioned loop."""
        body, seen = _capturing_body(lambda i: "constant", lambda i: {"k": 1})

        async def bare(condition, artifact):
            return False
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch(BODY, body), patch(EVALUATOR, bare):
            result = await execute_block(_until(4), ctx)
        assert len(seen) == 4
        assert not any(d.startswith("condition unreachable") for d in result.decisions)

    @pytest.mark.asyncio
    async def test_changing_data_under_a_constant_summary_is_progress(self, storage, run):
        body, seen = _capturing_body(lambda i: "ran smoke", lambda i: {"failed": 3 - i})
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch(BODY, body), patch(EVALUATOR, _always_no()):
            result = await execute_block(_until(4), ctx)
        assert len(seen) == 4
        assert not any(d.startswith("condition unreachable") for d in result.decisions)

    @pytest.mark.asyncio
    async def test_a_failed_iteration_is_left_to_the_stall_breaker(self, storage, run):
        calls = [0]

        async def body(b, project_root=None, project_id=None, run_id=None):
            calls[0] += 1
            return Artifact(summary="boom", failed=True, tokens=1)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch(BODY, body), patch(EVALUATOR, _always_no()):
            result = await execute_block(_until(6), ctx)
        assert not any(d.startswith("condition unreachable") for d in result.decisions)
        # Stall breaker still owns this shape: three REPEATED failures,
        # which needs a predecessor, so four passes (see
        # test_until_stall_breaker.test_failing_with_no_new_information).
        assert calls[0] == bx._UNTIL_STALL_LIMIT + 1
        assert any("stall breaker" in d for d in result.decisions)


def test_output_signature_sees_data_and_files_not_just_summary():
    a = Artifact(summary="s", outputs=[ArtifactPart(part_type="data", data={"n": 1})])
    b = Artifact(summary="s", outputs=[ArtifactPart(part_type="data", data={"n": 2})])
    c = Artifact(summary="s", outputs=[ArtifactPart(part_type="file", file_uri="x.json")])
    sigs = {bx._until_output_signature(x) for x in (a, b, c)}
    assert len(sigs) == 3
    assert bx._iteration_signature(a) == bx._iteration_signature(b), "summary-only sig is blind to data"
    assert bx._until_output_signature(a) == bx._until_output_signature(
        Artifact(summary="S", outputs=[ArtifactPart(part_type="data", data={"n": 1})]))
