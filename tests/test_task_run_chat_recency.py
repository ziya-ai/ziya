"""
Task-card activity must bump the source conversation's recency.

The conversation list sorts by ``lastActiveAt``, but a chat whose only
activity is a bound task card (launch, status transitions, heartbeats)
previously never moved — the run writes touched only the run file.
These tests cover the seam: TaskRunStorage lifecycle writes →
ChatStorage.touch() → chat file's lastActiveAt/_version.

Written to FAIL against the unpatched code (ChatStorage has no touch();
TaskRunStorage never touches the chat).
"""

import json
import time

import pytest

import app.storage.task_runs as task_runs_module
from app.models.chat import ChatCreate
from app.models.task_run import TaskRunCreate
from app.storage.chats import ChatStorage
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "projects" / "recency-test"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def chat_storage(project_dir):
    return ChatStorage(project_dir)


@pytest.fixture
def run_storage(project_dir):
    # The touch throttle is module-level (TaskRunStorage is per-request
    # on API paths); clear it so one test's touch can't shadow another's.
    task_runs_module._chat_touch_times.clear()
    return TaskRunStorage(project_dir)


def _backdate_chat(chat_storage: ChatStorage, chat_id: str, ts: int = 1000) -> None:
    """Rewrite the chat's lastActiveAt to an old value so a bump is
    unambiguous even when calls land in the same millisecond."""
    path = chat_storage._chat_file(chat_id)
    data = chat_storage._read_json(path)
    data["lastActiveAt"] = ts
    data["_version"] = ts
    chat_storage._write_json(path, data)


def _last_active(chat_storage: ChatStorage, chat_id: str) -> int:
    data = chat_storage._read_json(chat_storage._chat_file(chat_id))
    return data["lastActiveAt"]


class TestChatStorageTouch:
    def test_touch_bumps_last_active_and_version(self, chat_storage):
        chat = chat_storage.create(ChatCreate(title="t"))
        _backdate_chat(chat_storage, chat.id)

        result = chat_storage.touch(chat.id)

        assert result is not None
        data = chat_storage._read_json(chat_storage._chat_file(chat.id))
        assert data["lastActiveAt"] > 1000
        assert data["_version"] == data["lastActiveAt"]

    def test_touch_missing_chat_returns_none(self, chat_storage):
        assert chat_storage.touch("no-such-chat") is None

    def test_touch_preserves_other_fields(self, chat_storage):
        chat = chat_storage.create(ChatCreate(title="keep me"))
        _backdate_chat(chat_storage, chat.id)

        chat_storage.touch(chat.id)

        data = chat_storage._read_json(chat_storage._chat_file(chat.id))
        assert data["title"] == "keep me"
        assert data["id"] == chat.id


class TestTaskRunTouchesConversation:
    def test_create_bumps_source_conversation(self, chat_storage, run_storage):
        chat = chat_storage.create(ChatCreate(title="t"))
        _backdate_chat(chat_storage, chat.id)

        run_storage.create(TaskRunCreate(
            card_id="card-1", source_conversation_id=chat.id,
        ))

        assert _last_active(chat_storage, chat.id) > 1000

    def test_update_status_bumps_source_conversation(
        self, chat_storage, run_storage,
    ):
        chat = chat_storage.create(ChatCreate(title="t"))
        run = run_storage.create(TaskRunCreate(
            card_id="card-1", source_conversation_id=chat.id,
        ))
        _backdate_chat(chat_storage, chat.id)
        task_runs_module._chat_touch_times.clear()  # bypass throttle

        run_storage.update_status(run.id, "done")

        assert _last_active(chat_storage, chat.id) > 1000

    def test_no_source_conversation_is_a_noop(self, run_storage):
        # Headless/scheduled launch: must not raise.
        run = run_storage.create(TaskRunCreate(card_id="card-1"))
        assert run_storage.update_status(run.id, "running") is not None

    def test_missing_chat_does_not_break_run_write(self, run_storage):
        # Chat deleted (or lives in another project): run write survives.
        run = run_storage.create(TaskRunCreate(
            card_id="card-1", source_conversation_id="deleted-chat",
        ))
        assert run_storage.update_status(run.id, "failed") is not None

    def test_touch_is_throttled_per_chat(self, chat_storage, run_storage):
        chat = chat_storage.create(ChatCreate(title="t"))
        run = run_storage.create(TaskRunCreate(
            card_id="card-1", source_conversation_id=chat.id,
        ))
        # First touch (create) recorded a fresh timestamp for this chat.
        _backdate_chat(chat_storage, chat.id)

        # Within the throttle window: no chat write happens.
        run_storage.update_status(run.id, "running")

        assert _last_active(chat_storage, chat.id) == 1000
