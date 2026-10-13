"""
Whitespace-tolerant matching in app.storage.chat_search.

Regression: four conversations were titled ``ZIYA: WARNING  ⏱️  STALL: ...``
(two spaces either side of the clock, as the stall warning is logged) and
searching ``⏱️ STALL`` (one space) returned none of them because the scan was
a literal substring test.  compile_query now maps each whitespace run in the
query to ``\\s+``.
"""

import json
from pathlib import Path

import pytest

from app.storage.chat_search import compile_query, search_chats


def _write_chat(chats_dir: Path, chat_id: str, title: str, body: str = "") -> None:
    chats_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": chat_id,
        "title": title,
        "messages": [{"id": "m1", "role": "human", "content": body}] if body else [],
        "createdAt": 1000,
        "lastActiveAt": 1000,
    }
    (chats_dir / f"{chat_id}.json").write_text(json.dumps(data))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / ".ziya"
    (h / "projects" / "p1" / "chats").mkdir(parents=True)
    return h


class TestCompileQuery:
    def test_single_space_matches_double_space(self):
        assert compile_query("⏱️ STALL").search("WARNING  ⏱️  STALL: event loop")

    def test_matches_across_newline(self):
        assert compile_query("event loop").search("event\nloop")

    def test_literal_otherwise(self):
        # Regex metacharacters in the query are literal.
        assert compile_query("a.b").search("a.b")
        assert not compile_query("a.b").search("axb")

    def test_case_sensitive_flag(self):
        assert compile_query("stall").search("STALL")
        assert not compile_query("stall", case_sensitive=True).search("STALL")

    def test_blank_matches_nothing(self):
        assert compile_query("   ").search("anything") is None


class TestSearchChats:
    def test_title_with_double_space_found_by_single_space_query(self, home):
        chats = home / "projects" / "p1" / "chats"
        _write_chat(chats, "c1", "ZIYA: WARNING  ⏱️  STALL: event loop did not run")
        _write_chat(chats, "c2", "unrelated")
        res = search_chats(home, "p1", "⏱️ STALL")
        assert [r["conversationId"] for r in res] == ["c1"]
        assert res[0]["totalMatches"] == 1

    def test_body_highlight_spans_actual_match_length(self, home):
        chats = home / "projects" / "p1" / "chats"
        body = "log: WARNING  ⏱️  STALL: event loop"
        _write_chat(chats, "c1", "t", body)
        res = search_chats(home, "p1", "⏱️ STALL")
        pos = res[0]["matches"][0]["highlightPositions"][0]
        matched = body[pos["start"]:pos["start"] + pos["length"]]
        # The highlight covers the two-space form present in the text, not the
        # one-space form that was typed.
        assert matched == "⏱️  STALL"
        assert "⏱️  STALL" in res[0]["matches"][0]["snippet"]

    def test_plain_substring_still_works(self, home):
        chats = home / "projects" / "p1" / "chats"
        _write_chat(chats, "c1", "Email", "set up outlook today")
        res = search_chats(home, "p1", "outlook")
        assert len(res) == 1
        pos = res[0]["matches"][0]["highlightPositions"][0]
        assert pos == {"start": 7, "length": len("outlook")}
