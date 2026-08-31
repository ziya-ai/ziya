"""The ``repeat_max``-on-for_each scope-loss class, at all three layers.

Background — the incident this file pins.  A CL5 run's Stage 2 fan-out
carried ``repeat_max=60`` against a roster its own Stage 1 resolved to
108 items.  48 head-to-head comparisons never ran.  The clipping was
reported (see test_repeat_roster_truncation_visibility.py, which pins
that reporting), but three separate things made it happen and made it
unrecoverable afterwards:

  1. AUTHORING BLINDNESS.  ``RepeatBlockEditor`` renders the
     ``repeat_max`` input only in ``until`` mode.  In ``for_each`` mode
     the field is invisible and uneditable, and switching modes does not
     clear it — so a value set in one mode silently governs the other.
     Nothing at launch said "this cap will clip your roster", because
     the only cap-adjacent warning is about CONCURRENCY and its wording
     ("60 parallel iterations ... will run 8 at a time") actively
     reassures the author that all 60 run.

  2. UNRECOVERABLE LOSS.  ``_plan_iterations`` did ``items[:cap]`` and
     recorded only COUNTS ({"roster": N, "dispatched": M}).  The
     identities of the dropped items were discarded, so a follow-up pass
     over just the missed 48 was impossible; the only remedy was
     re-running all 108.

  3. NO CAP-SHAPED WARNING for the one case where a cap cannot be
     authored correctly: a TEMPLATED for_each source, whose roster size
     is unknowable when the number is typed.

Each layer is asserted independently, because fixing any one alone still
loses scope.  Truncation behaviour itself is deliberately NOT changed —
``repeat_max`` is a cost ceiling and silently exceeding it would be
worse.  What changes is that the cap is visible before the spend and the
loss is recoverable after it.
"""

import json

import pytest

from app.models.task_card import Block
from app.utils.task_card_validation import validate_card_tree

pytestmark = pytest.mark.usefixtures()

ROSTER = 108
CEILING = 60
LOOP_ID = "b-depth-fanout"

TEMPLATED_SOURCE = "{{previous_sibling.outputs.contested_index.ids}}"


def _literal_roster(n: int) -> str:
    return json.dumps([f"cap-{i:03d}" for i in range(n)])


def _loop(
    *,
    source: str,
    ceiling: int | None,
    mode: str = "for_each",
    parallel: bool = True,
) -> Block:
    return Block(
        block_type="repeat", id=LOOP_ID, name="Stage 2: head-to-head",
        repeat_mode=mode,
        repeat_for_each_source=source,
        repeat_max=ceiling,
        repeat_parallel=parallel,
        repeat_propagate="none",
        body=[Block(block_type="task", id="b-h2h", name="h2h",
                    instructions="quantify {{item}}")],
    )


def _messages(result) -> list[str]:
    return [f.message for f in result.warnings] + [
        f.message for f in result.errors
    ]


def _cap_warnings(result) -> list[str]:
    """Warnings that assert SCOPE LOSS, as opposed to any other warning.

    Anchored on the claim being made rather than on list position or on a
    keyword that happens to appear.  Two nearby warnings have to be told
    apart and neither can be excluded by a crude keyword filter:

      * the pre-existing wide-fanout warning ("60 parallel iterations
        with no repeat_max_concurrency - they will run 8 at a time")
        contains the substring "repeat_max" and a matching number, so
        matching on either alone lets it satisfy these tests; but it
        claims nothing about items going unrun.
      * the cap warning legitimately RECOMMENDS repeat_max_concurrency as
        the alternative cost bound, so filtering on "concurrency" -- the
        first thing tried here -- discarded the very message under test.

    So the discriminator is the conjunction that only a scope-loss
    warning satisfies: it names ``repeat_max`` AND claims items are
    clipped or never run.  The count-mode warning ("repeat_count is 0 -
    the body will never run") says "never run" but not "repeat_max", and
    is correctly excluded.
    """
    out = []
    for msg in _messages(result):
        low = msg.lower()
        if "repeat_max" in low and ("never run" in low or "clip" in low):
            out.append(msg)
    return out


class TestValidationWarnsOnCappedForEach:
    """Layer 3: the cap must be called out BEFORE the run spends anything."""

    def test_templated_source_with_finite_cap_warns(self):
        res = validate_card_tree(_loop(source=TEMPLATED_SOURCE, ceiling=CEILING))
        found = _cap_warnings(res)
        assert found, (
            "a finite repeat_max on a TEMPLATED for_each source is an "
            "unknowable-at-authoring-time cap and must warn; got: "
            f"{_messages(res)}"
        )

    def test_warning_is_not_an_error(self):
        """A cap is legitimate — it must not block a launch."""
        res = validate_card_tree(_loop(source=TEMPLATED_SOURCE, ceiling=CEILING))
        assert res.ok, f"cap must warn, not block: {res.summary()}"

    def test_warning_names_the_cap_value(self):
        res = validate_card_tree(_loop(source=TEMPLATED_SOURCE, ceiling=CEILING))
        assert any(str(CEILING) in m for m in _cap_warnings(res)), (
            "the warning must name the cap so the author can act on it"
        )

    def test_uncapped_templated_source_does_not_warn(self):
        """Negative pair: without this, a blanket warning would pass above."""
        for ceiling in (None, 0):
            res = validate_card_tree(
                _loop(source=TEMPLATED_SOURCE, ceiling=ceiling)
            )
            assert not _cap_warnings(res), (
                f"repeat_max={ceiling!r} means uncapped and must not warn; "
                f"got: {_messages(res)}"
            )

    def test_literal_roster_within_cap_does_not_warn(self):
        """A literal roster IS knowable at authoring time; 3 items, cap 60."""
        res = validate_card_tree(
            _loop(source=_literal_roster(3), ceiling=CEILING)
        )
        assert not _cap_warnings(res), (
            "a literal roster that fits under the cap loses nothing and "
            f"must not warn; got: {_messages(res)}"
        )

    def test_literal_roster_exceeding_cap_warns(self):
        """Knowable AND clipped — the strongest case, decidable statically."""
        res = validate_card_tree(
            _loop(source=_literal_roster(ROSTER), ceiling=CEILING)
        )
        found = _cap_warnings(res)
        assert found, (
            f"a literal {ROSTER}-item roster under a cap of {CEILING} drops "
            f"{ROSTER - CEILING} items and must warn; got: {_messages(res)}"
        )
        assert any(str(ROSTER) in m for m in found), (
            "when the roster size is statically known the warning should "
            "name it, so the author sees the actual loss"
        )

    def test_count_mode_cap_is_not_flagged(self):
        """repeat_max has no clipping role in count mode."""
        res = validate_card_tree(
            _loop(source="", ceiling=CEILING, mode="count")
        )
        assert not _cap_warnings(res), (
            f"count mode has no roster to clip; got: {_messages(res)}"
        )

    def test_until_mode_cap_is_not_flagged(self):
        """In until mode repeat_max is the iteration bound, not a clip."""
        res = validate_card_tree(
            _loop(source="", ceiling=CEILING, mode="until")
        )
        assert not _cap_warnings(res), (
            f"until mode's repeat_max is its bound, not a clip; "
            f"got: {_messages(res)}"
        )

    def test_serial_for_each_is_also_flagged(self):
        """Scope loss is independent of parallelism."""
        res = validate_card_tree(
            _loop(source=TEMPLATED_SOURCE, ceiling=CEILING, parallel=False)
        )
        assert _cap_warnings(res), (
            "a serial for_each clips exactly the same way; "
            f"got: {_messages(res)}"
        )


class TestDroppedItemsAreRecoverable:
    """Layer 2: the loss must be recoverable, not merely countable."""

    def _plan(self, roster_size: int, ceiling: int | None):
        from app.agents.block_executor import ExecutionContext, _plan_iterations
        ctx = ExecutionContext(
            run_id="r-cap", project_id="p", project_root="/tmp")
        plan = _plan_iterations(
            _loop(source=_literal_roster(roster_size), ceiling=ceiling), ctx
        )
        return plan, ctx

    def test_dispatched_count_is_unchanged(self):
        """The cost ceiling still holds — this is not a behaviour change."""
        plan, _ = self._plan(ROSTER, CEILING)
        assert len(plan) == CEILING

    def test_dropped_identities_are_recorded(self):
        _, ctx = self._plan(ROSTER, CEILING)
        rec = ctx.roster_truncations.get(LOOP_ID)
        assert rec is not None, "truncation must be recorded at all"
        dropped = rec.get("dropped")
        assert dropped is not None, (
            "counts alone make the loss unrecoverable — the identities of "
            f"the skipped items must be recorded; got keys {sorted(rec)}"
        )
        assert len(dropped) == ROSTER - CEILING, (
            f"expected {ROSTER - CEILING} dropped identities, got "
            f"{len(dropped)}"
        )

    def test_dropped_identities_are_exactly_the_unrun_tail(self):
        """Pins WHICH items, so a follow-up pass can run precisely them."""
        plan, ctx = self._plan(ROSTER, CEILING)
        ran = {it["item"] for it in plan}
        dropped = set(ctx.roster_truncations[LOOP_ID]["dropped"])
        expected = {f"cap-{i:03d}" for i in range(ROSTER)}
        assert dropped == expected - ran
        assert not (dropped & ran), "no item may be both run and dropped"

    def test_counts_still_present_alongside_identities(self):
        """The existing visibility contract must not regress."""
        _, ctx = self._plan(ROSTER, CEILING)
        rec = ctx.roster_truncations[LOOP_ID]
        assert rec["roster"] == ROSTER
        assert rec["dispatched"] == CEILING

    def test_untruncated_roster_records_nothing(self):
        """Negative pair: guards against annotating every loop."""
        _, ctx = self._plan(3, CEILING)
        assert ctx.roster_truncations == {}

    def test_uncapped_roster_records_nothing(self):
        for ceiling in (None, 0):
            _, ctx = self._plan(ROSTER, ceiling)
            assert ctx.roster_truncations == {}, (
                f"repeat_max={ceiling!r} is uncapped; nothing is dropped"
            )


class TestDroppedIdentitiesSurfaceOnTheArtifact:
    """The recoverable list has to reach a human, not just live in ctx."""

    @pytest.mark.asyncio
    async def test_decision_records_a_recoverable_sample(self):
        """The block's decision must point at the dropped items.

        Asserted through the artifact — the outermost surface a reader
        of the run actually sees — rather than through ctx, which the
        test itself populated.
        """
        import asyncio
        import time
        from unittest.mock import patch

        import app.agents.block_executor as be
        from app.agents.block_executor import ExecutionContext
        from app.models.task_card import Artifact

        block = _loop(source=_literal_roster(ROSTER), ceiling=CEILING)
        ctx = ExecutionContext(
            run_id="r-cap", project_id="p", project_root="/tmp")

        async def _work(blk, **kwargs):
            return Artifact(summary="ok", created_at=time.time())

        with patch.object(be, "execute_task_block", _work), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            artifact = await be.execute_block(block, ctx)

        joined = " | ".join(artifact.decisions)
        assert "never run" in joined, (
            f"scope-reduction decision missing: {artifact.decisions}"
        )
        assert "cap-" in joined, (
            "the decision must name dropped items (or a sample of them) so "
            "the loss is actionable, not just a number: "
            f"{artifact.decisions}"
        )
