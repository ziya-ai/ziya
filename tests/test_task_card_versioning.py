"""Tests for task card definition versioning.

Covers TaskCardStorage.update's version-bump policy: a monotonic
``version`` that increments ONLY when the block tree or scope changes
(a behaviour change that also invalidates signing), and stays put on
metadata-only edits, identical re-saves, run bookkeeping, and
duplication.  See design/task-card-versioning-and-resign.md.
"""

import pytest

from app.models.task_card import (
    Block, TaskScope, TaskCardCreate, TaskCardUpdate,
)
from app.storage.task_cards import TaskCardStorage


def _simple_task(name: str = "leaf", instructions: str = "do something") -> Block:
    return Block(
        block_type="task",
        name=name,
        instructions=instructions,
        scope=TaskScope(tools=["render_diagram"]),
    )


@pytest.fixture
def storage(tmp_path):
    return TaskCardStorage(tmp_path)


class TestVersionBump:
    def test_new_card_starts_at_version_1(self, storage):
        card = storage.create(TaskCardCreate(
            name="V", root=_simple_task(),
        ))
        assert card.version == 1

    def test_metadata_only_edit_does_not_bump(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        updated = storage.update(card.id, TaskCardUpdate(
            name="V renamed", description="new blurb",
        ))
        assert updated is not None
        # A rename is not a behaviour change: version — and any signature
        # keyed to it — must survive untouched.
        assert updated.version == 1
        assert storage.get(card.id).version == 1

    def test_tags_and_template_edit_does_not_bump(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        updated = storage.update(card.id, TaskCardUpdate(
            tags=["proposed"], is_template=True,
        ))
        assert updated.version == 1

    def test_root_change_bumps(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        # A genuine tree change: different instructions on the root task.
        updated = storage.update(card.id, TaskCardUpdate(
            root=_simple_task(instructions="do something ELSE"),
        ))
        assert updated.version == 2
        assert storage.get(card.id).version == 2

    def test_scope_change_bumps(self, storage):
        # Created with no card-level scope; adding one is exactly the
        # writable-path widening that forces a re-sign.
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        assert card.scope is None
        updated = storage.update(card.id, TaskCardUpdate(
            scope=TaskScope(tools=["render_diagram", "file_write"]),
        ))
        assert updated.version == 2

    def test_identical_resave_does_not_bump(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        # Re-submit the exact same tree the card already holds.
        same_root = storage.get(card.id).root
        updated = storage.update(card.id, TaskCardUpdate(root=same_root))
        assert updated.version == 1

    def test_successive_changes_are_monotonic(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        storage.update(card.id, TaskCardUpdate(
            root=_simple_task(instructions="change 1")))
        storage.update(card.id, TaskCardUpdate(
            scope=TaskScope(tools=["x"])))
        final = storage.update(card.id, TaskCardUpdate(
            root=_simple_task(instructions="change 2")))
        assert final.version == 4

    def test_record_run_does_not_bump(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        after = storage.record_run(card.id)
        assert after.run_count == 1
        # A run is not an edit: bookkeeping must not touch version.
        assert after.version == 1

    def test_duplicate_starts_fresh_at_1(self, storage):
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        storage.update(card.id, TaskCardUpdate(
            root=_simple_task(instructions="bumped")))
        assert storage.get(card.id).version == 2
        # A duplicate is a new card id / independent copy — its own history
        # begins at v1 rather than inheriting the source's counter.
        dup = storage.duplicate(card.id)
        assert dup.version == 1
        assert dup.id != card.id

    def test_legacy_card_without_version_loads_as_1(self, storage):
        """A card file predating the field deserializes to v1, and its
        first structural edit advances to v2 (not to some absent/None)."""
        card = storage.create(TaskCardCreate(name="V", root=_simple_task()))
        # Simulate an on-disk card written before ``version`` existed by
        # stripping the key and rewriting the raw file.
        raw = storage._read_json(storage._card_file(card.id))
        raw.pop("version", None)
        storage._write_json(storage._card_file(card.id), raw)

        loaded = storage.get(card.id)
        assert loaded.version == 1
        bumped = storage.update(card.id, TaskCardUpdate(
            root=_simple_task(instructions="first edit")))
        assert bumped.version == 2
