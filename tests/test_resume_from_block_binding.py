"""
Tests for POST /task-runs/{run_id}/resume-from/{block_id} — specifically
the BINDING side effect.

Why this matters more than it looks: the GUI can only render a task run
through a TaskBinding, and no other endpoint binds an *existing* run
(POST /task-bindings launches its own; /{id}/launch 409s unless run_id is
null).  So without the binding created here, a resumed run executes
invisibly and is unrecoverable after a reload — nothing on disk ties it
to a chat.  These tests pin that the binding is created, carries the
source run's anchor, and that a binding failure never costs the run.
"""

import json
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import TaskRunCreate
from app.storage.task_bindings import TaskBindingStorage
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage

CHAT_ID = "chat-resume-1"


@pytest.fixture
def env(tmp_path):
    """A project dir with a card, and a finished run carrying a snapshot."""
    home = tmp_path / ".ziya"
    pid = "proj-resume"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Resume Test", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))

    root = Block(block_type="group", id="g", body=[
        Block(block_type="task", id="b1", name="First", instructions="one"),
        Block(block_type="task", id="b2", name="Second", instructions="two"),
    ])
    card = TaskCardStorage(pdir).create(
        TaskCardCreate(name="Card", root=root))

    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(
        card_id=card.id, source_conversation_id=CHAT_ID))
    runs.set_card_snapshot(src.id, {
        "name": "Card", "description": "", "root": root.model_dump(),
    })
    runs.update_status(src.id, "done")
    return home, pid, pdir, card.id, src.id


@pytest.fixture
def client(env):
    home, pid, pdir, card_id, src_id = env

    async def _stub_execute(block, ctx):
        return Artifact(summary="stub", created_at=time.time())

    with patch("app.api.task_runs.get_ziya_home", return_value=home), \
         patch("app.api.task_runs.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.get_ziya_home", return_value=home), \
         patch("app.api.task_cards.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.execute_block", new=_stub_execute):
        from app.api.task_runs import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), pid, pdir, card_id, src_id


def _resume(tc, pid, run_id, block_id):
    return tc.post(
        f"/api/v1/projects/{pid}/task-runs/{run_id}/resume-from/{block_id}")


def test_creates_binding_for_the_new_run(client):
    tc, pid, pdir, card_id, src_id = client
    res = _resume(tc, pid, src_id, "b2")
    assert res.status_code == 200, res.text
    body = res.json()

    # New run, not the source one.
    assert body["run"]["id"] and body["run"]["id"] != src_id
    assert body["binding"] is not None

    # And it is on disk — this is what survives a reload.
    stored = TaskBindingStorage(pdir).list_for_chat(CHAT_ID)
    assert [b.run_id for b in stored] == [body["run"]["id"]]
    assert stored[0].card_id == card_id


def test_source_run_is_left_untouched(client):
    tc, pid, pdir, _, src_id = client
    _resume(tc, pid, src_id, "b2")
    src = TaskRunStorage(pdir).get(src_id)
    assert src.status == "done"


def test_reuses_the_source_bindings_anchor(client):
    """The resumed tile should appear where the original did, not at the
    chat tail."""
    tc, pid, pdir, card_id, src_id = client
    TaskBindingStorage(pdir).create(
        chat_id=CHAT_ID, card_id=card_id, run_id=src_id,
        anchor_message_id="msg-7")

    res = _resume(tc, pid, src_id, "b2")
    assert res.json()["binding"]["anchor_message_id"] == "msg-7"


def test_unanchored_when_source_binding_is_absent(client):
    # No binding for the source run at all (e.g. launched from the deck
    # before bindings existed).  Unanchored is correct; guessing is not.
    tc, pid, _, _, src_id = client
    res = _resume(tc, pid, src_id, "b2")
    assert res.json()["binding"]["anchor_message_id"] is None


def test_no_source_chat_yields_run_without_binding(client, env):
    """A run with no source_conversation_id has no chat to bind to.  The
    run must still launch — losing it would be worse than it being
    invisible."""
    home, pid, pdir, card_id, _ = env
    runs = TaskRunStorage(pdir)
    orphan = runs.create(TaskRunCreate(card_id=card_id))
    runs.set_card_snapshot(orphan.id, {
        "name": "Card", "description": "",
        "root": Block(block_type="task", id="b1", instructions="x").model_dump(),
    })
    runs.update_status(orphan.id, "done")

    tc, pid, _, _, _ = client
    res = _resume(tc, pid, orphan.id, "b1")
    assert res.status_code == 200, res.text
    assert res.json()["run"]["id"]
    assert res.json()["binding"] is None


def test_binding_failure_does_not_lose_the_run(client):
    """The run is already executing by the time we bind, so a binding
    write failure must degrade to invisible-but-running, not a 500."""
    tc, pid, _, _, src_id = client
    with patch.object(
        TaskBindingStorage, "create", side_effect=OSError("disk full"),
    ):
        res = _resume(tc, pid, src_id, "b2")
    assert res.status_code == 200, res.text
    assert res.json()["run"]["id"]
    assert res.json()["binding"] is None


def test_anchor_lookup_failure_still_binds(client):
    """A failed anchor lookup must cost the anchor, not the binding.

    Patches ``_source_anchor`` rather than ``TaskBindingStorage``.  A
    class-level patch of ``list_for_chat`` cannot express this scenario:
    ``create()`` calls that same method to read-append-write (see
    app/storage/task_bindings.py), so breaking it breaks the very binding
    the test asserts survives — unachievable by construction, not by a
    defect.  A corrupt FILE is also the wrong simulation, since
    ``list_for_chat`` skips unparseable rows and yields ``[]``, after
    which both the lookup and the create succeed.

    What this pins is that the anchor is resolved BEFORE the create call
    rather than inline as an argument: inline, an escape skips create()
    entirely and the run becomes unrenderable over a cosmetic failure.
    """
    tc, pid, _, _, src_id = client
    with patch(
        "app.api.task_runs._source_anchor", side_effect=OSError("boom"),
    ):
        res = _resume(tc, pid, src_id, "b2")
    assert res.status_code == 200, res.text
    binding = res.json()["binding"]
    # The binding survives; only the anchor is lost, so the tile renders
    # at the chat tail rather than not at all.
    assert binding is not None, (
        "a cosmetic anchor failure must not cost the binding — the run "
        "would be invisible despite executing"
    )
    assert binding["anchor_message_id"] is None


# ── guards (unchanged behaviour, pinned because the response shape moved) ──

def test_unknown_run_is_404(client):
    tc, pid, _, _, _ = client
    assert _resume(tc, pid, "nope", "b1").status_code == 404


def test_unknown_block_is_404_and_binds_nothing(client):
    tc, pid, pdir, _, src_id = client
    assert _resume(tc, pid, src_id, "not-a-block").status_code == 404
    assert TaskBindingStorage(pdir).list_for_chat(CHAT_ID) == []


def test_running_source_is_409_and_binds_nothing(client, env):
    home, pid, pdir, card_id, src_id = env
    TaskRunStorage(pdir).update_status(src_id, "running")
    tc, pid, _, _, _ = client
    assert _resume(tc, pid, src_id, "b2").status_code == 409
    assert TaskBindingStorage(pdir).list_for_chat(CHAT_ID) == []


def test_missing_snapshot_is_422(client, env):
    home, pid, pdir, card_id, _ = env
    runs = TaskRunStorage(pdir)
    old = runs.create(TaskRunCreate(
        card_id=card_id, source_conversation_id=CHAT_ID))
    runs.update_status(old.id, "done")
    tc, pid, _, _, _ = client
    assert _resume(tc, pid, old.id, "b2").status_code == 422
