"""
Fix 3 of the task-card self-improvement audit: targeted patch ops.

A judge revise formerly had to carry the FULL replacement text of every
field it touched.  Against the GFX Stage 1 card that is ~1,100 raw
tokens for one field before JSON escaping — a reply that also carries
a second field, a rationale and a lesson does not fit the former
2,000-token ceiling, so the only replies that tried to revise are the
ones most likely to have been truncated into a judge failure.

The fix: field values may be a list of ops (``replace`` an exact,
unique substring; ``append``) which ``resolve_improve_patch`` expands
to full text against the current subtree BEFORE validation.  Every
downstream step — hash (oscillation guard), pre-image, apply, persist
— sees only the resolved full text, so op-form and full-form patches
that produce the same text are the same revision.

Seam under test: judge emits ops → executor resolves → validates the
resolved text → applies → ledger record carries the RESOLVED patch and
its hash.  ``test_op_patch_flows_through_executor_to_the_ledger`` is
the one that fails against an executor that hands ops straight to the
validator.
"""
import asyncio

import pytest

from app.models.task_card import Artifact, Block
from app.utils import self_improve as si


def _tree():
    return {
        "id": "g", "block_type": "group", "body": [
            {"id": "t1", "block_type": "task",
             "instructions": "Render every spec.\nJudge the image.\nRecord the verdict."},
            {"id": "t2", "block_type": "task", "instructions": "x y x"},
        ],
    }


# ── 1. Resolver ────────────────────────────────────────────────────

class TestResolveOps:
    def test_full_string_passes_through_unchanged(self):
        out, errs = si.resolve_improve_patch(
            {"t1": {"instructions": "whole new text"}}, _tree())
        assert errs == []
        assert out == {"t1": {"instructions": "whole new text"}}

    def test_replace_unique_substring(self):
        out, errs = si.resolve_improve_patch(
            {"t1": {"instructions": [
                {"op": "replace", "find": "Judge the image.",
                 "with": "Judge the image in BOTH themes."}]}}, _tree())
        assert errs == []
        assert out["t1"]["instructions"] == (
            "Render every spec.\nJudge the image in BOTH themes.\nRecord the verdict.")

    def test_single_op_object_accepted_as_one_element_list(self):
        out, errs = si.resolve_improve_patch(
            {"t1": {"instructions": {"op": "append", "text": "Never file_write."}}},
            _tree())
        assert errs == []
        assert out["t1"]["instructions"].endswith("Record the verdict.\n\nNever file_write.")

    def test_ops_apply_in_order_against_edited_text(self):
        out, errs = si.resolve_improve_patch(
            {"t1": {"instructions": [
                {"op": "append", "text": "Step 4."},
                {"op": "replace", "find": "Step 4.", "with": "Step four."}]}},
            _tree())
        assert errs == []
        assert out["t1"]["instructions"].endswith("Step four.")

    def test_replace_refuses_zero_matches(self):
        out, errs = si.resolve_improve_patch(
            {"t1": {"instructions": [
                {"op": "replace", "find": "not present", "with": "z"}]}}, _tree())
        assert len(errs) == 1 and "occurs 0 time(s)" in errs[0]

    def test_replace_refuses_ambiguous_match(self):
        # "x" occurs twice in t2 — a guessed edit is worse than none.
        out, errs = si.resolve_improve_patch(
            {"t2": {"instructions": [
                {"op": "replace", "find": "x", "with": "z"}]}}, _tree())
        assert len(errs) == 1 and "occurs 2 time(s)" in errs[0]

    def test_unknown_op_rejected(self):
        _, errs = si.resolve_improve_patch(
            {"t1": {"instructions": [{"op": "prepend", "text": "a"}]}}, _tree())
        assert errs and "unknown op 'prepend'" in errs[0]

    def test_bad_id_and_field_are_left_for_the_validator(self):
        # The resolver must not invent a second error for an id/field
        # problem the validator already reports — one cause, one message.
        out, errs = si.resolve_improve_patch(
            {"nope": {"instructions": [{"op": "append", "text": "a"}]},
             "t1": {"scope": [{"op": "append", "text": "a"}]}}, _tree())
        assert errs == []
        v_errs = si.validate_improve_patch(out, _tree())
        assert any("unknown block id" in e for e in v_errs)
        assert any("not improvable" in e for e in v_errs)

    def test_resolved_text_hashes_identically_to_full_form(self):
        # The oscillation guard keys on the hash: the same edit written
        # two ways must be ONE revision, or a judge could alternate forms
        # to walk around the guard.
        full = {"t1": {"instructions":
                "Render every spec.\nJudge the image.\nRecord the verdict.\n\nAlso this."}}
        ops = {"t1": {"instructions": [{"op": "append", "text": "Also this."}]}}
        resolved, errs = si.resolve_improve_patch(ops, _tree())
        assert errs == []
        assert si.patch_hash(resolved) == si.patch_hash(full)


# ── 2. Executor seam ───────────────────────────────────────────────

class _Ledger:
    def __init__(self):
        self.records = []

    def for_block(self, *a, **k):
        return []

    def seen_patch_hash(self, *a, **k):
        return False

    def record(self, rec):
        self.records.append(rec)


class TestExecutorSeam:
    def test_op_patch_flows_through_executor_to_the_ledger(self, monkeypatch):
        from app.agents import block_executor as bx
        import app.agents.improve_evaluator as ev

        block = Block(
            block_type="group", id="g-root", name="level", self_improve=True,
            improve_max=1,
            body=[Block(block_type="task", id="t-1", name="one",
                        instructions="Render every spec.\nJudge the image.")],
        )
        seen_instructions = []

        async def fake_inner(b, ctx):
            seen_instructions.append(b.body[0].instructions)
            return Artifact(summary="ran")

        verdicts = iter([
            {"verdict": "revise", "rationale": "single theme", "lesson": "l",
             "patch": {"t-1": {"instructions": [
                 {"op": "replace", "find": "Judge the image.",
                  "with": "Judge the image in BOTH themes."}]}}},
            {"verdict": "accept", "rationale": "ok", "lesson": "", "patch": {}},
        ])

        async def fake_judge(*a, **k):
            return next(verdicts)

        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        ledger = _Ledger()
        monkeypatch.setattr(si, "LessonLedger", lambda *a, **k: ledger)
        monkeypatch.setattr(si, "persist_patch_to_card", lambda *a, **k: True)

        ctx = bx.ExecutionContext(run_id="r", project_id="p", storage=None)
        asyncio.run(bx._maybe_self_improve(block, ctx, fake_inner))

        # The level restarted with the RESOLVED text.
        assert seen_instructions == [
            "Render every spec.\nJudge the image.",
            "Render every spec.\nJudge the image in BOTH themes.",
        ]
        rec = ledger.records[0]
        assert rec["applied"] is True
        # The ledger carries full text, not ops — the revert endpoint
        # replays ``patch``/``pre_image`` as full-text patches.
        assert rec["patch"] == {"t-1": {"instructions":
                                "Render every spec.\nJudge the image in BOTH themes."}}
        assert rec["pre_image"] == {"t-1": {"instructions":
                                    "Render every spec.\nJudge the image."}}
        assert rec["patch_hash"] == si.patch_hash(rec["patch"])

    def test_unresolvable_op_stops_with_invalid_patch(self, monkeypatch):
        from app.agents import block_executor as bx
        import app.agents.improve_evaluator as ev

        block = Block(
            block_type="group", id="g-root", name="level", self_improve=True,
            improve_max=2,
            body=[Block(block_type="task", id="t-1", name="one",
                        instructions="a b a")],
        )
        runs = []

        async def fake_inner(b, ctx):
            runs.append(1)
            return Artifact(summary="ran")

        async def fake_judge(*a, **k):
            return {"verdict": "revise", "rationale": "x", "lesson": "",
                    "patch": {"t-1": {"instructions": [
                        {"op": "replace", "find": "a", "with": "z"}]}}}

        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        ledger = _Ledger()
        monkeypatch.setattr(si, "LessonLedger", lambda *a, **k: ledger)
        ctx = bx.ExecutionContext(run_id="r", project_id="p", storage=None)
        art = asyncio.run(bx._maybe_self_improve(block, ctx, fake_inner))

        assert len(runs) == 1  # no restart on an unapplied patch
        assert "self-improve: invalid_patch" in art.decisions
        assert ledger.records[0]["applied"] is False
        assert any("occurs 2 time(s)" in e for e in ledger.records[0]["errors"])


# ── 3. Evaluator ──────────────────────────────────────────────────

class TestEvaluator:
    def test_prompt_documents_ops_and_prefers_them(self):
        from app.agents import improve_evaluator as ev
        p = ev._SYSTEM_PROMPT
        assert '"op": "replace"' in p and '"op": "append"' in p
        assert "exactly once" in p

    def test_judge_call_uses_the_raised_ceiling(self, monkeypatch):
        from app.agents import improve_evaluator as ev
        import app.services.model_resolver as mr
        seen = {}

        async def fake_call(category, **kw):
            seen.update(kw)
            return '{"verdict": "accept", "rationale": "", "lesson": "", "patch": {}}'

        monkeypatch.setattr(mr, "call_service_model", fake_call)
        block = Block(block_type="group", id="g", name="", body=[
            Block(block_type="task", id="t", name="", instructions="x")])
        asyncio.run(ev.evaluate_improvement(block, Artifact(summary="ran")))
        assert seen["max_tokens"] == si.JUDGE_MAX_TOKENS
        assert si.JUDGE_MAX_TOKENS >= 6000

    def test_op_form_patch_survives_the_evaluator(self, monkeypatch):
        # The evaluator must hand op lists through untouched; the
        # executor resolves them.
        from app.agents import improve_evaluator as ev
        import app.services.model_resolver as mr

        async def fake_call(category, **kw):
            return ('{"verdict": "revise", "rationale": "r", "lesson": "l", '
                    '"patch": {"t": {"instructions": [{"op": "append", "text": "z"}]}}}')

        monkeypatch.setattr(mr, "call_service_model", fake_call)
        block = Block(block_type="group", id="g", name="", body=[
            Block(block_type="task", id="t", name="", instructions="x")])
        out = asyncio.run(ev.evaluate_improvement(block, Artifact(summary="ran")))
        assert out["patch"] == {"t": {"instructions": [{"op": "append", "text": "z"}]}}
