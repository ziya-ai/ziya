"""
Self-improvement ledger honesty: a judge that failed must be recorded
as a judge failure, not as an "accept"; the next judge must not be
primed by its predecessors' self-praise; the deck badge must count
edits, not verdicts.

Background (GFX Stage 1 card, Sep 2026): 10 ledger records, 0 revisions
applied, 3 of the 10 were the evaluator's fail-safe fallback recorded
as verdict "accept" — the UI showed 🌱 10 for a card that had never
once improved.
"""
import asyncio

import pytest

from app.models.task_card import Artifact, Block
from app.utils import self_improve as si


def _run(coro):
    return asyncio.run(coro)


def _level():
    return Block(
        block_type="group", id="g-root", name="level", self_improve=True,
        improve_max=2,
        body=[Block(block_type="task", id="t-1", name="one",
                    instructions="v1 instructions")],
    )


# ── evaluator ────────────────────────────────────────────────────

class TestEvaluatorFailureIsAnErrorVerdict:
    def test_transport_failure_is_error_not_accept(self, monkeypatch):
        from app.agents import improve_evaluator as ev
        import app.services.model_resolver as mr

        async def boom(**kw):
            raise RuntimeError("bedrock down")

        monkeypatch.setattr(mr, "call_service_model", boom)
        out = _run(ev.evaluate_improvement(_level(), Artifact(summary="ran")))
        assert out["verdict"] == si.JUDGE_ERROR_VERDICT
        assert out["error"] == "transport"
        assert "bedrock down" in out["rationale"]
        assert out["patch"] == {}

    def test_unparseable_reply_is_error_and_retains_excerpt(self, monkeypatch):
        from app.agents import improve_evaluator as ev
        import app.services.model_resolver as mr
        reply = '{"verdict": "revise", "rationale": "the instructions never' \
                + " x" * 400  # a truncated-looking reply, no closing brace

        async def fake(**kw):
            return reply

        monkeypatch.setattr(mr, "call_service_model", fake)
        out = _run(ev.evaluate_improvement(_level(), Artifact(summary="ran")))
        assert out["verdict"] == si.JUDGE_ERROR_VERDICT
        assert out["error"] == "unparseable"
        # Enough of the raw reply survives to diagnose WHY it failed —
        # the head shows what the judge was trying to say, the length
        # shows whether it hit the token ceiling.
        assert out["reply_excerpt"].startswith('{"verdict": "revise"')
        assert out["reply_len"] == len(reply)
        assert len(out["reply_excerpt"]) < len(reply)

    def test_unknown_verdict_is_error(self, monkeypatch):
        from app.agents import improve_evaluator as ev
        import app.services.model_resolver as mr

        async def fake(**kw):
            return '{"verdict": "maybe", "rationale": "", "lesson": "", "patch": {}}'

        monkeypatch.setattr(mr, "call_service_model", fake)
        out = _run(ev.evaluate_improvement(_level(), Artifact(summary="ran")))
        assert out["verdict"] == si.JUDGE_ERROR_VERDICT
        assert out["error"] == "bad_verdict"

    def test_success_carries_no_error_fields_set(self, monkeypatch):
        from app.agents import improve_evaluator as ev
        import app.services.model_resolver as mr

        async def fake(**kw):
            return '{"verdict": "accept", "rationale": "fine", "lesson": "", "patch": {}}'

        monkeypatch.setattr(mr, "call_service_model", fake)
        out = _run(ev.evaluate_improvement(_level(), Artifact(summary="ran")))
        assert out["verdict"] == "accept"
        assert not out.get("error")


class TestPriorLessonsExcludeSelfPraiseAndErrors:
    LESSONS = [
        {"verdict": "accept", "lesson": "this structure reliably produces the sweep"},
        {"verdict": si.JUDGE_ERROR_VERDICT, "rationale": "judge transport failed"},
        {"verdict": "stop", "lesson": "render server is stale after rebuild"},
        {"verdict": "revise", "lesson": "name the blackboard path explicitly"},
        {"verdict": "revise", "lesson": "", "rationale": ""},  # nothing to teach
    ]

    def test_filter_helper(self):
        kept = si.prior_lessons_for_judge(self.LESSONS)
        assert [r["verdict"] for r in kept] == ["stop", "revise"]

    def test_prompt_only_shows_revise_and_stop(self):
        from app.agents.improve_evaluator import _build_user_message
        msg = _build_user_message(
            _level(), Artifact(summary="ran"), criterion="", drift="conservative",
            lessons=self.LESSONS, revision=0,
        )
        assert "render server is stale" in msg
        assert "name the blackboard path" in msg
        assert "reliably produces" not in msg
        assert "judge transport failed" not in msg


# ── executor ─────────────────────────────────────────────────────

class TestExecutorRecordsJudgeError:
    def test_error_verdict_is_ledgered_as_error_and_stops(
            self, monkeypatch, tmp_path):
        from app.agents import block_executor as bx
        from app.agents.block_executor import ExecutionContext
        import app.agents.improve_evaluator as ev
        import app.utils.paths as paths

        monkeypatch.setattr(paths, "get_project_dir", lambda pid: tmp_path)
        calls = {"exec": 0}

        async def fake_inner(block, ctx):
            calls["exec"] += 1
            return Artifact(summary="ok")

        async def fake_judge(block, artifact, **kw):
            return {"verdict": si.JUDGE_ERROR_VERDICT, "error": "unparseable",
                    "rationale": "judge reply unparseable",
                    "reply_excerpt": '{"verdict": "rev', "reply_len": 9000,
                    "lesson": "", "patch": {}}

        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)
        ctx = ExecutionContext(run_id="run-1", project_id="p1", storage=None)
        art = _run(bx._maybe_self_improve(_level(), ctx, fake_inner))

        assert calls["exec"] == 1  # a failed judge never restarts the level
        assert "self-improve: judge_error" in art.decisions
        recs = si.LessonLedger(tmp_path).for_block(None, "g-root")
        assert len(recs) == 1
        rec = recs[0]
        assert rec["verdict"] == si.JUDGE_ERROR_VERDICT
        assert rec["error"] == "unparseable"
        assert rec["reply_excerpt"] == '{"verdict": "rev'
        assert rec["reply_len"] == 9000
        assert rec["applied"] is False


# ── ledger ───────────────────────────────────────────────────────

class TestLedgerHonesty:
    def test_legacy_fallback_records_relabelled_on_read(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        ledger.record({"card_id": "c", "block_id": "b", "verdict": "accept",
                       "rationale": si.LEGACY_FALLBACK_RATIONALE, "lesson": ""})
        ledger.record({"card_id": "c", "block_id": "b", "verdict": "accept",
                       "rationale": "genuinely fine", "lesson": "ok"})
        got = ledger.for_block("c", "b")
        assert [r["verdict"] for r in got] == [si.JUDGE_ERROR_VERDICT, "accept"]
        assert got[0]["error"] == "legacy_fallback"

    def test_summary_counts_judge_errors_separately(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        ledger.record({"card_id": "c", "block_id": "b", "verdict": "accept",
                       "rationale": si.LEGACY_FALLBACK_RATIONALE})
        ledger.record({"card_id": "c", "block_id": "b",
                       "verdict": si.JUDGE_ERROR_VERDICT, "error": "transport"})
        ledger.record({"card_id": "c", "block_id": "b", "verdict": "accept",
                       "rationale": "fine"})
        ledger.record({"card_id": "c", "block_id": "b", "verdict": "revise",
                       "applied": True, "patch_hash": "h"})
        s = ledger.summary_by_card()["c"]
        assert s["count"] == 4
        assert s["edits_applied"] == 1
        assert s["judge_errors"] == 2


class TestLessonsApiExposesJudgeErrors:
    def test_card_lessons_endpoint_reports_judge_errors(self, tmp_path, monkeypatch):
        """The seam: the ledger's aggregate must reach the HTTP surface
        the panel reads, not stop at the storage layer."""
        pytest.importorskip("fastapi")
        from app.api import task_cards as api
        from app.storage.task_cards import TaskCardStorage

        class _Store:
            def get(self, cid):
                return object()

        monkeypatch.setattr(api, "_get_storage", lambda pid: _Store())
        monkeypatch.setattr(api, "get_project_dir", lambda pid: tmp_path)
        ledger = si.LessonLedger(tmp_path)
        ledger.record({"card_id": "c1", "block_id": "b",
                       "verdict": si.JUDGE_ERROR_VERDICT, "error": "transport"})
        ledger.record({"card_id": "c1", "block_id": "b", "verdict": "accept"})
        body = _run(api.get_card_lessons("p1", "c1", limit=200))
        assert body["count"] == 2
        assert body["edits_applied"] == 0
        assert body["judge_errors"] == 1
