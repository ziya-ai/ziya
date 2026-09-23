"""Operator steer notes for running Task Card blocks.

Covers the three hops a note crosses and asserts the SEAM between them:

* ``task_steer`` — target resolution and the feedback-list key.
* ``TaskRunStorage`` — queued → delivered / expired bookkeeping.
* ``POST /task-runs/{id}/steer`` — enqueues on the SAME key
  ``execute_task_block`` hands to ``stream_with_tools(feedback_key=…)``.
  That equality is the whole feature: if either side drifts, the note is
  recorded as queued and never read.

The executor's delivery machinery (drain at the top of a tool round,
inject as ``[User feedback]``, yield ``feedback_delivered``) predates
this feature and is covered by tests/test_feedback_delivery_paths.py;
here we only check that a task run reaches it through a per-block key.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.project import ProjectCreate
from app.models.task_run import TaskRunCreate
from app.storage.projects import ProjectStorage
from app.storage.task_runs import TaskRunStorage
from app.utils.task_steer import (
    MAX_STEER_NOTES, new_steer_note, next_queued, queued_for,
    resolve_steer_target, steer_key, trim_notes,
)

# A card with a bare task, a serial loop, and a called card's block.
SNAPSHOT = {
    "id": "root", "block_type": "group", "body": [
        {"id": "t1", "block_type": "task", "name": "inventory"},
        {"id": "loop", "block_type": "repeat", "name": "migrate",
         "repeat_mode": "count", "repeat_count": 3, "body": [
             {"id": "t2", "block_type": "task", "name": "one file"},
         ]},
        {"id": "c1", "block_type": "call", "call_target": "other"},
    ],
}
# Shape recorded by block_executor._record_call_audit: the callee tree is
# under "root", keyed by the Call block's id.
CALL_SNAPSHOTS = {
    "c1": {"root": {"id": "callee-root", "block_type": "group", "body": [
        {"id": "ct", "block_type": "task", "name": "callee task"},
    ]}},
}


# ── task_steer helpers ────────────────────────────────────────────────

class TestSteerKey:
    def test_index_none_is_distinct_from_zero(self):
        assert steer_key("r", "b", None) != steer_key("r", "b", 0)

    def test_stable_and_scoped_to_run(self):
        assert steer_key("r1", "b", 2) == "steer:r1:b:2"
        assert steer_key("r1", "b", 2) != steer_key("r2", "b", 2)


class TestResolveSteerTarget:
    def test_bare_task_discards_frontend_synthetic_index(self):
        # The tile always sends the bucket index (0 for a bare task).
        # Keeping it would enqueue under a key nobody listens on.
        target, err = resolve_steer_target(SNAPSHOT, None, "t1", 0)
        assert err is None
        assert target == ("t1", None)

    def test_loop_requires_index(self):
        target, err = resolve_steer_target(SNAPSHOT, None, "loop", None)
        assert target is None and "index is required" in err
        target, err = resolve_steer_target(SNAPSHOT, None, "loop", 2)
        assert target == ("loop", 2)

    def test_negative_index_refused(self):
        target, err = resolve_steer_target(SNAPSHOT, None, "loop", -1)
        assert target is None and err

    def test_unknown_block(self):
        target, err = resolve_steer_target(SNAPSHOT, None, "nope", None)
        assert target is None and "not in this run" in err

    def test_callee_block_resolves_through_call_snapshots(self):
        target, err = resolve_steer_target(SNAPSHOT, CALL_SNAPSHOTS, "ct", 0)
        assert err is None and target == ("ct", None)

    def test_no_snapshot(self):
        target, err = resolve_steer_target(None, None, "t1", None)
        assert target is None and "card_snapshot" in err


class TestNoteBookkeeping:
    def test_fifo_per_target(self):
        a = new_steer_note("b", 1, "first", created_at=1)
        b = new_steer_note("b", 1, "second", created_at=2)
        c = new_steer_note("b", 2, "other iteration", created_at=3)
        notes = [a, b, c]
        assert next_queued(notes, "b", 1) is a
        a["status"] = "delivered"
        assert next_queued(notes, "b", 1) is b
        assert [n["text"] for n in queued_for(notes, "b", 2)] == ["other iteration"]
        assert next_queued(notes, "b", None) is None

    def test_trim_keeps_queued_over_settled(self):
        notes = [new_steer_note("b", None, f"n{i}", created_at=i)
                 for i in range(MAX_STEER_NOTES + 5)]
        for n in notes[:-1]:
            n["status"] = "delivered"
        out = trim_notes(notes)
        assert len(out) == MAX_STEER_NOTES
        assert out[-1]["status"] == "queued"
        # Oldest settled ones dropped first.
        assert out[0]["text"] == "n5"


# ── storage ───────────────────────────────────────────────────────────

@pytest.fixture
def tmp_ziya_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def project_id(tmp_ziya_home):
    storage = ProjectStorage(tmp_ziya_home)
    return storage.create(
        ProjectCreate(name="steer-project", path=str(tmp_ziya_home))).id


@pytest.fixture
def run_storage(tmp_ziya_home, project_id):
    from app.utils.paths import get_project_dir
    return TaskRunStorage(get_project_dir(project_id))


@pytest.fixture
def live_run(run_storage):
    run = run_storage.create(TaskRunCreate(card_id="card-1"))
    run_storage.set_card_snapshot(run.id, SNAPSHOT)
    run_storage.update_status(run.id, "running")
    return run_storage.get(run.id)


class TestStorage:
    def test_enqueue_deliver_expire_lifecycle(self, run_storage, live_run):
        rid = live_run.id
        n1 = run_storage.enqueue_steer(rid, "loop", 1, "first")
        n2 = run_storage.enqueue_steer(rid, "loop", 1, "second", hold=True)
        assert n1["status"] == "queued" and n2["hold"] is True
        run = run_storage.get(rid)
        assert [n["text"] for n in run.steer_notes] == ["first", "second"]

        delivered = run_storage.mark_steer_delivered(rid, "loop", 1)
        assert delivered["id"] == n1["id"]
        assert delivered["delivered_at"] is not None
        # Persisted, not just returned.
        assert run_storage.get(rid).steer_notes[0]["status"] == "delivered"

        expired = run_storage.expire_steers(rid, "loop", 1)
        assert [n["id"] for n in expired] == [n2["id"]]
        statuses = [n["status"] for n in run_storage.get(rid).steer_notes]
        assert statuses == ["delivered", "expired"]

    def test_delivery_does_not_cross_iterations(self, run_storage, live_run):
        rid = live_run.id
        run_storage.enqueue_steer(rid, "loop", 0, "for zero")
        assert run_storage.mark_steer_delivered(rid, "loop", 1) is None
        assert run_storage.expire_steers(rid, "loop", 1) == []
        assert run_storage.get(rid).steer_notes[0]["status"] == "queued"

    def test_unknown_run(self, run_storage):
        assert run_storage.enqueue_steer("nope", "b", None, "x") is None
        assert run_storage.mark_steer_delivered("nope", "b", None) is None
        assert run_storage.expire_steers("nope", "b", None) == []


# ── API ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_ziya_home):
    from app.api.task_runs import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _steer_url(project_id, run_id):
    return f"/api/v1/projects/{project_id}/task-runs/{run_id}/steer"


class TestSteerEndpoint:
    def test_enqueues_on_the_key_the_executor_listens_on(
        self, client, project_id, run_storage, live_run,
    ):
        """The seam.  The executor binds its feedback list to
        ``steer_key(run_id, delta_block_id, delta_index)``; the endpoint
        must enqueue under exactly that key after server-side target
        resolution — here the tile's synthetic index 0 for a bare task
        must become None."""
        captured = []
        pushed = []

        async def fake_push(run_id, evt):
            pushed.append(evt)

        with patch("app.server._enqueue_feedback",
                   side_effect=lambda k, item: captured.append((k, item))), \
             patch("app.agents.task_run_stream_relay.safe_push", fake_push):
            res = client.post(_steer_url(project_id, live_run.id), json={
                "block_id": "t1", "index": 0, "text": "  use the seal test  ",
            })
        assert res.status_code == 200, res.text
        assert len(captured) == 1
        key, item = captured[0]
        assert key == steer_key(live_run.id, "t1", None)
        assert item["type"] == "tool_feedback"
        assert item["message"] == "use the seal test"

        body = res.json()
        assert body["steer_notes"][0]["status"] == "queued"
        assert body["steer_notes"][0]["index"] is None
        assert item["feedback_id"] == body["steer_notes"][0]["id"]
        assert [e["type"] for e in pushed] == ["task_steer_queued"]
        assert pushed[0]["block_id"] == "t1"

    def test_hold_grants_a_step_credit(
        self, client, project_id, run_storage, live_run,
    ):
        with patch("app.server._enqueue_feedback"), \
             patch("app.agents.task_run_stream_relay.safe_push"):
            res = client.post(_steer_url(project_id, live_run.id), json={
                "block_id": "loop", "index": 2, "text": "stop after this",
                "hold": True,
            })
        assert res.status_code == 200, res.text
        run = res.json()
        assert run["pause_requested"] is True
        assert run["step_budget"] == 1
        assert run["steer_notes"][0]["hold"] is True

    def test_plain_send_does_not_hold(
        self, client, project_id, run_storage, live_run,
    ):
        with patch("app.server._enqueue_feedback"), \
             patch("app.agents.task_run_stream_relay.safe_push"):
            res = client.post(_steer_url(project_id, live_run.id), json={
                "block_id": "loop", "index": 2, "text": "carry on",
            })
        assert res.status_code == 200
        assert res.json()["pause_requested"] is False

    def test_loop_without_index_422(self, client, project_id, live_run):
        with patch("app.server._enqueue_feedback") as enq:
            res = client.post(_steer_url(project_id, live_run.id), json={
                "block_id": "loop", "text": "hi",
            })
        assert res.status_code == 422
        enq.assert_not_called()

    def test_unknown_block_404(self, client, project_id, live_run):
        res = client.post(_steer_url(project_id, live_run.id), json={
            "block_id": "nope", "text": "hi",
        })
        assert res.status_code == 404

    def test_empty_text_422(self, client, project_id, live_run):
        res = client.post(_steer_url(project_id, live_run.id), json={
            "block_id": "t1", "text": "   ",
        })
        assert res.status_code == 422

    def test_terminal_run_409(self, client, project_id, run_storage, live_run):
        run_storage.update_status(live_run.id, "done")
        with patch("app.server._enqueue_feedback") as enq:
            res = client.post(_steer_url(project_id, live_run.id), json={
                "block_id": "t1", "text": "too late",
            })
        assert res.status_code == 409
        enq.assert_not_called()
        assert run_storage.get(live_run.id).steer_notes == []

    def test_unknown_run_404(self, client, project_id):
        res = client.post(_steer_url(project_id, "nope"), json={
            "block_id": "t1", "text": "hi",
        })
        assert res.status_code == 404


# ── executor seam ─────────────────────────────────────────────────────

class TestExecutorKey:
    def test_task_executor_passes_per_block_feedback_key(self, tmp_path):
        """``execute_task_block`` must hand ``stream_with_tools`` the same
        key the endpoint enqueues on, while keeping conversation_id=run_id
        for the context tools."""
        from app.agents import task_executor
        from app.models.task_card import Block

        seen = {}

        class FakeExecutor:
            model_config = {}

            def __init__(self, *a, **kw):
                pass

            async def stream_with_tools(self, messages, **kw):
                seen.update(kw)
                yield {"type": "text", "content": "done"}
                yield {"type": "stream_end"}

        block = Block(block_type="task", id="t1", name="x",
                      instructions="do the thing")
        with patch("app.streaming_tool_executor.StreamingToolExecutor",
                   FakeExecutor):
            asyncio.run(task_executor.execute_task_block(
                block, project_root=str(tmp_path), run_id="run-xyz"))
        assert seen.get("conversation_id") == "run-xyz"
        assert seen.get("feedback_key") == steer_key("run-xyz", "t1", None)
