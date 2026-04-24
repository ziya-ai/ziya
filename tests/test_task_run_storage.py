"""Tests for task run storage."""

import pytest
from pathlib import Path

from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.models.task_card import Artifact, ArtifactPart
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


class TestRunCRUD:
    def test_create_and_get(self, storage):
        run = storage.create(TaskRunCreate(card_id="card-1"))
        assert run.id
        assert run.card_id == "card-1"
        assert run.status == "queued"

        retrieved = storage.get(run.id)
        assert retrieved is not None
        assert retrieved.id == run.id

    def test_get_missing(self, storage):
        assert storage.get("nope") is None

    def test_list_filters_by_card(self, storage):
        storage.create(TaskRunCreate(card_id="a"))
        storage.create(TaskRunCreate(card_id="a"))
        storage.create(TaskRunCreate(card_id="b"))
        a_runs = storage.list(card_id="a")
        b_runs = storage.list(card_id="b")
        assert len(a_runs) == 2
        assert len(b_runs) == 1

    def test_update_status_transitions(self, storage):
        run = storage.create(TaskRunCreate(card_id="x"))
        running = storage.update_status(run.id, "running")
        assert running.status == "running"
        assert running.started_at is not None

        done = storage.update_status(run.id, "done")
        assert done.status == "done"
        assert done.completed_at is not None

    def test_update_status_with_error(self, storage):
        run = storage.create(TaskRunCreate(card_id="x"))
        failed = storage.update_status(run.id, "failed", error="bad things")
        assert failed.status == "failed"
        assert failed.error == "bad things"

    def test_set_artifact(self, storage):
        run = storage.create(TaskRunCreate(card_id="x"))
        artifact = Artifact(summary="done", tokens=1234, tool_calls=5)
        updated = storage.set_artifact(run.id, artifact)
        assert updated.artifact.summary == "done"
        assert updated.artifact.tokens == 1234

    def test_set_block_state(self, storage):
        run = storage.create(TaskRunCreate(card_id="x"))
        state = TaskRunBlockState(
            block_id="b-1",
            block_type="task",
            status="done",
            started_at=100.0, completed_at=110.0,
        )
        updated = storage.set_block_state(run.id, state)
        assert "b-1" in updated.block_states
        assert updated.block_states["b-1"].status == "done"

    def test_delete(self, storage):
        run = storage.create(TaskRunCreate(card_id="x"))
        assert storage.delete(run.id) is True
        assert storage.get(run.id) is None

    def test_delete_missing(self, storage):
        assert storage.delete("nope") is False
