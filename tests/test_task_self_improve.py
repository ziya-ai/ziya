"""Tests for self-improving task cards.

Covers the three layers:
  1. Guard invariants in app/utils/self_improve.py — the patch
     whitelist, existing-id keying, the structure fingerprint ("text
     changed and ONLY text"), no-op rejection, budgets.
  2. The lesson ledger — durability, per-(card, block) recall, the
     cross-run oscillation guard.
  3. The executor loop (_maybe_self_improve) with a stubbed judge —
     restart on revise, stop on accept, budget exhaustion, and the
     seam to card persistence.

The judge's model call itself is not exercised (no network in unit
tests); its parsing is covered separately below.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.models.task_card import Artifact, Block
from app.utils import self_improve as si


def _tree() -> dict:
    """A group with one state and two tasks, dict form, ids present."""
    return Block(
        block_type="group", id="g-root", name="level",
        body=[
            Block(block_type="state", id="s-1", name="givens",
                  state_context="assume prod"),
            Block(block_type="task", id="t-1", name="one",
                  instructions="do the first thing"),
            Block(block_type="task", id="t-2", name="two",
                  instructions="do the second thing"),
        ],
    ).model_dump()


# ── 1. Patch guard invariants ───────────────────────────────────

class TestPatchValidation:
    def test_valid_patch_passes(self):
        patch = {"t-1": {"instructions": "do the first thing, but check X"}}
        assert si.validate_improve_patch(patch, _tree()) == []

    def test_unknown_block_id_rejected(self):
        patch = {"t-999": {"instructions": "hi"}}
        errors = si.validate_improve_patch(patch, _tree())
        assert any("unknown block id" in e for e in errors)

    def test_non_whitelisted_field_rejected(self):
        # scope is the privilege surface; repeat_count is the loop the
        # model could weaken to make the card look cheaper.
        for field in ("scope", "repeat_count", "id", "block_type"):
            patch = {"t-1": {field: "x"}}
            errors = si.validate_improve_patch(patch, _tree())
            assert any("not improvable" in e for e in errors), field

    def test_empty_value_rejected(self):
        errors = si.validate_improve_patch(
            {"t-1": {"instructions": "   "}}, _tree())
        assert any("non-empty string" in e for e in errors)

    def test_noop_patch_rejected(self):
        # Identical text is not a revision — the loop must not restart
        # a level over a patch that changes nothing.
        patch = {"t-1": {"instructions": "do the first thing"}}
        errors = si.validate_improve_patch(patch, _tree())
        assert any("changes nothing" in e for e in errors)

    def test_empty_patch_rejected(self):
        assert si.validate_improve_patch({}, _tree())
        assert si.validate_improve_patch(None, _tree())

    def test_state_context_is_patchable(self):
        patch = {"s-1": {"state_context": "assume prod; migration ran"}}
        assert si.validate_improve_patch(patch, _tree()) == []


class TestPatchApplication:
    def test_apply_changes_text(self):
        tree = _tree()
        n = si.apply_improve_patch(
            tree, {"t-1": {"instructions": "revised"}})
        assert n == 1
        blocks = si.collect_blocks_by_id(tree)
        assert blocks["t-1"]["instructions"] == "revised"
        # Sibling untouched.
        assert blocks["t-2"]["instructions"] == "do the second thing"

    def test_apply_ignores_unknown_ids_and_fields(self):
        # Best-effort against live-card drift: unmatched ids don't
        # apply; non-whitelisted fields never apply even unvalidated.
        tree = _tree()
        n = si.apply_improve_patch(tree, {
            "t-999": {"instructions": "x"},
            "t-1": {"scope": "evil", "repeat_count": 99},
        })
        assert n == 0
        assert si.collect_blocks_by_id(tree)["t-1"].get("scope") is None

    def test_structure_fingerprint_invariant_under_text_patch(self):
        # The "text but not privilege" invariant, asserted directly:
        # a whitelisted patch leaves the fingerprint unchanged...
        tree = _tree()
        before = si.structure_fingerprint(tree)
        si.apply_improve_patch(tree, {"t-1": {"instructions": "revised"}})
        assert si.structure_fingerprint(tree) == before

    def test_structure_fingerprint_detects_scope_change(self):
        # ...and any scope/structure change moves it.  This is the
        # check that catches a future regression in apply_improve_patch
        # before it can reach a saved card.
        tree = _tree()
        before = si.structure_fingerprint(tree)
        blocks = si.collect_blocks_by_id(tree)
        blocks["t-1"]["scope"] = {"shell_commands": ["rm"]}
        assert si.structure_fingerprint(tree) != before

    def test_structure_fingerprint_detects_id_change(self):
        tree = _tree()
        before = si.structure_fingerprint(tree)
        si.collect_blocks_by_id(tree)["t-2"]["id"] = "t-2-new"
        assert si.structure_fingerprint(tree) != before


class TestBudgets:
    def test_default_and_clamping(self):
        assert si.resolve_improve_max(None) == si.DEFAULT_IMPROVE_MAX
        assert si.resolve_improve_max(5) == 5
        assert si.resolve_improve_max(-3) == 0
        assert si.resolve_improve_max("bogus") == si.DEFAULT_IMPROVE_MAX

    def test_run_ceiling_env_override(self, monkeypatch):
        monkeypatch.setenv("ZIYA_TASK_IMPROVE_RUN_MAX", "3")
        assert si.run_improve_ceiling() == 3
        monkeypatch.setenv("ZIYA_TASK_IMPROVE_RUN_MAX", "junk")
        assert si.run_improve_ceiling() == si.DEFAULT_RUN_IMPROVE_CEILING


# ── 2. Lesson ledger ────────────────────────────────────────────

class TestLessonLedger:
    def test_record_and_recall(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "verdict": "revise", "lesson": "be explicit about X"})
        ledger.record({"card_id": "c1", "block_id": "b2",
                       "verdict": "accept", "lesson": "other block"})
        got = ledger.for_block("c1", "b1")
        assert len(got) == 1
        assert got[0]["lesson"] == "be explicit about X"
        assert "ts" in got[0]

    def test_recall_is_scoped_and_limited(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        for i in range(12):
            ledger.record({"card_id": "c1", "block_id": "b1", "n": i})
        got = ledger.for_block("c1", "b1", limit=8)
        assert len(got) == 8
        assert got[-1]["n"] == 11  # most recent retained, oldest first
        assert ledger.for_block("c2", "b1") == []

    def test_oscillation_guard(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        patch = {"t-1": {"instructions": "revised"}}
        h = si.patch_hash(patch)
        # Not seen until an APPLIED record exists.
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "patch_hash": h, "applied": False})
        assert not ledger.seen_patch_hash("c1", "b1", h)
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "patch_hash": h, "applied": True})
        assert ledger.seen_patch_hash("c1", "b1", h)
        # Scoped: same patch on another block is fresh.
        assert not ledger.seen_patch_hash("c1", "b2", h)

    def test_write_never_raises(self, tmp_path):
        # Sink must not fail the run that produced the lesson.
        ledger = si.LessonLedger(tmp_path / "nonexistent" / "deep")
        ledger.record({"card_id": "c1", "block_id": "b1"})  # creates dirs
        assert ledger.for_block("c1", "b1")


# ── 3. Executor loop with stubbed judge ─────────────────────────

def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def improving_block():
    return Block(
        block_type="group", id="g-root", name="level", self_improve=True,
        improve_max=2,
        body=[Block(block_type="task", id="t-1", name="one",
                    instructions="v1 instructions")],
    )


def _ctx():
    from app.agents.block_executor import ExecutionContext
    return ExecutionContext(run_id="run-1", project_id=None, storage=None)


class TestExecutorLoop:
    def test_accept_runs_once(self, improving_block, monkeypatch):
        from app.agents import block_executor as bx
        calls = {"exec": 0, "judge": 0}

        async def fake_inner(block, ctx):
            calls["exec"] += 1
            return Artifact(summary="ok")

        async def fake_judge(block, artifact, **kw):
            calls["judge"] += 1
            return {"verdict": "accept", "rationale": "fine",
                    "lesson": "", "patch": {}}

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        art = _run(bx._maybe_self_improve(improving_block, _ctx(), fake_inner))
        assert calls == {"exec": 1, "judge": 1}
        assert art.summary == "ok"
        # accept adds no decision noise
        assert not any("self-improve" in d for d in art.decisions)

    def test_flag_off_skips_judge_entirely(self, monkeypatch):
        from app.agents import block_executor as bx
        block = Block(block_type="group", id="g", name="plain",
                      body=[Block(block_type="task", id="t", name="x",
                                  instructions="y")])
        judged = []

        async def fake_inner(b, c):
            return Artifact(summary="ok")

        async def fake_judge(*a, **k):
            judged.append(1)
            return {"verdict": "accept", "patch": {}}

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        _run(bx._maybe_self_improve(block, _ctx(), fake_inner))
        assert not judged

    def test_revise_restarts_level_with_patched_text(
            self, improving_block, monkeypatch):
        from app.agents import block_executor as bx
        seen_instructions = []
        verdicts = iter([
            {"verdict": "revise", "rationale": "weak", "lesson": "l",
             "patch": {"t-1": {"instructions": "v2 instructions"}}},
            {"verdict": "accept", "rationale": "good now",
             "lesson": "", "patch": {}},
        ])

        async def fake_inner(block, ctx):
            seen_instructions.append(block.body[0].instructions)
            return Artifact(summary="ran")

        async def fake_judge(block, artifact, **kw):
            return next(verdicts)

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        ctx = _ctx()
        art = _run(bx._maybe_self_improve(improving_block, ctx, fake_inner))
        # THE core behavior: level re-executed, second pass sees v2 text.
        assert seen_instructions == ["v1 instructions", "v2 instructions"]
        assert ctx.improve_edits_used == 1
        assert any("revision 1 applied" in d for d in art.decisions)

    def test_budget_exhaustion_stops_editing(
            self, improving_block, monkeypatch):
        from app.agents import block_executor as bx
        improving_block.improve_max = 1
        n = {"exec": 0}

        async def fake_inner(block, ctx):
            n["exec"] += 1
            return Artifact(summary=f"run {n['exec']}")

        async def fake_judge(block, artifact, **kw):
            # Judge ALWAYS wants a (different) revision — budget must win.
            return {"verdict": "revise", "rationale": "more",
                    "lesson": "", "patch": {"t-1": {
                        "instructions": f"v{n['exec'] + 1} instructions"}}}

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        art = _run(bx._maybe_self_improve(
            improving_block, _ctx(), fake_inner))
        # improve_max=1 → at most 2 executions (original + 1 revision).
        assert n["exec"] == 2
        assert any("budget_exhausted" in d for d in art.decisions)

    def test_improve_max_zero_is_observe_only(
            self, improving_block, monkeypatch):
        from app.agents import block_executor as bx
        improving_block.improve_max = 0
        n = {"exec": 0}

        async def fake_inner(block, ctx):
            n["exec"] += 1
            return Artifact(summary="ran")

        async def fake_judge(block, artifact, **kw):
            return {"verdict": "revise", "rationale": "wants edit",
                    "lesson": "", "patch": {"t-1": {
                        "instructions": "never applied"}}}

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        ctx = _ctx()
        _run(bx._maybe_self_improve(improving_block, ctx, fake_inner))
        assert n["exec"] == 1
        assert ctx.improve_edits_used == 0

    def test_invalid_patch_stops_without_edit(
            self, improving_block, monkeypatch):
        from app.agents import block_executor as bx
        n = {"exec": 0}

        async def fake_inner(block, ctx):
            n["exec"] += 1
            return Artifact(summary="ran")

        async def fake_judge(block, artifact, **kw):
            # Patch tries to touch a sibling outside this level AND a
            # forbidden field — both rejected, loop must end.
            return {"verdict": "revise", "rationale": "x", "lesson": "",
                    "patch": {"t-elsewhere": {"instructions": "hi"},
                              "t-1": {"scope": "evil"}}}

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        ctx = _ctx()
        art = _run(bx._maybe_self_improve(improving_block, ctx, fake_inner))
        assert n["exec"] == 1
        assert ctx.improve_edits_used == 0
        assert any("invalid_patch" in d for d in art.decisions)


# ── Judge reply parsing (no model call) ─────────────────────────

class TestJudgeParsing:
    def test_strict_json(self):
        from app.agents.improve_evaluator import _extract_json
        assert _extract_json('{"verdict": "accept"}') == {"verdict": "accept"}

    def test_fenced_json(self):
        from app.agents.improve_evaluator import _extract_json
        text = '```json\n{"verdict": "revise", "patch": {}}\n```'
        assert _extract_json(text)["verdict"] == "revise"

    def test_garbage_returns_none(self):
        from app.agents.improve_evaluator import _extract_json
        assert _extract_json("I think it went well!") is None
        assert _extract_json(None) is None
        assert _extract_json('["not", "an", "object"]') is None

    def test_editable_blocks_lists_only_text_carriers(self):
        from app.agents.improve_evaluator import _editable_blocks
        block = Block(
            block_type="group", id="g", name="lvl",
            body=[
                Block(block_type="state", id="s", name="st",
                      state_context="given"),
                Block(block_type="task", id="t", name="tk",
                      instructions="do"),
                Block(block_type="parallel", id="p", name="par"),
            ],
        )
        ids = {b["id"] for b in _editable_blocks(block)}
        assert ids == {"s", "t"}


# ── Persistence seam ────────────────────────────────────────────

class TestPersistPatchToCard:
    def test_missing_ids_are_noop(self):
        # No project/card → False, never raises.
        assert si.persist_patch_to_card(None, None, {"a": {}}) is False
        assert si.persist_patch_to_card("pid", None, {}) is False
