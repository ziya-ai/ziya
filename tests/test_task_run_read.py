"""task_run_read: a run's outcome is readable without the browser.

Run records are encrypted at rest and the journal was tailed only by the
UI, so nothing exposed WHY a run failed to a model or a script.  These
tests build a real run through TaskRunStorage (so the encrypted-at-rest
path is the one exercised), write a journal the way the relay does, and
assert that the summary names the failed block, its executor
``failure_signature``, the model's verdict, the card version that ran,
the schedule skips, and the ``task_finished`` events — the facts an
operator reconstructed by hand before.
"""

import asyncio
import json

import pytest

from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import TaskRunBlockState, TaskRunCreate, IterationSummary
from app.utils import task_run_read as trr


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    from app.models.project import ProjectCreate
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    repo = tmp_path / "repo"
    repo.mkdir()
    p = ProjectStorage(get_ziya_home()).create(ProjectCreate(path=str(repo), name="repo"))
    pdir = get_project_dir(p.id)
    card = TaskCardStorage(pdir).create(TaskCardCreate(
        name="Nightly audit", description="d",
        root=Block(block_type="group", id="g", body=[
            Block(block_type="task", id="t1", name="baseline", instructions="x"),
            Block(block_type="task", id="t2", name="report", instructions="y"),
        ])))
    return {"project": p, "pdir": pdir, "card": card,
            "runs": TaskRunStorage(pdir), "cards": TaskCardStorage(pdir)}


def _failed_run(proj):
    runs = proj["runs"]
    run = runs.create(TaskRunCreate(card_id=proj["card"].id, source_conversation_id=None))
    runs.set_card_snapshot(run.id, {
        "name": "Nightly audit", "version": 7,
        "root": proj["card"].root.model_dump(), "schedule_block_id": "s1",
    })
    runs.update_status(run.id, "running")
    t1 = Artifact(summary="all good", created_at=1.0)
    t2 = Artifact(
        summary="did most of it", failed=True, signature="self_assessment_partial",
        self_assessment={"objective_met": "partial", "rationale": "lane 1B skipped"},
        decisions=["self_assessment: objective_met=partial"], created_at=2.0,
    )
    runs.set_block_state(run.id, TaskRunBlockState(
        block_id="t1", block_type="task", status="done", artifact=t1, started_at=1))
    runs.set_block_state(run.id, TaskRunBlockState(
        block_id="t2", block_type="task", status="failed", artifact=t2, started_at=2,
        iteration_summaries=[IterationSummary(index=0, status="failed",
                                              signature="self_assessment_partial")]))
    final = Artifact(summary="Group of 2", failed=True, signature="self_assessment_partial",
                     decisions=["sequence stopped: step 2/2 (report) failed"], created_at=3.0)
    runs.set_artifact(run.id, final)
    runs.update_status(run.id, "failed")
    # Journal as the relay writes it: one JSON object per line.
    jp = runs.journal_path(run.id)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with open(jp, "w") as fh:
        for ev in [
            {"type": "task_text_delta", "block_id": "t1", "content": "x"},
            {"type": "task_finished", "block_id": "t1", "ok": True,
             "self_assessment": {"objective_met": "true", "rationale": "ok"},
             "failure_signature": None, "failure_reason": None},
            {"type": "task_tool_call", "block_id": "t2", "tool_name": "file_read"},
            {"type": "task_finished", "block_id": "t2", "ok": False,
             "self_assessment": {"objective_met": "partial", "rationale": "lane 1B skipped"},
             "failure_signature": "self_assessment_partial",
             "failure_reason": "self_assessment objective_met=partial: lane 1B skipped"},
        ]:
            fh.write(json.dumps(ev) + "\n")
        fh.write('{"type": "task_text_delta", "block_id": "t2", "con')  # partial tail
    return runs.get(run.id)


def test_summary_names_the_failed_block_and_why(proj):
    run = _failed_run(proj)
    out = trr.summarize_run(proj["runs"], run, project_id=proj["project"].id)

    assert out["run"]["status"] == "failed"
    assert out["run"]["card_name"] == "Nightly audit"
    assert out["run"]["card_version"] == 7
    assert out["run"]["scheduled_fire"] is True
    assert out["run"]["failed"] is True

    [f] = out["failures"]
    assert f["block_id"] == "t2"
    assert f["signature"] == "self_assessment_partial"
    assert f["self_assessment"]["rationale"] == "lane 1B skipped"
    assert f["failure_reason"] == "self_assessment objective_met=partial: lane 1B skipped"

    by_id = {b["block_id"]: b for b in out["blocks"]}
    assert by_id["t1"]["failed"] is False and by_id["t2"]["iterations_failed"] == 1

    # Only verdict-bearing events come back; traffic is counted, not returned.
    assert [e["type"] for e in out["events"]] == ["task_finished", "task_finished"]
    assert out["events"][1]["failure_signature"] == "self_assessment_partial"
    assert out["event_counts"]["task_text_delta"] == 1   # the partial tail is dropped
    assert out["event_counts"]["task_tool_call"] == 1
    # No payloads: the final artifact's summary is clipped prose, outputs an index.
    assert out["artifact"]["summary"] == "Group of 2"
    assert out["artifact"]["outputs"] == []


def test_block_drilldown_carries_full_decisions_and_iterations(proj):
    run = _failed_run(proj)
    out = trr.summarize_run(proj["runs"], run, block_id="t2", include_events=True)
    blk = out["block"]
    assert blk["artifact"]["decisions"] == ["self_assessment: objective_met=partial"]
    assert blk["iteration_summaries"] == [
        {"index": 0, "status": "failed", "signature": "self_assessment_partial",
         "duration_ms": 0, "tokens": 0}]
    # Events are narrowed to the block.
    assert all(e["block_id"] == "t2" for e in out["events"]) and out["events"]
    missing = trr.summarize_run(proj["runs"], run, block_id="nope", include_events=False)
    assert "no such block" in missing["block"]["error"]


def test_failure_reason_for_runs_whose_journal_predates_the_field():
    a = Artifact(summary="", failed=True, signature="output_contract:finding",
                 decisions=["output_contract: required output(s) not produced after one "
                            "correction turn: finding"])
    assert trr.failure_reason_of(a).startswith("output_contract: required")
    b = Artifact(summary="", failed=True, signature="self_assessment_false",
                 self_assessment={"objective_met": "false", "rationale": "no"})
    assert trr.failure_reason_of(b) == "self_assessment objective_met=false: no"
    assert trr.failure_reason_of(Artifact(summary="ok")) is None


def test_listing_is_newest_first_and_card_scoped(proj):
    runs = proj["runs"]
    r_old = runs.create(TaskRunCreate(card_id=proj["card"].id, source_conversation_id=None))
    other = proj["cards"].create(TaskCardCreate(
        name="Other", root=Block(block_type="task", id="o", instructions="z")))
    r_other = runs.create(TaskRunCreate(card_id=other.id, source_conversation_id=None))
    r_new = _failed_run(proj)

    rows = trr.list_runs(runs, card_id=proj["card"].id)
    assert [r["run_id"] for r in rows] == [r_new.id, r_old.id]
    assert rows[0]["failure_reason"].startswith("sequence stopped")
    all_rows = trr.list_runs(runs, limit=2)
    assert len(all_rows) == 2 and r_other.id in {r["run_id"] for r in trr.list_runs(runs)}


def test_card_resolves_by_id_or_name(proj):
    cards = proj["cards"]
    cid = proj["card"].id
    assert trr.resolve_card_id(cards, cid) == cid
    assert trr.resolve_card_id(cards, "nightly AUDIT") == cid
    assert trr.resolve_card_id(cards, "no such card") is None


def test_schedule_state_with_skips_is_included(proj):
    from app.agents import task_scheduler as ts
    run = _failed_run(proj)
    ts._write_state(proj["project"].id, {proj["card"].id: {
        "block_id": "s1", "fires_so_far": 3, "next_fire_at": 1, "run_ids": [run.id],
        "skips_so_far": 1,
        "skips": [{"at": 5, "live_run_id": "abc", "reason": "skipped: run abc still live"}],
    }})
    out = trr.summarize_run(proj["runs"], run, project_id=proj["project"].id)
    assert out["schedule"]["skips_so_far"] == 1
    assert "still live" in out["schedule"]["skips"][0]["reason"]
    # A card that was never scheduled has no schedule section at all.
    plain = trr.summarize_run(proj["runs"], run, project_id="unknown-project")
    assert "schedule" not in plain
