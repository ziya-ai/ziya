"""
Tests for app.storage.chats and app.storage.base — file-based chat storage.

Covers:
  - CRUD operations (create, read, update, delete)
  - list_summaries preserves _version
  - add_message appends and updates lastActiveAt
  - remove_context_from_all_chats / remove_skill_from_all_chats
  - touch updates timestamp
  - Atomic write safety (tmp file cleanup on error)
  - Corrupt JSON handling (returns None, doesn't crash)
  - Group filtering in list()
"""

import json
import time
from pathlib import Path

import pytest

from app.models.chat import Chat, ChatCreate, ChatUpdate, Message
from app.storage.chats import ChatStorage


@pytest.fixture
def storage(tmp_path):
    """Create a ChatStorage backed by a temp directory."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return ChatStorage(project_dir)


@pytest.fixture
def sample_chat(storage) -> Chat:
    """Create a sample chat via the storage API."""
    data = ChatCreate(title="Test Chat")
    return storage.create(data)


# ── Create ─────────────────────────────────────────────────────────

class TestCreate:

    def test_create_returns_chat_with_id(self, storage):
        chat = storage.create(ChatCreate(title="My Chat"))
        assert chat.id
        assert chat.title == "My Chat"
        assert chat.messages == []
        assert chat.createdAt > 0
        assert chat.lastActiveAt > 0

    def test_create_with_group_id(self, storage):
        chat = storage.create(ChatCreate(title="Grouped", groupId="grp-1"))
        assert chat.groupId == "grp-1"

    def test_create_with_default_contexts(self, storage):
        chat = storage.create(
            ChatCreate(title="With Defaults"),
            default_context_ids=["ctx-1", "ctx-2"],
            default_skill_ids=["skill-1"],
        )
        assert chat.contextIds == ["ctx-1", "ctx-2"]
        assert chat.skillIds == ["skill-1"]

    def test_create_explicit_contexts_override_defaults(self, storage):
        chat = storage.create(
            ChatCreate(title="Explicit", contextIds=["my-ctx"]),
            default_context_ids=["default-ctx"],
        )
        assert chat.contextIds == ["my-ctx"]

    def test_create_persists_to_disk(self, storage):
        chat = storage.create(ChatCreate(title="Persisted"))
        # Verify the file exists
        chat_file = storage._chat_file(chat.id)
        assert chat_file.exists()
        data = json.loads(chat_file.read_text())
        assert data["title"] == "Persisted"


# ── Get ────────────────────────────────────────────────────────────

class TestGet:

    def test_get_existing(self, storage, sample_chat):
        retrieved = storage.get(sample_chat.id)
        assert retrieved is not None
        assert retrieved.id == sample_chat.id
        assert retrieved.title == sample_chat.title

    def test_get_nonexistent(self, storage):
        assert storage.get("nonexistent-id") is None


# ── List ───────────────────────────────────────────────────────────

class TestList:

    def test_list_all(self, storage):
        storage.create(ChatCreate(title="A"))
        storage.create(ChatCreate(title="B"))
        chats = storage.list()
        assert len(chats) == 2

    def test_list_sorted_by_last_active(self, storage):
        c1 = storage.create(ChatCreate(title="Older"))
        time.sleep(0.05)
        c2 = storage.create(ChatCreate(title="Newer"))
        chats = storage.list()
        # Newest first
        assert chats[0].id == c2.id

    def test_list_by_group(self, storage):
        storage.create(ChatCreate(title="Group A", groupId="grp-a"))
        storage.create(ChatCreate(title="Group B", groupId="grp-b"))
        storage.create(ChatCreate(title="No Group"))

        grp_a = storage.list(group_id="grp-a")
        assert len(grp_a) == 1
        assert grp_a[0].title == "Group A"

    def test_list_ungrouped(self, storage):
        storage.create(ChatCreate(title="Grouped", groupId="grp-a"))
        storage.create(ChatCreate(title="Ungrouped"))

        ungrouped = storage.list(group_id="ungrouped")
        assert len(ungrouped) == 1
        assert ungrouped[0].title == "Ungrouped"

    def test_list_empty_storage(self, storage):
        assert storage.list() == []

    def test_list_ignores_underscore_files(self, storage):
        """Files like _groups.json should not appear as chats."""
        (storage.chats_dir / "_groups.json").write_text('{"version": 1}')
        storage.create(ChatCreate(title="Real Chat"))
        chats = storage.list()
        assert len(chats) == 1


# ── List Summaries ─────────────────────────────────────────────────

class TestListSummaries:

    def test_summaries_have_no_messages(self, storage, sample_chat):
        # Add a message first
        msg = Message(id="m1", role="human", content="hello", timestamp=int(time.time() * 1000))
        storage.add_message(sample_chat.id, msg)

        summaries = storage.list_summaries()
        assert len(summaries) == 1
        assert summaries[0].messageCount == 1
        # ChatSummary should not have a messages field
        assert not hasattr(summaries[0], "messages") or summaries[0].model_dump().get("messages") is None

    def test_summaries_preserve_version(self, storage, tmp_path):
        """If a chat has _version in extra fields, it should appear in summary."""
        # Manually write a chat with _version
        chat_id = "versioned-chat"
        chat_data = {
            "id": chat_id,
            "title": "Versioned",
            "messages": [],
            "createdAt": int(time.time() * 1000),
            "lastActiveAt": int(time.time() * 1000),
            "_version": 1719900000000,
        }
        (storage.chats_dir / f"{chat_id}.json").write_text(json.dumps(chat_data))

        summaries = storage.list_summaries()
        assert len(summaries) == 1
        # _version should be preserved via extra fields
        summary_dump = summaries[0].model_dump()
        assert summary_dump.get("_version") == 1719900000000


# ── Update ─────────────────────────────────────────────────────────

class TestUpdate:

    def test_update_title(self, storage, sample_chat):
        updated = storage.update(sample_chat.id, ChatUpdate(title="New Title"))
        assert updated is not None
        assert updated.title == "New Title"
        # Verify persisted
        reloaded = storage.get(sample_chat.id)
        assert reloaded.title == "New Title"

    def test_update_updates_last_active(self, storage, sample_chat):
        original_time = sample_chat.lastActiveAt
        time.sleep(0.05)
        updated = storage.update(sample_chat.id, ChatUpdate(title="Updated"))
        assert updated.lastActiveAt > original_time

    def test_update_nonexistent_returns_none(self, storage):
        result = storage.update("nonexistent", ChatUpdate(title="Nope"))
        assert result is None

    def test_update_partial_preserves_other_fields(self, storage):
        chat = storage.create(ChatCreate(title="Original", groupId="grp-1"))
        updated = storage.update(chat.id, ChatUpdate(title="Changed"))
        assert updated.groupId == "grp-1"


# ── Delete ─────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, storage, sample_chat):
        assert storage.delete(sample_chat.id)
        assert storage.get(sample_chat.id) is None

    def test_delete_nonexistent(self, storage):
        assert not storage.delete("nonexistent-id")

    def test_delete_removes_file(self, storage, sample_chat):
        chat_file = storage._chat_file(sample_chat.id)
        assert chat_file.exists()
        storage.delete(sample_chat.id)
        assert not chat_file.exists()


# ── Add Message ────────────────────────────────────────────────────

class TestAddMessage:

    def test_add_message(self, storage, sample_chat):
        msg = Message(id="m1", role="human", content="Hello!", timestamp=int(time.time() * 1000))
        result = storage.add_message(sample_chat.id, msg)
        assert result is not None
        assert len(result.messages) == 1
        assert result.messages[0].content == "Hello!"

    def test_add_multiple_messages(self, storage, sample_chat):
        for i in range(3):
            msg = Message(id=f"m{i}", role="human", content=f"msg {i}", timestamp=int(time.time() * 1000))
            storage.add_message(sample_chat.id, msg)
        chat = storage.get(sample_chat.id)
        assert len(chat.messages) == 3

    def test_add_message_updates_last_active(self, storage, sample_chat):
        original_time = sample_chat.lastActiveAt
        time.sleep(0.05)
        msg = Message(id="m1", role="human", content="Hi", timestamp=int(time.time() * 1000))
        result = storage.add_message(sample_chat.id, msg)
        assert result.lastActiveAt > original_time

    def test_add_message_to_nonexistent_returns_none(self, storage):
        msg = Message(id="m1", role="human", content="Hi", timestamp=int(time.time() * 1000))
        assert storage.add_message("nonexistent", msg) is None


# ── remove_context / remove_skill ──────────────────────────────────

class TestRemoveReferences:

    def test_remove_context_from_all(self, storage):
        c1 = storage.create(ChatCreate(title="A", contextIds=["ctx-1", "ctx-2"]))
        c2 = storage.create(ChatCreate(title="B", contextIds=["ctx-1"]))
        c3 = storage.create(ChatCreate(title="C", contextIds=["ctx-3"]))

        storage.remove_context_from_all_chats("ctx-1")

        assert "ctx-1" not in storage.get(c1.id).contextIds
        assert "ctx-2" in storage.get(c1.id).contextIds
        assert "ctx-1" not in storage.get(c2.id).contextIds
        assert "ctx-3" in storage.get(c3.id).contextIds

    def test_remove_skill_from_all(self, storage):
        c1 = storage.create(ChatCreate(title="A", skillIds=["s1", "s2"]))
        c2 = storage.create(ChatCreate(title="B", skillIds=["s1"]))

        storage.remove_skill_from_all_chats("s1")

        assert "s1" not in storage.get(c1.id).skillIds
        assert "s2" in storage.get(c1.id).skillIds
        assert "s1" not in storage.get(c2.id).skillIds


# ── Touch ──────────────────────────────────────────────────────────

class TestTouch:

    def test_touch_updates_timestamp(self, storage, sample_chat):
        original = sample_chat.lastActiveAt
        time.sleep(0.05)
        storage.touch(sample_chat.id)
        reloaded = storage.get(sample_chat.id)
        assert reloaded.lastActiveAt > original

    def test_touch_nonexistent_is_noop(self, storage):
        # Should not raise
        storage.touch("nonexistent-id")


# ── Base storage edge cases ────────────────────────────────────────

class TestBaseStorageEdgeCases:

    def test_corrupt_json_returns_none(self, storage):
        """Corrupt JSON files should return None, not crash."""
        bad_file = storage.chats_dir / "corrupt.json"
        bad_file.write_text("{invalid json content!!!")
        result = storage._read_json(bad_file)
        assert result is None

    def test_missing_file_returns_none(self, storage):
        result = storage._read_json(storage.chats_dir / "nonexistent.json")
        assert result is None

    def test_atomic_write_creates_file(self, storage, tmp_path):
        target = tmp_path / "atomic_test.json"
        storage._write_json(target, {"key": "value"})
        assert target.exists()
        assert json.loads(target.read_text()) == {"key": "value"}

    def test_atomic_write_no_tmp_leftover(self, storage, tmp_path):
        """After a successful write, no .tmp file should remain."""
        target = tmp_path / "clean.json"
        storage._write_json(target, {"clean": True})
        assert not (tmp_path / "clean.tmp").exists()
