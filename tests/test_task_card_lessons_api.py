"""Tests for the self-improvement lessons surface.

Three seams, each of which can silently break while its halves stay
correct:

  1. ``extract_pre_image`` / ``revert_lesson_patch`` — the pre-image
     recorded at apply time must be a valid patch that actually
     restores the old text through the guarded path.
  2. ``LessonLedger.for_card`` — the API reads the ledger per-card;
     a filter bug returns another card's lessons.
  3. The REST endpoints — GET /lessons must surface what the ledger
     holds, and POST /lessons/revert must locate the right record and
     write the pre-image back to the live card (asserted on the card
     file, not on the response alone).
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Block, TaskCardCreate
from app.utils.self_improve import (
    LessonLedger,
    apply_improve_patch,
    extract_pre_image,
    patch_hash,
    revert_lesson_patch,
)


# ── Fixtures (mirroring tests/test_api_task_cards.py) ────────────

@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-001"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    project_data = {
        "id": project_id,
        "name": "Test",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }
    (proj_dir / "project.json").write_text(json.dumps(project_data))
    return project_id


@pytest.fixture
def client(ziya_home, project_dir):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.task_cards.get_ziya_home", return_value=ziya_home):
            with patch(
                "app.api.task_cards.get_project_dir",
                return_value=ziya_home / "projects" / project_dir,
            ):
                from app.api.task_cards import router

                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_dir


def _proj_dir(ziya_home, project_id):
    return ziya_home / "projects" / project_id


def _make_card(ziya_home, project_id, instructions="v1 text"):
    from app.storage.task_cards import TaskCardStorage
    storage = TaskCardStorage(_proj_dir(ziya_home, project_id))
    return storage.create(TaskCardCreate(
        name="card", description="",
        root=Block(block_type="group", body=[
            Block(block_type="task", name="t", instructions=instructions),
        ]),
    ))


# ── Seam 1: pre-image round trip ─────────────────────────────────

class TestPreImageRoundTrip:
    def test_extract_captures_current_text_for_patched_fields_only(self):
        root = Block(block_type="group", body=[
            Block(block_type="task", id="t-1", name="a", instructions="old"),
            Block(block_type="task", id="t-2", name="b", instructions="keep"),
        ]).model_dump()
        pre = extract_pre_image({"t-1": {"instructions": "new"}}, root)
        assert pre == {"t-1": {"instructions": "old"}}

    def test_pre_image_reverses_an_applied_patch(self):
        root = Block(block_type="group", body=[
            Block(block_type="task", id="t-1", name="a", instructions="old"),
        ]).model_dump()
        fwd = {"t-1": {"instructions": "new"}}
        pre = extract_pre_image(fwd, root)
        apply_improve_patch(root, fwd)
        assert root["body"][0]["instructions"] == "new"
        apply_improve_patch(root, pre)
        assert root["body"][0]["instructions"] == "old"

    def test_unknown_ids_and_fields_are_not_captured(self):
        root = Block(block_type="group", body=[
            Block(block_type="task", id="t-1", name="a", instructions="x"),
        ]).model_dump()
        pre = extract_pre_image(
            {"ghost": {"instructions": "y"},
             "t-1": {"scope": "EVIL", "instructions": "y"}}, root)
        assert pre == {"t-1": {"instructions": "x"}}

    def test_revert_writes_pre_image_to_live_card(self, ziya_home, project_dir):
        card = _make_card(ziya_home, project_dir, instructions="original")
        task_id = card.root.body[0].id
        with patch("app.utils.paths.get_project_dir",
                   return_value=_proj_dir(ziya_home, project_dir)):
            from app.storage.task_cards import TaskCardStorage
            from app.utils.self_improve import persist_patch_to_card
            # Simulate the improvement having been persisted.
            assert persist_patch_to_card(
                project_dir, card.id, {task_id: {"instructions": "improved"}})
            record = {"pre_image": {task_id: {"instructions": "original"}}}
            assert revert_lesson_patch(project_dir, card.id, record)
            got = TaskCardStorage(_proj_dir(ziya_home, project_dir)).get(card.id)
            assert got.root.body[0].instructions == "original"

    def test_revert_without_pre_image_is_a_recorded_no(self):
        assert revert_lesson_patch("p", "c", {}) is False
        assert revert_lesson_patch("p", "c", {"pre_image": {}}) is False


# ── Seam 2: per-card ledger read ─────────────────────────────────

class TestForCard:
    def test_filters_by_card_and_preserves_order(self, tmp_path):
        ledger = LessonLedger(tmp_path)
        ledger.record({"card_id": "A", "block_id": "b1", "verdict": "revise"})
        ledger.record({"card_id": "B", "block_id": "b1", "verdict": "accept"})
        ledger.record({"card_id": "A", "block_id": "b2", "verdict": "accept"})
        got = ledger.for_card("A")
        assert [r["block_id"] for r in got] == ["b1", "b2"]
        assert all(r["card_id"] == "A" for r in got)


# ── Seam 3: the REST endpoints ───────────────────────────────────

class TestLessonsEndpoint:
    def test_404_for_unknown_card(self, client):
        c, project_id = client
        r = c.get(f"/api/v1/projects/{project_id}/task-cards/nope/lessons")
        assert r.status_code == 404

    def test_returns_ledger_records_newest_first(self, client, ziya_home):
        c, project_id = client
        card = _make_card(ziya_home, project_id)
        ledger = LessonLedger(_proj_dir(ziya_home, project_id))
        ledger.record({"card_id": card.id, "block_id": "b1",
                       "verdict": "revise", "applied": True})
        ledger.record({"card_id": card.id, "block_id": "b1",
                       "verdict": "accept", "applied": False})
        ledger.record({"card_id": "other", "block_id": "b9",
                       "verdict": "revise", "applied": True})
        r = c.get(f"/api/v1/projects/{project_id}/task-cards/{card.id}/lessons")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert body["edits_applied"] == 1
        # Newest first: the accept was recorded last.
        assert [l["verdict"] for l in body["lessons"]] == ["accept", "revise"]


class TestRevertEndpoint:
    def _seed_applied_revision(self, ziya_home, project_id):
        """Card at 'improved', ledger holding the applied record with a
        pre-image of 'original' — the state after one real revision."""
        card = _make_card(ziya_home, project_id, instructions="original")
        task_id = card.root.body[0].id
        fwd = {task_id: {"instructions": "improved"}}
        with patch("app.utils.paths.get_project_dir",
                   return_value=_proj_dir(ziya_home, project_id)):
            from app.utils.self_improve import persist_patch_to_card
            assert persist_patch_to_card(project_id, card.id, fwd)
        LessonLedger(_proj_dir(ziya_home, project_id)).record({
            "card_id": card.id, "block_id": "loop-1", "verdict": "revise",
            "applied": True, "patch": fwd, "patch_hash": patch_hash(fwd),
            "pre_image": {task_id: {"instructions": "original"}},
        })
        return card, task_id, patch_hash(fwd)

    def test_revert_restores_card_text(self, client, ziya_home):
        c, project_id = client
        card, task_id, h = self._seed_applied_revision(ziya_home, project_id)
        with patch("app.utils.paths.get_project_dir",
                   return_value=_proj_dir(ziya_home, project_id)):
            r = c.post(
                f"/api/v1/projects/{project_id}/task-cards/{card.id}"
                f"/lessons/revert",
                json={"patch_hash": h, "block_id": "loop-1"})
        assert r.status_code == 200
        assert r.json()["success"] is True
        # The outermost surface: the card file itself.
        from app.storage.task_cards import TaskCardStorage
        got = TaskCardStorage(_proj_dir(ziya_home, project_id)).get(card.id)
        assert got.root.body[0].instructions == "original"

    def test_unknown_hash_is_404(self, client, ziya_home):
        c, project_id = client
        card, _, _ = self._seed_applied_revision(ziya_home, project_id)
        r = c.post(
            f"/api/v1/projects/{project_id}/task-cards/{card.id}"
            f"/lessons/revert",
            json={"patch_hash": "deadbeef", "block_id": "loop-1"})
        assert r.status_code == 404

    def test_record_without_pre_image_is_409(self, client, ziya_home):
        c, project_id = client
        card = _make_card(ziya_home, project_id)
        fwd = {"x": {"instructions": "y"}}
        LessonLedger(_proj_dir(ziya_home, project_id)).record({
            "card_id": card.id, "block_id": "loop-1", "verdict": "revise",
            "applied": True, "patch": fwd, "patch_hash": patch_hash(fwd),
        })
        r = c.post(
            f"/api/v1/projects/{project_id}/task-cards/{card.id}"
            f"/lessons/revert",
            json={"patch_hash": patch_hash(fwd), "block_id": "loop-1"})
        assert r.status_code == 409


# ── The executor records the pre-image (seam into block_executor) ─

class TestExecutorRecordsPreImage:
    def test_applied_revision_record_carries_pre_image(self, monkeypatch):
        """The ledger record written for an applied revision must hold
        the pre-application text — this is what makes it revertable.
        Runs the real _maybe_self_improve with a stubbed judge/inner."""
        import asyncio
        from app.agents import block_executor as bx
        from app.models.task_card import Artifact

        block = Block(
            block_type="group", id="g-1", name="lvl",
            body=[Block(block_type="task", id="t-1", name="t",
                        instructions="v1 text")],
        )
        block.self_improve = True

        verdicts = iter([
            {"verdict": "revise", "rationale": "", "lesson": "",
             "patch": {"t-1": {"instructions": "v2 text"}}},
            {"verdict": "accept", "rationale": "", "lesson": "", "patch": {}},
        ])

        async def fake_inner(b, ctx):
            return Artifact(summary="ran")

        async def fake_judge(b, a, **kw):
            return next(verdicts)

        import app.agents.improve_evaluator as ev
        monkeypatch.setattr(ev, "evaluate_improvement", fake_judge)

        recorded = []

        class FakeLedger:
            def for_block(self, *a, **k):
                return []

            def seen_patch_hash(self, *a, **k):
                return False

            def record(self, rec):
                recorded.append(rec)

        from app.utils import self_improve as si
        monkeypatch.setattr(si, "LessonLedger", lambda *a: FakeLedger())

        ctx = bx.ExecutionContext(run_id="r1", project_id="p1")
        asyncio.run(bx._maybe_self_improve(block, ctx, fake_inner))
        applied = [r for r in recorded if r.get("applied")]
        assert len(applied) == 1
        assert applied[0]["pre_image"] == {"t-1": {"instructions": "v1 text"}}


# ── Seam 4: the deck-badge aggregate ─────────────────────────────

class TestSummaryByCard:
    """One ledger read must yield per-card counts the deck badge can
    trust — a grouping bug either hides a learning card or badges the
    wrong one."""

    def test_groups_and_counts_by_card(self, ziya_home, project_dir):
        ledger = LessonLedger(_proj_dir(ziya_home, project_dir))
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "verdict": "revise", "applied": True, "ts": 10.0})
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "verdict": "accept", "applied": False, "ts": 20.0})
        ledger.record({"card_id": "c2", "block_id": "b9",
                       "verdict": "stop", "applied": False, "ts": 5.0})
        summary = ledger.summary_by_card()
        assert summary["c1"] == {
            "count": 2, "edits_applied": 1, "last_ts": 20.0}
        assert summary["c2"] == {
            "count": 1, "edits_applied": 0, "last_ts": 5.0}

    def test_records_without_card_id_are_skipped(self, ziya_home, project_dir):
        ledger = LessonLedger(_proj_dir(ziya_home, project_dir))
        ledger.record({"block_id": "b1", "verdict": "accept"})
        assert ledger.summary_by_card() == {}

    def test_empty_ledger_yields_empty_summary(self, ziya_home, project_dir):
        ledger = LessonLedger(_proj_dir(ziya_home, project_dir))
        assert ledger.summary_by_card() == {}


class TestSummaryEndpoint:
    """GET /lessons-summary — and the route-order hazard: registered
    after /{card_id} it would be captured as a card id and 404."""

    def test_returns_per_card_aggregates(self, client, ziya_home):
        test_client, project_id = client
        card = _make_card(ziya_home, project_id)
        ledger = LessonLedger(_proj_dir(ziya_home, project_id))
        ledger.record({"card_id": card.id, "block_id": "b1",
                       "verdict": "revise", "applied": True, "ts": 7.0})
        res = test_client.get(
            f"/api/v1/projects/{project_id}/task-cards/lessons-summary")
        # Route-order seam: a 404 here means the path was captured by
        # GET /{card_id} — the registration-order hazard, not a data bug.
        assert res.status_code == 200
        body = res.json()
        assert body["cards"][card.id]["count"] == 1
        assert body["cards"][card.id]["edits_applied"] == 1

    def test_empty_ledger_returns_empty_map(self, client):
        test_client, project_id = client
        res = test_client.get(
            f"/api/v1/projects/{project_id}/task-cards/lessons-summary")
        assert res.status_code == 200
        assert res.json() == {"cards": {}}
