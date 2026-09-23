"""
``conversation_ids`` scoping on chat search — storage layer and the
chat_search MCP tool.  See design/conversation-handoff.md ("Retrieval from
the new segment"): a general per-conversation filter on the EXISTING search,
not a handoff-specific tool.  The handoff prelude tells the model which ids
to pass; the tool layer stays ignorant of handoffs.

Reuses the two-project fixture from test_chat_history_tools (project A is
the request root; ``cur`` is the current conversation, ``alpha`` and ``old``
also mention widget_parser).
"""
import asyncio

import pytest

from app.storage.chat_search import search_chats
from app.mcp.tools.chat_history_tools import ChatSearchTool
from tests.test_chat_history_tools import env  # noqa: F401  (fixture)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── storage layer ─────────────────────────────────────────────────────

class TestSearchChatsFilter:
    def test_restricts_to_listed_ids(self, env):
        allhits = {r["conversationId"] for r in
                   search_chats(env["ziya_home"], env["pid_a"], "widget_parser")}
        assert {env["cur"], env["alpha"], env["old"]} <= allhits
        scoped = search_chats(env["ziya_home"], env["pid_a"], "widget_parser",
                              conversation_ids=[env["alpha"]])
        assert [r["conversationId"] for r in scoped] == [env["alpha"]]

    def test_empty_list_means_unfiltered(self, env):
        a = search_chats(env["ziya_home"], env["pid_a"], "widget_parser")
        b = search_chats(env["ziya_home"], env["pid_a"], "widget_parser",
                         conversation_ids=[])
        assert [r["conversationId"] for r in a] == [r["conversationId"] for r in b]

    def test_id_outside_project_scope_yields_nothing(self, env):
        # beta lives in project B; without all_projects the filter cannot
        # reach it (scope first, then membership).
        assert search_chats(env["ziya_home"], env["pid_a"], "widget_parser",
                            conversation_ids=[env["beta"]]) == []
        hits = search_chats(env["ziya_home"], env["pid_a"], "widget_parser",
                            all_projects=True, conversation_ids=[env["beta"]])
        assert [r["conversationId"] for r in hits] == [env["beta"]]


# ── tool layer ────────────────────────────────────────────────────────

class TestChatSearchToolScoping:
    def test_scoped_search_returns_only_listed_conversations(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser",
                                         conversation_ids=[env["alpha"], env["old"]]))
        assert not r.get("error"), r
        assert {x["conversationId"] for x in r["results"]} == {env["alpha"], env["old"]}
        assert r["conversation_ids"] == [env["alpha"], env["old"]]

    def test_explicitly_listed_current_conversation_is_searched(self, env):
        # Default behaviour excludes the current conversation…
        r0 = run(ChatSearchTool().execute(query="widget_parser"))
        assert env["cur"] not in {x["conversationId"] for x in r0["results"]}
        # …but an id the caller asked for by name is never dropped.
        r1 = run(ChatSearchTool().execute(query="widget_parser",
                                          conversation_ids=[env["cur"]]))
        assert [x["conversationId"] for x in r1["results"]] == [env["cur"]]

    def test_matches_still_carry_message_index_for_chat_read(self, env):
        r = run(ChatSearchTool().execute(query="empty case",
                                         conversation_ids=[env["alpha"]]))
        assert r["count"] == 1
        m = r["results"][0]["matches"][0]
        assert isinstance(m["messageIndex"], int) and m["side"] == "user"

    def test_omitted_parameter_reports_null_scope(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser"))
        assert r["conversation_ids"] is None
