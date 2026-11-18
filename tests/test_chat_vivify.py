"""
vivify_chat (app/utils/chat_vivify.py): a server write that needs the chat
record must work on a brand-new conversation the browser has not pushed yet,
at the client's own id, without shadowing a conversation stored elsewhere.
Real ChatStorage under tmp project directories.
"""
from __future__ import annotations

from app.models.chat import ChatUpdate
from app.storage.chats import ChatStorage
from app.utils.chat_vivify import PLACEHOLDER_TITLE, vivify_chat


def _storage(root, pid):
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    return ChatStorage(d)


def test_a_missing_chat_is_created_at_the_clients_id_and_is_writable(tmp_path):
    s = _storage(tmp_path, "p1")
    assert s.get("new-chat") is None
    chat = vivify_chat(s, "p1", "new-chat")
    assert chat is not None and chat.id == "new-chat"
    assert chat.title == PLACEHOLDER_TITLE and chat.messages == []
    # The point of the record: a server-owned field can now be written.
    s.update("new-chat", ChatUpdate(activeAgent="helper"))
    assert s.get("new-chat").activeAgent == "helper"
    assert [c.id for c in s.list()] == ["new-chat"]


def test_an_existing_chat_is_returned_untouched(tmp_path):
    s = _storage(tmp_path, "p1")
    first = vivify_chat(s, "p1", "c")
    s.update("c", ChatUpdate(title="Real title"))
    again = vivify_chat(s, "p1", "c")
    assert again.title == "Real title" and again.createdAt == first.createdAt


def test_a_conversation_stored_in_another_project_is_not_shadowed(tmp_path):
    other = _storage(tmp_path, "p2")
    other._write_json(other._chat_file("g"), {
        "id": "g", "title": "Global", "messages": [{"id": "m", "role": "human", "content": "hi"}],
        "createdAt": 1, "lastActiveAt": 1})
    here = _storage(tmp_path, "p1")
    assert vivify_chat(here, "p1", "g") is None
    assert here.get("g") is None
