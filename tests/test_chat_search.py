"""
Tests for app.storage.chat_search — server-side chat search.

Covers:
  - string content matches (the original "outlook returns nothing" bug)
  - array/block content matches (multimodal — coerced to text)
  - None/non-string content is skipped without crashing the whole scan
  - a poisoned record does NOT zero out other results
  - title-only matches
  - case sensitivity
  - strict-local scope vs all-projects scope
  - inactive chats excluded
  - snippet/highlight shape matches the frontend SearchResult contract
  - encrypted-file read path (decrypt before scan)
  - result ordering (match count, then recency)
"""

import json
import time
from pathlib import Path

import pytest

from app.storage.chat_search import (
    search_chats,
    _to_searchable_text,
    _search_one_chat,
)


def _write_chat(chats_dir: Path, chat_id: str, **fields) -> None:
    chats_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": chat_id,
        "title": fields.pop("title", "Untitled"),
        "messages": fields.pop("messages", []),
        "createdAt": fields.pop("createdAt", 1000),
        "lastActiveAt": fields.pop("lastActiveAt", 1000),
        **fields,
    }
    (chats_dir / f"{chat_id}.json").write_text(json.dumps(data))


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    (home / "projects").mkdir(parents=True)
    return home


def _proj(ziya_home, pid):
    d = ziya_home / "projects" / pid / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── content coercion ───────────────────────────────────────────────

class TestToSearchableText:
    def test_string_passthrough(self):
        assert _to_searchable_text("hello outlook") == "hello outlook"

    def test_block_array_extracts_text(self):
        blocks = [{"type": "text", "text": "talk to outlook"}, {"type": "image"}]
        assert "outlook" in _to_searchable_text(blocks)

    def test_none_returns_empty(self):
        assert _to_searchable_text(None) == ""

    def test_number_returns_empty(self):
        assert _to_searchable_text(42) == ""


# ── core matching ──────────────────────────────────────────────────

class TestSearch:
    def test_string_content_match(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="Email",
                    messages=[{"id": "m1", "role": "human", "content": "set up outlook"}])
        results = search_chats(ziya_home, "p1", "outlook")
        assert len(results) == 1
        assert results[0]["conversationId"] == "c1"
        assert results[0]["matches"][0]["messageIndex"] == 0

    def test_array_content_match(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="Multimodal",
                    messages=[{"id": "m1", "role": "human",
                               "content": [{"type": "text", "text": "configure outlook now"}]}])
        results = search_chats(ziya_home, "p1", "outlook")
        assert len(results) == 1

    def test_poisoned_record_does_not_zero_results(self, ziya_home):
        """A chat with non-string content must not abort the whole scan."""
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "poison", title="Bad",
                    messages=[{"id": "m1", "role": "human", "content": None},
                              {"id": "m2", "role": "assistant", "content": 123}])
        _write_chat(chats, "good", title="Good",
                    messages=[{"id": "m1", "role": "human", "content": "outlook rocks"}])
        results = search_chats(ziya_home, "p1", "outlook")
        ids = {r["conversationId"] for r in results}
        assert "good" in ids

    def test_title_match(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="Outlook setup notes",
                    messages=[{"id": "m1", "role": "human", "content": "unrelated"}])
        results = search_chats(ziya_home, "p1", "outlook")
        assert len(results) == 1
        assert results[0]["totalMatches"] >= 1

    def test_case_insensitive_default(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "OUTLOOK"}])
        assert len(search_chats(ziya_home, "p1", "outlook")) == 1

    def test_case_sensitive_no_match(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "OUTLOOK"}])
        assert search_chats(ziya_home, "p1", "outlook", case_sensitive=True) == []

    def test_no_match_returns_empty(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "nothing here"}])
        assert search_chats(ziya_home, "p1", "outlook") == []

    def test_inactive_chat_excluded(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="x", isActive=False,
                    messages=[{"id": "m1", "role": "human", "content": "outlook"}])
        assert search_chats(ziya_home, "p1", "outlook") == []


# ── scope ──────────────────────────────────────────────────────────

class TestScope:
    def test_local_excludes_other_projects(self, ziya_home):
        _write_chat(_proj(ziya_home, "p1"), "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "outlook here"}])
        _write_chat(_proj(ziya_home, "p2"), "c2", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "outlook there"}])
        results = search_chats(ziya_home, "p1", "outlook", all_projects=False)
        assert {r["conversationId"] for r in results} == {"c1"}

    def test_local_excludes_global_chats_from_other_projects(self, ziya_home):
        """Strict local: even isGlobal chats in other projects are out of scope."""
        _write_chat(_proj(ziya_home, "p2"), "g1", title="x", isGlobal=True,
                    messages=[{"id": "m1", "role": "human", "content": "outlook global"}])
        assert search_chats(ziya_home, "p1", "outlook", all_projects=False) == []

    def test_all_projects_includes_everything(self, ziya_home):
        _write_chat(_proj(ziya_home, "p1"), "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "outlook here"}])
        _write_chat(_proj(ziya_home, "p2"), "c2", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "outlook there"}])
        results = search_chats(ziya_home, "p1", "outlook", all_projects=True)
        assert {r["conversationId"] for r in results} == {"c1", "c2"}


# ── result shape & ordering ────────────────────────────────────────

class TestShapeAndOrder:
    def test_match_shape_matches_frontend_contract(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "c1", title="x", lastAccessedAt=5000,
                    messages=[{"id": "m1", "role": "human", "content": "an outlook message",
                               "_timestamp": 4242}])
        r = search_chats(ziya_home, "p1", "outlook")[0]
        assert set(r.keys()) >= {
            "conversationId", "conversationTitle", "folderId", "projectId",
            "matches", "totalMatches", "lastAccessedAt",
        }
        m = r["matches"][0]
        assert set(m.keys()) >= {
            "messageIndex", "messageRole", "snippet", "fullContent",
            "timestamp", "highlightPositions",
        }
        assert m["highlightPositions"][0]["length"] == len("outlook")
        assert m["timestamp"] == 4242

    def test_snippet_truncated_to_max_length(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        body = ("padding " * 50) + "outlook" + (" padding" * 50)
        _write_chat(chats, "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": body}])
        m = search_chats(ziya_home, "p1", "outlook", max_snippet_length=40)[0]["matches"][0]
        # +3 for the trailing ellipsis appended after the length cap.
        assert len(m["snippet"]) <= 43

    def test_results_sorted_by_match_count_then_recency(self, ziya_home):
        chats = _proj(ziya_home, "p1")
        _write_chat(chats, "few", title="x", lastAccessedAt=9000,
                    messages=[{"id": "m1", "role": "human", "content": "outlook once"}])
        _write_chat(chats, "many", title="x", lastAccessedAt=1000,
                    messages=[{"id": "m1", "role": "human", "content": "outlook outlook outlook"}])
        results = search_chats(ziya_home, "p1", "outlook")
        # "many" has 3 occurrences but they're in ONE message → 1 match each.
        # Tie on totalMatches (1) → recency wins → "few" (9000) first.
        assert results[0]["conversationId"] == "few"

    def test_empty_query_returns_empty(self, ziya_home):
        _write_chat(_proj(ziya_home, "p1"), "c1", title="x",
                    messages=[{"id": "m1", "role": "human", "content": "outlook"}])
        assert search_chats(ziya_home, "p1", "   ") == []


# ── encryption ─────────────────────────────────────────────────────

class TestEncryptedFiles:
    def test_encrypted_chat_is_decrypted_and_searched(self, ziya_home, monkeypatch):
        """A file written via the encrypting storage path is still searchable."""
        from app.storage.chats import ChatStorage
        from app.models.chat import ChatCreate
        storage = ChatStorage(ziya_home / "projects" / "p1")
        chat = storage.create(ChatCreate(title="Encrypted maybe"))
        # Append a message with the search term and persist.
        raw = storage._read_json(storage._chat_file(chat.id))
        raw["messages"] = [{"id": "m1", "role": "human", "content": "secret outlook"}]
        storage._write_json(storage._chat_file(chat.id), raw)
        results = search_chats(ziya_home, "p1", "outlook")
        assert len(results) == 1
        assert results[0]["conversationId"] == chat.id
