"""
Tests for app.mcp.tools.chat_history_tools — chat_search / chat_read / chat_list.

End-to-end against real chat files on disk (one ALE-encrypted), through the
cross-project chat index and the decrypt-aware reader, with the request-scoped
ContextVars (conversation_id, project_root) stubbed via app.context.

The seam that matters most: app/tool_execution.py overwrites
``args['conversation_id']`` with the *calling* conversation's id before
``execute()`` runs.  chat_read therefore takes ``chat_id``; a test below
injects a foreign ``conversation_id`` and asserts the requested chat is the
one returned.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from app.mcp.tools.chat_history_tools import (
    ChatListTool,
    ChatReadTool,
    ChatSearchTool,
    _assign_turns,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _msg(role, content, ts, muted=False, **extra):
    d = {"id": os.urandom(4).hex(), "role": role, "content": content,
         "timestamp": ts}
    if muted:
        d["muted"] = True
    d.update(extra)
    return d


NOW_MS = int(time.time() * 1000)
DAY = 86_400_000


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Two projects; project A registered for the current request root.

    Project A chats:
      cur     — the "current" conversation (should be excluded by default)
      alpha   — 3 turns about widget_parser.py, includes a system note and
                a muted assistant message
      old     — 40 days old, mentions widget_parser in the title only
    Project B chats:
      beta    — ALE-encrypted on disk; mentions widget_parser in a user turn
      inactive— isActive False (excluded from search like the sidebar)
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    projects_dir.mkdir(parents=True)

    pid_a = "p_a_" + os.urandom(3).hex()
    pid_b = "p_b_" + os.urandom(3).hex()
    for pid in (pid_a, pid_b):
        (projects_dir / pid / "chats").mkdir(parents=True)
    (projects_dir / pid_a / "project.json").write_text(json.dumps({
        "id": pid_a, "name": "A", "path": str(project_root.resolve()),
        "createdAt": NOW_MS, "lastAccessedAt": NOW_MS,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / pid_b / "project.json").write_text(json.dumps({
        "id": pid_b, "name": "B", "path": str(tmp_path / "other"),
        "createdAt": NOW_MS, "lastAccessedAt": NOW_MS,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): pid_a}))

    def chat(cid, title, messages, last_active=NOW_MS, **extra):
        rec = {"id": cid, "title": title, "groupId": None, "contextIds": [],
               "skillIds": [], "additionalFiles": [], "additionalPrompt": None,
               "messages": messages, "createdAt": last_active - 1000,
               "lastActiveAt": last_active, "_version": last_active}
        rec.update(extra)
        return rec

    ids = {"cur": "c_cur_" + os.urandom(3).hex(),
           "alpha": "c_alpha_" + os.urandom(3).hex(),
           "old": "c_old_" + os.urandom(3).hex(),
           "beta": "c_beta_" + os.urandom(3).hex(),
           "inactive": "c_inact_" + os.urandom(3).hex()}

    t = NOW_MS - 10 * 60_000
    alpha_msgs = [
        _msg("human", "Please fix widget_parser.py so it handles empty input", t),
        _msg("assistant", "Looking at widget_parser.py now.", t + 1),
        _msg("system", "[task result] run 42 finished", t + 2),
        _msg("human", "Also add a test for the empty case", t + 3),
        _msg("assistant", "A" * 500, t + 4, muted=True),
        _msg("human", "Looks good, ship it", t + 5),
        _msg("assistant", [{"type": "text", "text": "Shipped the widget_parser fix."},
                           {"type": "image", "source": {}}], t + 6),
    ]
    records_a = [
        chat(ids["cur"], "Current chat",
             [_msg("human", "widget_parser question in the current chat", t)]),
        chat(ids["alpha"], "Widget parser work", alpha_msgs,
             additionalFiles=["src/widget_parser.py"],
             branchedFrom="c_trunk", branchedAtMessageIndex=7,
             lineageRootId="c_trunk"),
        chat(ids["old"], "widget_parser archaeology",
             [_msg("human", "unrelated body text", NOW_MS - 40 * DAY),
              _msg("assistant", "yes", NOW_MS - 40 * DAY + 1)],
             last_active=NOW_MS - 40 * DAY),
    ]
    for rec in records_a:
        (projects_dir / pid_a / "chats" / f"{rec['id']}.json").write_text(json.dumps(rec))

    beta = chat(ids["beta"], "Encrypted beta",
                [_msg("human", "where does widget_parser get imported?", t),
                 _msg("assistant", "In three places.", t + 1)])
    from app.utils.encryption import get_encryptor
    (projects_dir / pid_b / "chats" / f"{ids['beta']}.json").write_bytes(
        get_encryptor().encrypt(json.dumps(beta).encode()))
    inactive = chat(ids["inactive"], "Inactive",
                    [_msg("human", "widget_parser in an inactive chat", t)],
                    isActive=False)
    (projects_dir / pid_b / "chats" / f"{ids['inactive']}.json").write_text(json.dumps(inactive))

    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer",
        lambda: type("X", (), {"is_expired": lambda *a, **kw: False})(),
        raising=False)
    from app.storage import chat_index
    chat_index.invalidate()

    from app.context import set_conversation_id, set_project_root
    set_conversation_id(ids["cur"])
    set_project_root(str(project_root.resolve()))

    return {"ziya_home": ziya_home, "pid_a": pid_a, "pid_b": pid_b, **ids}


# ── turn assignment ────────────────────────────────────────────────

class TestAssignTurns:

    def test_new_turn_at_each_user_message(self):
        msgs = [{"role": "human"}, {"role": "assistant"}, {"role": "system"},
                {"role": "human"}, {"role": "assistant"}]
        assert _assign_turns(msgs) == [1, 1, 1, 2, 2]

    def test_leading_system_folds_into_turn_one(self):
        msgs = [{"role": "system"}, {"role": "human"}, {"role": "assistant"}]
        assert _assign_turns(msgs) == [1, 1, 1]

    def test_user_alias_role(self):
        assert _assign_turns([{"role": "user"}, {"role": "ai"}, {"role": "user"}]) == [1, 1, 2]

    def test_empty(self):
        assert _assign_turns([]) == []


# ── chat_search ────────────────────────────────────────────────────

class TestChatSearch:

    def test_current_project_default_excludes_current_and_other_project(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser"))
        assert not r.get("error"), r
        ids = {x["conversationId"] for x in r["results"]}
        assert env["alpha"] in ids
        assert env["old"] in ids            # title-only hit still counts
        assert env["cur"] not in ids        # current conversation excluded
        assert env["beta"] not in ids       # other project
        assert r["scope"] == "current_project"
        assert "projectId" not in r["results"][0]

    def test_all_projects_reads_encrypted_and_skips_inactive(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser", all_projects=True))
        ids = {x["conversationId"]: x for x in r["results"]}
        assert env["beta"] in ids
        assert ids[env["beta"]]["projectId"] == env["pid_b"]
        assert env["inactive"] not in ids

    def test_include_current(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser", include_current=True))
        assert env["cur"] in {x["conversationId"] for x in r["results"]}

    def test_match_shape_feeds_chat_read(self, env):
        r = run(ChatSearchTool().execute(query="empty case"))
        hit = next(x for x in r["results"] if x["conversationId"] == env["alpha"])
        m = hit["matches"][0]
        assert m["messageIndex"] == 3
        assert m["side"] == "user"
        assert "empty case" in m["snippet"]
        assert m["timestamp"] and m["timestamp"].endswith("Z")
        assert hit["lastActiveAt"].endswith("Z")

    def test_side_user_drops_assistant_only_and_title_only_hits(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser", side="user"))
        by_id = {x["conversationId"]: x for x in r["results"]}
        assert env["alpha"] in by_id
        assert all(m["side"] == "user" for m in by_id[env["alpha"]]["matches"])
        # "old" matches widget_parser only in its title → gone under a side filter
        assert env["old"] not in by_id
        # Positive control: unfiltered search does include it
        r2 = run(ChatSearchTool().execute(query="widget_parser"))
        assert env["old"] in {x["conversationId"] for x in r2["results"]}

    def test_side_assistant(self, env):
        r = run(ChatSearchTool().execute(query="Shipped the", side="assistant"))
        hit = next(x for x in r["results"] if x["conversationId"] == env["alpha"])
        assert hit["matches"][0]["side"] == "assistant"
        assert hit["matches"][0]["messageIndex"] == 6   # multimodal content searched

    def test_sort_newest_and_limit(self, env):
        r = run(ChatSearchTool().execute(query="widget_parser", sort="newest", limit=1))
        assert r["count"] == 1
        assert r["truncated"] is True
        assert r["results"][0]["conversationId"] == env["alpha"]

    def test_invalid_side_and_empty_query(self, env):
        assert run(ChatSearchTool().execute(query="x", side="nope"))["error"]
        assert run(ChatSearchTool().execute(query="   "))["error"]

    def test_executor_injected_conversation_id_is_ignored(self, env):
        # The executor adds the calling chat id under this key; the tool
        # must not treat it as an argument.
        r = run(ChatSearchTool().execute(query="widget_parser",
                                        conversation_id=env["alpha"]))
        assert not r.get("error")
        assert env["cur"] not in {x["conversationId"] for x in r["results"]}


# ── chat_read ──────────────────────────────────────────────────────

class TestChatRead:

    def test_full_read_header_and_message_shape(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"]))
        assert not r.get("error"), r
        assert r["conversationId"] == env["alpha"]
        assert r["title"] == "Widget parser work"
        assert r["projectId"] == env["pid_a"]
        assert r["messageCount"] == 7
        assert r["turnCount"] == 3
        assert r["selection"] == "all"
        assert r["returned"] == 7
        # heritage + files surfaced in the header
        assert r["branchedFrom"] == "c_trunk"
        assert r["branchedAtMessageIndex"] == 7
        assert r["lineageRootId"] == "c_trunk"
        assert r["additionalFiles"] == ["src/widget_parser.py"]
        first = r["messages"][0]
        assert first["index"] == 0 and first["turn"] == 1 and first["side"] == "user"
        assert first["timestamp"].endswith("Z")
        assert [m["turn"] for m in r["messages"]] == [1, 1, 1, 2, 2, 3, 3]
        assert r["messages"][2]["side"] == "system"
        assert r["messages"][4]["muted"] is True
        # multimodal content → text blocks joined, image counted
        last = r["messages"][6]
        assert last["content"] == "Shipped the widget_parser fix."

    def test_reads_encrypted_chat_in_other_project(self, env):
        r = run(ChatReadTool().execute(chat_id=env["beta"]))
        assert not r.get("error"), r
        assert r["projectId"] == env["pid_b"]
        assert r["messages"][0]["content"].startswith("where does widget_parser")

    def test_side_user_only_returns_prompting(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], side="user"))
        assert [m["index"] for m in r["messages"]] == [0, 3, 5]
        assert all(m["side"] == "user" for m in r["messages"])
        assert r["skipped_other_side"] == 4   # 3 assistant + 1 system

    def test_side_assistant_excludes_system(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], side="assistant"))
        assert [m["index"] for m in r["messages"]] == [1, 4, 6]

    def test_turn_range(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], turn_start=2, turn_end=2))
        assert r["selection"] == "turns 2..2"
        assert [m["index"] for m in r["messages"]] == [3, 4]

    def test_turn_start_only_means_single_turn(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], turn_start=3))
        assert [m["index"] for m in r["messages"]] == [5, 6]

    def test_negative_turns_count_from_end(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], turn_start=-2, turn_end=-1))
        assert r["selection"] == "turns 2..3"
        assert [m["index"] for m in r["messages"]] == [3, 4, 5, 6]

    def test_message_range_and_around(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], message_start=1, message_end=2))
        assert [m["index"] for m in r["messages"]] == [1, 2]
        r = run(ChatReadTool().execute(chat_id=env["alpha"], around=3, radius=1))
        assert r["selection"] == "around message 3 ±1"
        assert [m["index"] for m in r["messages"]] == [2, 3, 4]

    def test_around_out_of_range_and_empty_ranges_error(self, env):
        assert run(ChatReadTool().execute(chat_id=env["alpha"], around=99))["error"]
        assert run(ChatReadTool().execute(chat_id=env["alpha"], turn_start=5))["error"]
        assert run(ChatReadTool().execute(chat_id=env["alpha"],
                                          message_start=5, message_end=2))["error"]

    def test_per_message_truncation(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], around=4, radius=0,
                                       max_chars_per_message=100))
        m = r["messages"][0]
        assert m["truncated"] is True
        assert m["content"].startswith("A" * 100)
        assert "[+400 chars]" in m["content"]

    def test_total_cap_reports_next_index(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], max_total_chars=200))
        assert r["truncated"] is True
        assert r["returned"] >= 1
        assert r["next_message_index"] == r["messages"][-1]["index"] + 1
        # Continue from there: the next page starts exactly at that index.
        r2 = run(ChatReadTool().execute(chat_id=env["alpha"],
                                        message_start=r["next_message_index"]))
        assert r2["messages"][0]["index"] == r["next_message_index"]

    def test_exclude_muted(self, env):
        r = run(ChatReadTool().execute(chat_id=env["alpha"], include_muted=False))
        assert 4 not in [m["index"] for m in r["messages"]]
        assert r["skipped_muted"] == 1

    def test_unknown_id_and_path_tricks(self, env):
        assert run(ChatReadTool().execute(chat_id="nope"))["error"]
        assert run(ChatReadTool().execute(chat_id="../project.json"))["error"]
        assert run(ChatReadTool().execute(chat_id=""))["error"]

    def test_executor_injected_conversation_id_does_not_hijack_chat_id(self, env):
        # Exactly what app/tool_execution.py does: args['conversation_id']
        # is set to the CALLING chat.  The requested chat must still win.
        r = run(ChatReadTool().execute(chat_id=env["alpha"],
                                       conversation_id=env["cur"]))
        assert r["conversationId"] == env["alpha"]

    def test_chat_written_after_index_built_is_found(self, env):
        from app.storage import chat_index
        chat_index.lookup(env["ziya_home"], env["alpha"])   # force build
        new_id = "c_late_" + os.urandom(3).hex()
        rec = {"id": new_id, "title": "Late", "messages": [
            _msg("human", "hello", NOW_MS)], "createdAt": NOW_MS,
            "lastActiveAt": NOW_MS}
        (env["ziya_home"] / "projects" / env["pid_a"] / "chats" / f"{new_id}.json"
         ).write_text(json.dumps(rec))
        r = run(ChatReadTool().execute(chat_id=new_id))
        assert r.get("conversationId") == new_id


# ── chat_list ──────────────────────────────────────────────────────

class TestChatList:

    def test_current_project_newest_first_excludes_current(self, env):
        r = run(ChatListTool().execute())
        ids = [c["conversationId"] for c in r["conversations"]]
        assert ids == [env["alpha"], env["old"]]
        assert env["cur"] not in ids
        alpha = r["conversations"][0]
        assert alpha["messageCount"] == 7
        assert alpha["branchedFrom"] == "c_trunk"
        assert alpha["lastActiveAt"].endswith("Z")
        assert "projectId" not in alpha

    def test_since_days_filters_old(self, env):
        r = run(ChatListTool().execute(since_days=7))
        assert [c["conversationId"] for c in r["conversations"]] == [env["alpha"]]

    def test_title_filter_and_all_projects(self, env):
        r = run(ChatListTool().execute(all_projects=True, title_contains="beta"))
        assert [c["conversationId"] for c in r["conversations"]] == [env["beta"]]
        assert r["conversations"][0]["projectId"] == env["pid_b"]

    def test_limit_and_truncated(self, env):
        r = run(ChatListTool().execute(all_projects=True, limit=1, include_current=True))
        assert r["count"] == 1 and r["truncated"] is True


# ── registration seam ──────────────────────────────────────────────

class TestRegistration:

    def test_category_registered_and_enabled_by_default(self):
        from app.mcp import builtin_tools as bt
        assert "chat_history" in bt.BUILTIN_TOOL_CATEGORIES
        assert bt.BUILTIN_TOOL_CATEGORIES["chat_history"]["enabled_by_default"] is True
        names = {cls().name for cls in bt.get_builtin_tools_for_category("chat_history")}
        assert names == {"chat_search", "chat_read", "chat_list"}

    def test_results_are_medium_trust(self):
        from app.mcp.tool_result_demarcation import classify_trust
        for n in ("chat_search", "chat_read", "chat_list"):
            assert classify_trust(n) == "medium"

    def test_chat_read_schema_has_no_conversation_id_param(self):
        # Guard against the executor-overwrite hazard being reintroduced.
        from app.mcp.tools.chat_history_tools import ChatReadInput
        assert "conversation_id" not in ChatReadInput.model_fields
        assert "chat_id" in ChatReadInput.model_fields
