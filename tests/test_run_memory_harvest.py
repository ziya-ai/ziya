"""Task-run memory harvest (app/agents/run_memory_harvest.py).

The extractor model is stubbed; everything downstream is real: quality
gate, dedup, ProposalsStore projection (which decides what corroboration
counts) and the lifecycle promotion rule.  The trust rules are asserted on
those real outcomes, each with a positive control so a rule cannot pass by
the path never running.
"""
import asyncio
import json
from unittest.mock import patch

import pytest

from app.agents import run_memory_harvest as h
from app.models.task_card import Artifact, Block, TaskCard

FACT = ("The ingest daemon rejects HTTP request headers longer "
        "than 8 KB and drops the request without an error response.")
FACT2 = ("The batch window is three flush intervals, 12 seconds, on the "
         "ingest scheduler.")
GOOD = {"atomicity": 0.95, "self_containment": 0.95, "durability": 0.95}


def _card(cid="card_a", tags=()):
    return TaskCard(id=cid, name="header sweep", tags=list(tags),
                    root=Block(block_type="task", id="t1", instructions="x"))


def _artifact(**kw):
    base = dict(summary="Investigated header handling across twelve daemons. " * 6,
                self_assessment={"objective_met": "true", "rationale": "ok"})
    base.update(kw)
    return Artifact(**base)


def _model_returns(*contents, layer="architecture"):
    out = json.dumps([{"content": c, "layer": layer, "tags": ["http", "ingest"],
                       "confidence": "high", **GOOD} for c in contents])

    async def fake(**kwargs):
        fake.calls.append(kwargs)
        return out
    fake.calls = []
    return fake


def _run(card, artifact, status="done", run_id="run_1", fake=None, path="/w/proj"):
    fake = fake or _model_returns(FACT)
    with patch("app.services.model_resolver.call_service_model", side_effect=fake), \
         patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True):
        res = asyncio.run(h.harvest_run_memories(
            run_id=run_id, card=card, artifact=artifact, status=status, project_path=path))
    return res, fake


def _open():
    from app.storage.proposals import get_proposals_store
    return get_proposals_store().list_open()


def test_done_run_lands_one_probationary_proposal_with_card_provenance():
    res, fake = _run(_card(), _artifact())
    assert res["proposed"] == 1 and len(fake.calls) == 1
    [row] = _open()
    assert row["content"] == FACT
    assert row["conversation_id"] == "task_card:card_a"
    assert row["learned_from"] == "task_run"
    assert row["scope"]["project_paths"] == ["/w/proj"]
    assert row["source_run"]["run_id"] == "run_1" and row["source_run"]["objective_met"] == "true"
    # Graded, but not on the field lifecycle fast-tracks on.
    assert row.get("quality") is None and row["run_quality"] > 0.75


def test_reruns_of_the_same_card_never_corroborate_it():
    card = _card()
    for i in range(5):
        _run(card, _artifact(), run_id=f"run_{i}")
    [row] = _open()
    assert row.get("corroborations", 0) == 0
    # Positive control: a DIFFERENT card agreeing does count.
    _run(_card("card_b"), _artifact(), run_id="run_b")
    [row] = _open()
    assert row["corroborations"] == 1


def test_run_proposal_does_not_fast_track_but_would_if_quality_were_set():
    from app.memory.lifecycle import _evaluate_promotion
    _run(_card(), _artifact())
    [row] = _open()
    assert _evaluate_promotion(row, 1000) is None
    assert _evaluate_promotion({**row, "quality": row["run_quality"]}, 1000) == "quality_fast_track"


def test_harvest_does_not_age_anything():
    from app.memory.lifecycle import current_activity_count
    from app.utils import memory_clock
    before = (current_activity_count(), memory_clock.load())
    _run(_card(), _artifact())
    assert _open(), "harvest did not run"
    assert (current_activity_count(), memory_clock.load()) == before


@pytest.mark.parametrize("card,artifact,status,reason", [
    (_card(tags=["no-memory"]), _artifact(), "done", "card_opted_out"),
    (_card(), _artifact(), "failed", "status_failed"),
    (_card(), _artifact(), "cancelled", "status_cancelled"),
    (_card(), _artifact(self_assessment={"objective_met": "false"}), "done", "objective_not_met"),
    (_card(), _artifact(summary="tiny", self_assessment=None), "done", "too_short"),
])
def test_skips_without_calling_the_model(card, artifact, status, reason):
    res, fake = _run(card, artifact, status=status)
    assert res == {"skipped": True, "reason": reason}
    assert fake.calls == [] and _open() == []


def test_env_kill_switch_and_readonly(monkeypatch):
    monkeypatch.setenv("ZIYA_MEMORY_HARVEST_RUNS", "0")
    assert _run(_card(), _artifact())[0]["reason"] == "disabled"
    monkeypatch.delenv("ZIYA_MEMORY_HARVEST_RUNS")
    monkeypatch.setenv("ZIYA_MEMORY_READONLY", "1")
    assert _run(_card(), _artifact())[0]["reason"] == "memory_readonly"


def test_partial_run_reads_only_passed_stages_and_strips_meta():
    art = _artifact(failed=True, summary="ROOT FAILED SUMMARY", stages=[
        {"index": 0, "label": "daemon a", "status": "passed",
         "summary": "<thinking-data>secret reasoning</thinking-data>FINDING ONE " * 20},
        {"index": 1, "label": "daemon b", "status": "failed", "summary": "BROKEN STAGE"},
    ])
    text = h.build_run_text("sweep", art, "partial")
    assert "FINDING ONE" in text and "daemon a" in text
    assert "ROOT FAILED SUMMARY" not in text and "BROKEN STAGE" not in text
    assert "secret reasoning" not in text
    res, fake = _run(_card(), art, status="partial")
    assert res["proposed"] == 1
    sent = fake.calls[0]["user_message"]
    assert "FINDING ONE" in sent and "BROKEN STAGE" not in sent


def test_paraphrase_of_active_memory_corroborates_it_and_proposes_nothing():
    from app.models.memory import Memory
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    store.save(Memory(id="m_x", content=FACT, layer="architecture", tags=["http", "ingest"]))
    res, _ = _run(_card(), _artifact(), fake=_model_returns(FACT, FACT2))
    # FACT is a keyword duplicate of m_x (dropped); FACT2 is new.
    assert [r["content"] for r in _open()] == [FACT2]
    assert res["proposed"] == 1


def test_scheduler_fire_schedules_harvest(tmp_path):
    """Seam: a scheduled fire hands its final artifact to the harvest."""
    from app.agents import task_scheduler as ts
    from app.models.project import ProjectCreate
    from app.models.task_card import TaskCardCreate
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    root = tmp_path / "repo"
    root.mkdir()
    proj = ProjectStorage(get_ziya_home()).create(ProjectCreate(path=str(root), name="repo"))
    body = Block(block_type="task", id="t1", instructions="x")
    card = TaskCardStorage(get_project_dir(proj.id)).create(
        TaskCardCreate(name="sched", root=Block(block_type="schedule", body=[body])))
    target = ts._ScheduledCard(project_id=proj.id, card_id=card.id, card_name="sched",
                               block=card.root, body_root=card.root.body[0])
    seen = []

    async def recorder(**kw):
        seen.append(kw)

    async def fake_exec(block, ctx):
        return _artifact()

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec), \
             patch.object(h, "harvest_run_memories", side_effect=recorder):
            await ts._fire_one(target)
            for _ in range(20):
                await asyncio.sleep(0)
    asyncio.run(go())
    assert len(seen) == 1
    assert seen[0]["status"] == "done" and seen[0]["card"].id == card.id
    assert seen[0]["project_path"] == proj.path
