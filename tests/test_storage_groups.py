"""
Tests for app.storage.groups — ChatGroupStorage (folder/group management).

Covers:
  - CRUD operations
  - Atomic writes (no leftover .tmp files)
  - Reorder
  - Groups file initialization
  - Corrupt groups file recovery
"""

import json
import time
from pathlib import Path

import pytest

from app.models.group import ChatGroupCreate, ChatGroupUpdate
from app.storage.groups import ChatGroupStorage


@pytest.fixture
def storage(tmp_path):
    """Create a ChatGroupStorage backed by a temp directory."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return ChatGroupStorage(project_dir)


# ── Create ─────────────────────────────────────────────────────────

class TestCreate:

    def test_create_returns_group(self, storage):
        group = storage.create(ChatGroupCreate(name="Frontend"))
        assert group.id
        assert group.name == "Frontend"
        assert group.order == 0  # First group
        assert group.createdAt > 0

    def test_create_increments_order(self, storage):
        g1 = storage.create(ChatGroupCreate(name="First"))
        g2 = storage.create(ChatGroupCreate(name="Second"))
        assert g2.order == g1.order + 1

    def test_create_with_defaults(self, storage):
        group = storage.create(ChatGroupCreate(
            name="With Defaults",
            defaultContextIds=["ctx-1"],
            defaultSkillIds=["skill-1"],
        ))
        assert group.defaultContextIds == ["ctx-1"]
        assert group.defaultSkillIds == ["skill-1"]

    def test_create_persists_to_disk(self, storage):
        storage.create(ChatGroupCreate(name="Persisted"))
        groups_data = json.loads(storage.groups_file.read_text())
        assert len(groups_data["groups"]) == 1
        assert groups_data["groups"][0]["name"] == "Persisted"


# ── Get ────────────────────────────────────────────────────────────

class TestGet:

    def test_get_existing(self, storage):
        created = storage.create(ChatGroupCreate(name="Test"))
        retrieved = storage.get(created.id)
        assert retrieved is not None
        assert retrieved.name == "Test"

    def test_get_nonexistent(self, storage):
        assert storage.get("nonexistent") is None


# ── List ───────────────────────────────────────────────────────────

class TestList:

    def test_list_sorted_by_order(self, storage):
        storage.create(ChatGroupCreate(name="A"))
        storage.create(ChatGroupCreate(name="B"))
        storage.create(ChatGroupCreate(name="C"))
        groups = storage.list()
        assert [g.name for g in groups] == ["A", "B", "C"]

    def test_list_empty(self, storage):
        assert storage.list() == []


# ── Update ─────────────────────────────────────────────────────────

class TestUpdate:

    def test_update_name(self, storage):
        group = storage.create(ChatGroupCreate(name="Old"))
        updated = storage.update(group.id, ChatGroupUpdate(name="New"))
        assert updated.name == "New"

    def test_update_preserves_other_fields(self, storage):
        group = storage.create(ChatGroupCreate(
            name="Keep",
            defaultContextIds=["ctx-1"],
        ))
        updated = storage.update(group.id, ChatGroupUpdate(name="Changed"))
        assert updated.defaultContextIds == ["ctx-1"]

    def test_update_nonexistent_returns_none(self, storage):
        assert storage.update("missing", ChatGroupUpdate(name="X")) is None


# ── Delete ─────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, storage):
        group = storage.create(ChatGroupCreate(name="Doomed"))
        assert storage.delete(group.id)
        assert storage.get(group.id) is None
        assert len(storage.list()) == 0

    def test_delete_nonexistent(self, storage):
        assert not storage.delete("nonexistent")


# ── Reorder ────────────────────────────────────────────────────────

class TestReorder:

    def test_reorder_changes_order(self, storage):
        g1 = storage.create(ChatGroupCreate(name="A"))
        g2 = storage.create(ChatGroupCreate(name="B"))
        g3 = storage.create(ChatGroupCreate(name="C"))

        # Reverse order
        result = storage.reorder([g3.id, g1.id, g2.id])
        assert [g.name for g in result] == ["C", "A", "B"]
        assert result[0].order == 0
        assert result[1].order == 1
        assert result[2].order == 2

    def test_reorder_persists(self, storage):
        g1 = storage.create(ChatGroupCreate(name="A"))
        g2 = storage.create(ChatGroupCreate(name="B"))
        storage.reorder([g2.id, g1.id])

        # Reload from disk
        reloaded = storage.list()
        assert [g.name for g in reloaded] == ["B", "A"]


# ── Edge cases ─────────────────────────────────────────────────────

class TestEdgeCases:

    def test_no_groups_file_returns_empty(self, storage):
        """When _groups.json doesn't exist, list returns empty."""
        if storage.groups_file.exists():
            storage.groups_file.unlink()
        assert storage.list() == []

    def test_corrupt_groups_file_returns_empty(self, storage):
        """Corrupt _groups.json should not crash."""
        storage.groups_file.write_text("not valid json!!!")
        assert storage.list() == []

    def test_atomic_write_no_tmp_leftover(self, storage):
        storage.create(ChatGroupCreate(name="Test"))
        tmp_file = storage.groups_file.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_chats_dir_created_on_init(self, tmp_path):
        """ChatGroupStorage should create the chats directory if missing."""
        project_dir = tmp_path / "new-project"
        # Don't pre-create the directory
        storage = ChatGroupStorage(project_dir)
        assert (project_dir / "chats").is_dir()
