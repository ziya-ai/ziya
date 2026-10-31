"""Reading another conversation by id when its turns are tool-heavy.

Case 606f5f05: asked to "integrate the learnings from 23fb5735", the model
spent six minutes and ~75 tool calls getting at one assistant message.

* chat_read: message 15 of 23fb5735 was 534,000+ characters, almost all of
  it rendered tool output, with the model's conclusions after it.  Per-message
  truncation keeps the head, so every read returned the first tool body and
  none of the prose.  Tool blocks are now collapsed to one-line stubs by
  default (tool_results="summary"); "full" keeps the old behaviour.

* chat_search with conversation_ids: the ids were applied only inside the
  project scope, so an id from another project returned nothing until the
  model added all_projects=True -- which then decrypted every chat file of
  every project before applying the filter (7-22 s per call, ~70 calls).
  Listed ids are now resolved directly through the chat index, across
  projects, and only those files are read.
"""
import asyncio
import json

import pytest

from app.mcp.tools.chat_history_tools import ChatReadTool, ChatSearchTool
from app.storage import chat_search as chat_search_mod
from app.storage.chat_search import search_chats
from app.utils.tool_history_rewrite import collapse_tool_blocks
from tests.test_chat_history_tools import NOW_MS, _msg, env  # noqa: F401


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


BIG = "x" * 50_000
HEAVY = (
    "I'll read the code end to end first.\n\n"
    "<!-- TOOL_GROUP_START:dff596cb|2 -->\n"
    "````tool:mcp_ReadInternalWebsites|🔐 ⫽ Readinternalwebsites|text\n"
    f'{{"content": "{BIG}"}}\n'
    "````\n"
    "<!-- TOOL_GROUP_END:dff596cb|2|900|1800 -->\n"
    "```tool:run_shell_command|$ cat a.py|python\n"
    f"{BIG}\n"
    "```\n\n"
    "CONCLUSION: frame_counter is monotonic per key_id bin.\n"
)


@pytest.fixture
def heavy(env):
    """A tool-heavy chat in project B (not the request's project)."""
    cid = "c_heavy_000001"
    rec = {"id": cid, "title": "Tool heavy", "messages": [
        _msg("human", "learn it all", NOW_MS - 2),
        _msg("assistant", HEAVY, NOW_MS - 1),
    ], "createdAt": NOW_MS - 3, "lastActiveAt": NOW_MS, "_version": NOW_MS}
    (env["ziya_home"] / "projects" / env["pid_b"] / "chats" / f"{cid}.json").write_text(
        json.dumps(rec))
    from app.storage import chat_index
    chat_index.invalidate()
    return {**env, "heavy": cid}


# ── helper ────────────────────────────────────────────────────────────

def test_collapse_keeps_prose_and_stubs_each_block():
    text, n, elided = collapse_tool_blocks(HEAVY)
    assert n == 2
    assert elided > 100_000
    assert BIG not in text
    assert "TOOL_GROUP" not in text
    assert text.startswith("I'll read the code end to end first.")
    assert text.rstrip().endswith("CONCLUSION: frame_counter is monotonic per key_id bin.")
    assert "[tool mcp_ReadInternalWebsites:" in text
    assert "[tool run_shell_command: $ cat a.py (50,000 chars of output not shown)]" in text


def test_collapse_leaves_plain_prose_alone():
    assert collapse_tool_blocks("just prose ```py\nx=1\n```") == (
        "just prose ```py\nx=1\n```", 0, 0)


# ── chat_read ─────────────────────────────────────────────────────────

class TestChatReadToolHeavy:
    def test_default_returns_the_conclusion_under_a_small_cap(self, heavy):
        r = run(ChatReadTool().execute(chat_id=heavy["heavy"], message_start=1,
                                       max_chars_per_message=2000))
        assert not r.get("error"), r
        m = r["messages"][0]
        assert "CONCLUSION: frame_counter is monotonic" in m["content"]
        assert "truncated" not in m
        assert m["tool_blocks_collapsed"] == 2
        assert r["tool_results"] == "summary"

    def test_full_mode_is_the_old_raw_view(self, heavy):
        r = run(ChatReadTool().execute(chat_id=heavy["heavy"], message_start=1,
                                       max_chars_per_message=2000,
                                       tool_results="full"))
        m = r["messages"][0]
        # Raw view truncates in the first tool body: the conclusion is lost.
        assert m["truncated"] is True
        assert "CONCLUSION" not in m["content"]
        assert "tool_blocks_collapsed" not in m

    def test_invalid_mode_errors(self, heavy):
        r = run(ChatReadTool().execute(chat_id=heavy["heavy"], tool_results="bogus"))
        assert r["error"]

    def test_plain_messages_unchanged(self, heavy):
        r = run(ChatReadTool().execute(chat_id=heavy["alpha"]))
        assert r["messages"][0]["content"].startswith("Please fix widget_parser.py")
        assert all("tool_blocks_collapsed" not in m for m in r["messages"])


# ── chat_search with conversation_ids ─────────────────────────────────

class TestScopedSearchAcrossProjects:
    def test_listed_id_in_other_project_found_without_all_projects(self, heavy):
        r = run(ChatSearchTool().execute(query="monotonic",
                                         conversation_ids=[heavy["heavy"]]))
        assert not r.get("error"), r
        assert [x["conversationId"] for x in r["results"]] == [heavy["heavy"]]
        assert r["results"][0]["matches"][0]["messageIndex"] == 1

    def test_only_listed_files_are_read(self, heavy, monkeypatch):
        reads = []
        real = chat_search_mod._read_chat_data
        monkeypatch.setattr(chat_search_mod, "_read_chat_data",
                            lambda p: reads.append(p.stem) or real(p))
        hits = search_chats(heavy["ziya_home"], heavy["pid_a"], "monotonic",
                            all_projects=True, conversation_ids=[heavy["heavy"]])
        assert [h["conversationId"] for h in hits] == [heavy["heavy"]]
        assert reads == [heavy["heavy"]]

    def test_unknown_listed_id_yields_nothing(self, heavy):
        assert search_chats(heavy["ziya_home"], heavy["pid_a"], "monotonic",
                            conversation_ids=["c_does_not_exist"]) == []

    def test_unscoped_search_still_respects_project(self, heavy):
        # Positive control: without ids, project A's scope excludes project B.
        assert search_chats(heavy["ziya_home"], heavy["pid_a"], "monotonic") == []
        hits = search_chats(heavy["ziya_home"], heavy["pid_a"], "monotonic",
                            all_projects=True)
        assert heavy["heavy"] in {h["conversationId"] for h in hits}
