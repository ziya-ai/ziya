"""Roster completeness assertion — ``repeat_require_complete``.

Maps to design/task-card-roster-assertion.md §6.  The defect classes it
pins, from a six-phase study where 21 of 22 defects were a missing
question rather than a wrong answer:

* D1/D3/D4/D5 — a finite ``repeat_max`` on an asserted roster is a
  contradiction, refused at BOTH validation and plan time (the seam
  test: the two must agree on what the field means).
* D6/D7/D8 shape — an item planned, no passed iteration produced: the
  loop previously returned success; now it fails NAMING the member.
* D9 — the wide-fan-out warning never fired for an uncapped for_each,
  the shape most needing it.
* Prerequisite — ``item_key`` gives an iteration an identity beyond its
  ordinal position, recorded unconditionally and surviving the 50-pass
  artifact retention cap.
* Regression guard — with the assertion unset, behaviour is unchanged,
  including the silent-hole behaviour the assertion exists to fix.

Several assertions here MUST fail against unpatched code (a suite that
passes against a deleted assertion certifies nothing); the regression
tests must pass both before and after.
"""

import asyncio
import json
import time
from typing import Any, List, Optional

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import IterationSummary
from app.utils.roster_keys import derive_item_key, roster_key_problems
from app.utils.task_card_validation import validate_card_tree
import app.agents.block_executor as be
from app.agents.block_executor import ExecutionContext, _plan_iterations

LOOP_ID = "b-roster"
MEMBERS = ["m-0", "m-1", "m-2", "m-3", "m-4"]


# ── harness ──────────────────────────────────────────────────────────

def _loop(
    items: Any = None,
    *,
    require: bool = True,
    ceiling: Optional[int] = None,
    item_key: Optional[str] = None,
    parallel: bool = False,
    mode: str = "for_each",
    count: Optional[int] = None,
    until: Optional[str] = None,
    concurrency: Optional[int] = 4,
) -> Block:
    kw: dict = dict(
        block_type="repeat", id=LOOP_ID, name="fan-out",
        repeat_mode=mode,
        repeat_parallel=parallel,
        repeat_propagate="none",
        repeat_require_complete=require,
        body=[Block(block_type="task", id="b-a", name="A",
                    instructions="audit {{item}}")],
    )
    if mode == "for_each":
        kw["repeat_for_each_source"] = json.dumps(
            items if items is not None else MEMBERS)
    if count is not None:
        kw["repeat_count"] = count
    if until is not None:
        kw["repeat_until"] = until
    if ceiling is not None:
        kw["repeat_max"] = ceiling
    if item_key is not None:
        kw["repeat_item_key"] = item_key
    if concurrency is not None:
        kw["repeat_max_concurrency"] = concurrency
    return Block(**kw)


def _ctx(**kw) -> ExecutionContext:
    return ExecutionContext(
        run_id="r-req", project_id="p", project_root="/tmp", **kw)


def _errors(block: Block) -> List[str]:
    return [f.message for f in validate_card_tree(block).errors]


def _warnings(block: Block) -> List[str]:
    return [f.message for f in validate_card_tree(block).warnings]


async def _run_loop(block: Block, ctx: ExecutionContext,
                    fail_items: frozenset = frozenset()) -> Artifact:
    """Execute the loop, failing iterations whose rendered item is in
    ``fail_items``.

    The body stub recovers the item from the templated instructions.
    ``rsplit`` rather than ``replace``, because the auto-injected
    iteration-context preamble is PREPENDED to the authored text — a
    leading-anchored strip leaves the whole preamble in ``item``, no
    member ever matches ``fail_items``, and every "the block fails"
    assertion passes vacuously against a loop where nothing failed.
    """
    async def _work(blk, **kwargs):
        item = (blk.instructions or "").rsplit("audit ", 1)[-1].strip()
        if item in fail_items:
            return Artifact(summary=f"broke on {item}", failed=True,
                            created_at=time.time())
        return Artifact(summary=f"ok {item}", created_at=time.time())

    with patch.object(be, "execute_task_block", _work):
        return await be.execute_block(block, ctx)


class _CapturingStorage:
    """Duck-typed stand-in for TaskRunStorage's iteration surface.

    Every method the executor calls on ``ctx.storage`` is declared
    explicitly rather than absorbed by a ``__getattr__`` catch-all: a
    catch-all makes a NEW storage call site silently succeed, which is
    the failure shape that hides a persistence seam from its own test.
    ``get`` returns None, which ``pause_requested`` /
    ``cancel_requested`` already handle (``bool(run and ...)``).
    """

    def __init__(self):
        self.summaries: List[IterationSummary] = []
        self.artifacts: List[tuple] = []
        self.planned: dict = {}
        self.block_status: List[tuple] = []

    def write_iteration_artifact(self, run_id, block_id, index, artifact):
        self.artifacts.append((block_id, index))

    def append_iteration_summary(self, run_id, block_id, summary):
        self.summaries.append(summary)

    def get(self, run_id):
        return None

    def set_block_planned_iterations(self, run_id, block_id, total):
        self.planned[block_id] = total

    def update_block_status(self, run_id, block_id, status, **kw):
        self.block_status.append((block_id, status))

    def set_block_state(self, *a, **kw):
        pass

    def update_status(self, *a, **kw):
        pass

    def consume_step(self, *a, **kw):
        return False

    def record_call(self, *a, **kw):
        pass


# ── the shared key derivation (app/utils/roster_keys.py) ────────────

class TestKeyDerivation:

    def test_scalar_defaults_to_str(self):
        assert derive_item_key("aider") == "aider"
        assert derive_item_key(7) == "7"

    def test_dict_with_path(self):
        assert derive_item_key({"id": "aider", "n": 1}, "id") == "aider"

    def test_nested_path(self):
        assert derive_item_key({"meta": {"slug": "x"}}, "meta.slug") == "x"

    def test_dict_without_path_is_refused_not_guessed(self):
        assert derive_item_key({"id": "aider"}) is None

    def test_unresolvable_path_is_none(self):
        assert derive_item_key({"id": "aider"}, "slug") is None

    def test_path_to_container_is_none(self):
        """A key must name exactly one member — a container value is
        never stringified into an identity."""
        assert derive_item_key({"id": {"x": 1}}, "id") is None

    def test_clean_scalar_roster_has_no_problems(self):
        assert roster_key_problems(MEMBERS) == []

    def test_duplicate_keys_are_a_problem(self):
        probs = roster_key_problems(["a", "b", "a"])
        assert len(probs) == 1 and "duplicate" in probs[0]

    def test_unkeyed_dict_items_are_a_problem(self):
        probs = roster_key_problems([{"id": "a"}, {"id": "b"}])
        assert len(probs) == 1 and "repeat_item_key" in probs[0]

    def test_keyed_dict_roster_is_clean(self):
        assert roster_key_problems([{"id": "a"}, {"id": "b"}], "id") == []


# ── validation refusals (D1/D3/D4/D5 as a class) ────────────────────

class TestValidationRefusals:

    def test_cap_plus_require_complete_is_an_error(self):
        errs = _errors(_loop(ceiling=60))
        assert any("contradiction" in e for e in errs), errs

    def test_uncapped_require_complete_is_clean(self):
        assert _errors(_loop()) == []

    def test_require_complete_on_count_is_an_error(self):
        errs = _errors(_loop(mode="count", count=3))
        assert any("only for_each has a roster" in e for e in errs), errs

    def test_require_complete_on_until_is_an_error(self):
        errs = _errors(_loop(mode="until", until="DONE", ceiling=None))
        assert any("only for_each has a roster" in e for e in errs), errs

    def test_duplicate_keys_in_literal_roster_are_an_error(self):
        errs = _errors(_loop(["a", "b", "a"]))
        assert any("duplicate" in e for e in errs), errs

    def test_dict_items_without_key_path_are_an_error(self):
        errs = _errors(_loop([{"id": "a"}, {"id": "b"}]))
        assert any("repeat_item_key" in e for e in errs), errs

    def test_dict_items_with_key_path_are_clean(self):
        assert _errors(_loop([{"id": "a"}, {"id": "b"}], item_key="id")) == []

    def test_templated_roster_is_not_judged_statically(self):
        """Keys exist only at run time; only the cap contradiction is
        decidable for a templated source."""
        blk = _loop()
        blk.repeat_for_each_source = '{{sibling("plan").outputs.r.ids}}'
        assert _errors(blk) == []

    def test_without_require_complete_none_of_this_fires(self):
        """Regression guard: the hazards are hazards OF the assertion.
        An unasserted roster with duplicates or a cap validates exactly
        as before."""
        assert _errors(_loop(["a", "b", "a"], require=False)) == []
        assert _errors(_loop(ceiling=60, require=False)) == []


# ── D9: the wide-fan-out warning for uncapped for_each ──────────────

class TestWideFanoutWarningD9:

    ROSTER20 = [f"s-{i}" for i in range(20)]

    def test_uncapped_literal_for_each_now_fires(self):
        """The defect: planned was read off repeat_count/repeat_max, so
        an uncapped for_each — the shape most needing the warning —
        never fired it."""
        warns = _warnings(_loop(self.ROSTER20, require=False,
                                parallel=True, concurrency=None))
        assert any("20 parallel iterations" in w for w in warns), warns

    def test_templated_source_warns_roster_sized(self):
        blk = _loop(require=False, parallel=True, concurrency=None)
        blk.repeat_for_each_source = '{{sibling("plan").outputs.r.ids}}'
        warns = _warnings(blk)
        assert any("roster-sized" in w for w in warns), warns

    def test_a_concurrency_cap_silences_it(self):
        warns = _warnings(_loop(self.ROSTER20, require=False,
                                parallel=True, concurrency=4))
        assert not any("parallel iterations" in w or "roster-sized" in w
                       for w in warns), warns

    def test_small_literal_roster_stays_quiet(self):
        warns = _warnings(_loop(require=False, parallel=True,
                                concurrency=None))  # 5 members <= 8
        assert not any("parallel iterations" in w for w in warns), warns


# ── plan-time refusals in the executor (the seam with validation) ───

class TestPlanTimeRefusals:

    def test_cap_plus_require_complete_raises(self):
        exc = getattr(be, "RosterAssertionError")
        with pytest.raises(exc, match="contradict"):
            _plan_iterations(_loop(ceiling=60), _ctx())

    def test_duplicate_keys_raise(self):
        exc = getattr(be, "RosterAssertionError")
        with pytest.raises(exc, match="duplicate"):
            _plan_iterations(_loop(["a", "b", "a"]), _ctx())

    def test_dict_items_without_key_path_raise(self):
        exc = getattr(be, "RosterAssertionError")
        with pytest.raises(exc, match="repeat_item_key"):
            _plan_iterations(_loop([{"id": "a"}, {"id": "b"}]), _ctx())

    def test_unresolvable_source_with_require_complete_raises(self):
        """The count fallback would make the assertion silently vacuous."""
        exc = getattr(be, "RosterAssertionError")
        blk = _loop()
        blk.repeat_for_each_source = "not json at all"
        with pytest.raises(exc, match="no roster"):
            _plan_iterations(blk, _ctx())

    def test_seam_validator_and_planner_agree(self):
        """The two readings of repeat_require_complete must refuse the
        SAME blocks — two suites each passing against their own reading
        of one field is the failure shape this feature was built from."""
        exc = getattr(be, "RosterAssertionError")
        for blk in (_loop(ceiling=60), _loop(["a", "b", "a"]),
                    _loop([{"id": "a"}, {"id": "b"}])):
            assert _errors(blk), f"validator accepted what planning refuses: {blk}"
            with pytest.raises(exc):
                _plan_iterations(blk, _ctx())

    def test_refusal_surfaces_as_failed_block_not_crash(self):
        result = asyncio.run(_run_loop(_loop(ceiling=60), _ctx()))
        assert result.failed
        assert "0 iterations run" in result.summary


# ── the item_key prerequisite ────────────────────────────────────────

class TestItemKeyThreading:

    def test_descriptors_carry_scalar_keys(self):
        iters = _plan_iterations(_loop(require=False), _ctx())
        assert [d["item_key"] for d in iters] == MEMBERS

    def test_descriptors_carry_path_derived_keys(self):
        iters = _plan_iterations(
            _loop([{"id": "a"}, {"id": "b"}], item_key="id"), _ctx())
        assert [d["item_key"] for d in iters] == ["a", "b"]

    def test_keys_are_recorded_without_the_assertion(self):
        """Unconditional recording — the run map and any future
        member-level re-dispatch key on it, assertion or not."""
        iters = _plan_iterations(_loop(require=False), _ctx())
        assert all(d.get("item_key") for d in iters)

    def test_record_iteration_persists_item_key(self):
        storage = _CapturingStorage()
        ctx = _ctx(storage=storage)
        art = Artifact(summary="ok", created_at=time.time())
        asyncio.run(be._record_iteration(
            _loop(require=False), ctx, 0, art, item_key="m-0"))
        assert storage.summaries[0].item_key == "m-0"

    def test_item_key_survives_the_pass_retention_cap(self):
        """append_iteration_summary is unconditional, OUTSIDE the
        keep_full branch — the key must survive iterations whose full
        artifact was dropped by the 50-pass cap (design §3.1, R5)."""
        storage = _CapturingStorage()
        ctx = _ctx(storage=storage)
        ctx.pass_counts[LOOP_ID] = be.PASS_ARTIFACT_RETENTION_CAP + 1
        art = Artifact(summary="ok", created_at=time.time())
        asyncio.run(be._record_iteration(
            _loop(require=False), ctx, 60, art, item_key="m-60"))
        s = storage.summaries[0]
        assert s.item_key == "m-60"
        assert s.has_artifact is False  # proves the cap path was taken

    def test_end_to_end_summaries_carry_keys(self):
        """Seam: planning derives keys, _run_one threads them, recording
        persists them — assert at the outermost surface."""
        storage = _CapturingStorage()
        ctx = _ctx(storage=storage)
        result = asyncio.run(_run_loop(_loop(require=False), ctx))
        assert not result.failed
        assert sorted(s.item_key for s in storage.summaries) == sorted(MEMBERS)

    def test_old_summary_without_item_key_is_unknown_not_crash(self):
        s = IterationSummary(**{"index": 0, "status": "passed"})
        assert s.item_key is None


# ── the exit-time assertion (D6/D7/D8 shape) ─────────────────────────

class TestExitAssertion:

    def test_one_failed_member_fails_the_block_naming_it(self):
        ctx = _ctx()
        result = asyncio.run(_run_loop(
            _loop(), ctx, fail_items=frozenset({"m-2"})))
        assert result.failed
        assert "m-2" in result.summary
        assert "m-0" not in result.summary.split("missing:")[-1]

    def test_shortfall_is_recorded_structured(self):
        ctx = _ctx()
        asyncio.run(_run_loop(_loop(), ctx, fail_items=frozenset({"m-2"})))
        got = ctx.roster_shortfalls.get(LOOP_ID)
        assert got == {"roster": 5, "produced": 4, "missing": ["m-2"]}

    def test_parallel_path_asserts_too(self):
        """CL1/CL2/CL3 were all parallel fan-outs; a serial-only
        assertion would have caught none of the headline cases."""
        ctx = _ctx()
        result = asyncio.run(_run_loop(
            _loop(parallel=True), ctx, fail_items=frozenset({"m-1", "m-3"})))
        assert result.failed
        assert "m-1" in result.summary and "m-3" in result.summary
        assert ctx.roster_shortfalls[LOOP_ID]["missing"] == ["m-1", "m-3"]

    def test_full_coverage_passes_without_decision_noise(self):
        """Negative control: a clean pass must not be annotated —
        otherwise the failure tests above would pass against code that
        annotated every loop."""
        ctx = _ctx()
        result = asyncio.run(_run_loop(_loop(), ctx))
        assert not result.failed
        assert ctx.roster_shortfalls == {}
        assert not any("roster" in d for d in result.decisions)

    def test_unset_preserves_the_silent_hole(self):
        """Regression guard AND the demonstration of the defect: without
        the assertion, a serial for_each under on_failure=continue whose
        middle member fails still returns a PASSING artifact (the final
        artifact reflects the last iteration).  This is today's
        behaviour, byte-for-byte — the assertion is opt-in."""
        ctx = _ctx()
        result = asyncio.run(_run_loop(
            _loop(require=False), ctx, fail_items=frozenset({"m-2"})))
        assert not result.failed
        assert ctx.roster_shortfalls == {}
