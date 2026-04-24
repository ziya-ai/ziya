"""Tests for task card storage."""

import pytest
from pathlib import Path

from app.models.task_card import (
    Block, TaskScope, TaskCardCreate, TaskCardUpdate,
)
from app.storage.task_cards import TaskCardStorage


def _simple_task(name: str = "leaf") -> Block:
    return Block(
        block_type="task",
        name=name,
        instructions="do something",
        scope=TaskScope(tools=["render_diagram"]),
    )


def _loop(body=None) -> Block:
    return Block(
        block_type="repeat",
        name="loop",
        repeat_mode="count",
        repeat_count=3,
        repeat_parallel=True,
        body=body or [_simple_task()],
    )


@pytest.fixture
def storage(tmp_path):
    return TaskCardStorage(tmp_path)


class TestCRUD:
    def test_create_and_get(self, storage):
        card = storage.create(TaskCardCreate(
            name="Test Card",
            description="A test",
            root=_simple_task("Spec Gen"),
        ))
        assert card.id
        assert card.name == "Test Card"
        assert card.created_at > 0

        retrieved = storage.get(card.id)
        assert retrieved is not None
        assert retrieved.name == "Test Card"
        assert retrieved.root.name == "Spec Gen"

    def test_get_missing(self, storage):
        assert storage.get("nonexistent") is None

    def test_list_empty(self, storage):
        assert storage.list() == []

    def test_list_returns_all(self, storage):
        storage.create(TaskCardCreate(name="Card A", root=_simple_task()))
        storage.create(TaskCardCreate(name="Card B", root=_simple_task()))
        assert len(storage.list()) == 2

    def test_list_templates_only(self, storage):
        storage.create(TaskCardCreate(name="Regular", root=_simple_task()))
        storage.create(TaskCardCreate(
            name="Template", root=_simple_task(), is_template=True,
        ))
        templates = storage.list(templates_only=True)
        assert len(templates) == 1
        assert templates[0].name == "Template"

    def test_update(self, storage):
        card = storage.create(TaskCardCreate(
            name="Original", root=_simple_task(),
        ))
        updated = storage.update(card.id, TaskCardUpdate(name="Renamed"))
        assert updated.name == "Renamed"
        assert updated.updated_at >= card.updated_at

    def test_update_preserves_unspecified_fields(self, storage):
        card = storage.create(TaskCardCreate(
            name="X", description="keep", root=_simple_task(),
        ))
        updated = storage.update(card.id, TaskCardUpdate(name="Y"))
        assert updated.description == "keep"

    def test_update_root_reassigns_block_ids(self, storage):
        card = storage.create(TaskCardCreate(name="X", root=_simple_task()))
        new_root = _simple_task("leaf2")
        updated = storage.update(card.id, TaskCardUpdate(root=new_root))
        assert updated.root.name == "leaf2"
        assert updated.root.id  # IDs assigned

    def test_update_nonexistent(self, storage):
        assert storage.update("nope", TaskCardUpdate(name="X")) is None

    def test_delete(self, storage):
        card = storage.create(TaskCardCreate(name="Doomed", root=_simple_task()))
        assert storage.delete(card.id) is True
        assert storage.get(card.id) is None

    def test_delete_nonexistent(self, storage):
        assert storage.delete("nope") is False


class TestDuplicate:
    def test_duplicate_preserves_tree(self, storage):
        original = storage.create(TaskCardCreate(
            name="Original", root=_loop(), tags=["x"],
        ))
        clone = storage.duplicate(original.id)
        assert clone.name == "Original (copy)"
        assert clone.id != original.id
        assert clone.root.repeat_count == 3
        assert clone.tags == ["x"]

    def test_duplicate_as_template(self, storage):
        card = storage.create(TaskCardCreate(name="Task", root=_simple_task()))
        t = storage.duplicate(card.id, as_template=True)
        assert t.is_template is True

    def test_duplicate_nonexistent(self, storage):
        assert storage.duplicate("nope") is None


class TestBlockIdAssignment:
    def test_nested_blocks_all_get_ids(self, storage):
        tree = _loop(body=[_simple_task("a"), _loop(body=[_simple_task("b")])])
        card = storage.create(TaskCardCreate(name="nested", root=tree))
        assert card.root.id
        assert card.root.body[0].id
        assert card.root.body[1].id
        assert card.root.body[1].body[0].id

    def test_existing_ids_preserved(self, storage):
        tree = _simple_task("keep")
        tree.id = "preexisting-id"
        card = storage.create(TaskCardCreate(name="keep", root=tree))
        assert card.root.id == "preexisting-id"


class TestRunRecording:
    def test_record_run_bumps_counters(self, storage):
        card = storage.create(TaskCardCreate(name="X", root=_simple_task()))
        assert card.run_count == 0
        assert card.last_run_at is None
        updated = storage.record_run(card.id)
        assert updated.run_count == 1
        assert updated.last_run_at is not None

    def test_record_run_nonexistent(self, storage):
        assert storage.record_run("nope") is None
