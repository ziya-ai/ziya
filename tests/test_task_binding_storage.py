"""Tests for TaskBindingStorage.

Exercises the per-chat list-in-one-file storage shape without
depending on the chat content itself — bindings are pure pointers.
"""

import json
import pytest
from pathlib import Path

from app.models.task_binding import TaskBinding
from app.storage.task_bindings import TaskBindingStorage


@pytest.fixture
def storage(tmp_path: Path) -> TaskBindingStorage:
    return TaskBindingStorage(project_dir=tmp_path)


@pytest.fixture
def chat_id() -> str:
    return "chat-abc"


class TestListAndCreate:
    def test_list_empty_when_no_file(self, storage, chat_id):
        assert storage.list_for_chat(chat_id) == []

    def test_create_returns_populated_binding(self, storage, chat_id):
        b = storage.create(
            chat_id=chat_id, card_id="card-1", run_id="run-1",
            anchor_message_id="msg-40",
        )
        assert b.id  # got a uuid
        assert b.chat_id == chat_id
        assert b.card_id == "card-1"
        assert b.run_id == "run-1"
        assert b.anchor_message_id == "msg-40"
        assert b.created_at > 0

    def test_create_then_list_roundtrip(self, storage, chat_id):
        b = storage.create(chat_id, "card-1", "run-1", "msg-40")
        listed = storage.list_for_chat(chat_id)
        assert len(listed) == 1
        assert listed[0].id == b.id
        assert listed[0].anchor_message_id == "msg-40"

    def test_create_with_null_anchor(self, storage, chat_id):
        b = storage.create(chat_id, "card-1", "run-1", anchor_message_id=None)
        assert b.anchor_message_id is None
        listed = storage.list_for_chat(chat_id)
        assert listed[0].anchor_message_id is None

    def test_multiple_bindings_same_chat(self, storage, chat_id):
        b1 = storage.create(chat_id, "card-1", "run-1", "msg-10")
        b2 = storage.create(chat_id, "card-1", "run-2", "msg-40")
        b3 = storage.create(chat_id, "card-2", "run-3", "msg-50")
        listed = storage.list_for_chat(chat_id)
        assert len(listed) == 3
        assert {b.id for b in listed} == {b1.id, b2.id, b3.id}

    def test_different_chats_isolated(self, storage):
        storage.create("chat-a", "card-1", "run-1", "msg-1")
        storage.create("chat-b", "card-2", "run-2", "msg-2")
        a = storage.list_for_chat("chat-a")
        b = storage.list_for_chat("chat-b")
        assert len(a) == 1 and len(b) == 1
        assert a[0].card_id == "card-1"
        assert b[0].card_id == "card-2"


class TestGet:
    def test_get_existing(self, storage, chat_id):
        b = storage.create(chat_id, "card-1", "run-1", "msg-1")
        found = storage.get(chat_id, b.id)
        assert found is not None
        assert found.id == b.id

    def test_get_missing_binding(self, storage, chat_id):
        storage.create(chat_id, "card-1", "run-1", "msg-1")
        assert storage.get(chat_id, "nonexistent") is None

    def test_get_missing_chat(self, storage):
        assert storage.get("no-chat", "no-binding") is None


class TestDelete:
    def test_delete_returns_true_when_found(self, storage, chat_id):
        b = storage.create(chat_id, "card-1", "run-1", "msg-1")
        assert storage.delete(chat_id, b.id) is True

    def test_delete_returns_false_when_missing(self, storage, chat_id):
        storage.create(chat_id, "card-1", "run-1", "msg-1")
        assert storage.delete(chat_id, "nonexistent") is False

    def test_delete_removes_only_that_binding(self, storage, chat_id):
        b1 = storage.create(chat_id, "card-1", "run-1", "msg-1")
        b2 = storage.create(chat_id, "card-1", "run-2", "msg-2")
        storage.delete(chat_id, b1.id)
        remaining = storage.list_for_chat(chat_id)
        assert len(remaining) == 1
        assert remaining[0].id == b2.id

    def test_delete_last_binding_removes_file(self, storage, chat_id, tmp_path):
        b = storage.create(chat_id, "card-1", "run-1", "msg-1")
        bindings_file = tmp_path / "chats" / f"{chat_id}.bindings.json"
        assert bindings_file.exists()
        storage.delete(chat_id, b.id)
        assert not bindings_file.exists()

    def test_delete_from_empty_chat(self, storage):
        assert storage.delete("ghost-chat", "ghost-binding") is False


class TestCorruptData:
    def test_non_list_content_returns_empty(self, storage, chat_id, tmp_path):
        """If the bindings file is malformed JSON (not a list), listing
        should return [] rather than crash."""
        bindings_file = tmp_path / "chats" / f"{chat_id}.bindings.json"
        bindings_file.parent.mkdir(parents=True, exist_ok=True)
        bindings_file.write_text('{"not": "a list"}')
        assert storage.list_for_chat(chat_id) == []

    def test_partial_corruption_skips_bad_rows(
        self, storage, chat_id, tmp_path,
    ):
        """A row missing required fields should be skipped, not crash
        the whole list."""
        bindings_file = tmp_path / "chats" / f"{chat_id}.bindings.json"
        bindings_file.parent.mkdir(parents=True, exist_ok=True)
        good = {
            "id": "b-1", "chat_id": chat_id,
            "card_id": "c-1", "run_id": "r-1",
            "anchor_message_id": "msg-1", "created_at": 1000,
        }
        bad = {"id": "b-2"}  # missing required chat_id/card_id/run_id
        bindings_file.write_text(json.dumps([good, bad]))
        listed = storage.list_for_chat(chat_id)
        assert len(listed) == 1
        assert listed[0].id == "b-1"


class TestAbstractStubs:
    """The abstract methods we inherit but don't use should raise
    clearly rather than silently pretend to work."""

    def test_list_raises(self, storage):
        with pytest.raises(NotImplementedError):
            storage.list()

    def test_update_raises(self, storage):
        with pytest.raises(NotImplementedError):
            storage.update("x", {})
