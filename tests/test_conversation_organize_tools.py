"""
Tests for app.mcp.tools.conversation_organize_tools — the model's sidebar
organisation tools (folder_list / folder_create / folder_update /
folder_delete / chat_move / chat_rename / chat_fork).

End-to-end against real records on disk under a temp ZIYA_HOME, through the
same storage classes the HTTP routes use, so every assertion is on what the
next browser sync will actually read.

Seams pinned here:
  * folders carry ``parentId`` and ``updatedAt`` after a create/update —
    the frontend's serverFolderWins compares updatedAt, and the sidebar's
    path join (chat_history_tools._folder_paths) reads parentId;
  * chat_move / chat_rename bump ``_version`` (so a stale browser push loses
    on bulk-sync) but leave ``lastActiveAt`` alone (recency order);
  * chat_fork mirrors frontend/src/utils/lineage.ts forkLineageFields.
"""
import asyncio
import json
import os
import time

import pytest

from app.mcp.tools.conversation_organize_tools import (
    ChatForkTool,
    ChatMoveTool,
    ChatRenameTool,
    FolderCreateTool,
    FolderDeleteTool,
    FolderListTool,
    FolderUpdateTool,
    ORGANIZE_TOOL_NAMES,
)
from app.mcp.tools.chat_history_tools import ChatListTool, ChatReadTool, _folder_paths


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


NOW_MS = int(time.time() * 1000)
OLD_MS = NOW_MS - 30 * 86_400_000


def _msg(role, content, ts):
    return {"id": os.urandom(4).hex(), "role": role, "content": content, "timestamp": ts}


@pytest.fixture
def env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    pid = "p_org_" + os.urandom(3).hex()
    (projects_dir / pid / "chats").mkdir(parents=True)
    (projects_dir / pid / "project.json").write_text(json.dumps({
        "id": pid, "name": "Org", "path": str(project_root.resolve()),
        "createdAt": NOW_MS, "lastAccessedAt": NOW_MS,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): pid}))

    def chat(cid, title, messages, last_active, **extra):
        rec = {"id": cid, "title": title, "groupId": None, "contextIds": [],
               "skillIds": [], "additionalFiles": [], "additionalPrompt": None,
               "messages": messages, "createdAt": last_active - 1000,
               "lastActiveAt": last_active, "_version": last_active - 500}
        rec.update(extra)
        (projects_dir / pid / "chats" / f"{cid}.json").write_text(json.dumps(rec))
        return rec

    cur = "c_cur_" + os.urandom(3).hex()
    old = "c_old_" + os.urandom(3).hex()
    chat(cur, "Current chat", [_msg("human", "hi", NOW_MS)], NOW_MS)
    chat(old, "Old parser work",
         [_msg("human", "fix the parser", OLD_MS), _msg("assistant", "done", OLD_MS + 1)],
         OLD_MS, branchedFrom="c_trunk", branchedAtMessageIndex=3,
         branchedFromLabel="seam", lineageKind="branch", lineageRootId="c_trunk",
         handedOffTo="c_next", _deletedMessages=["m_gone"])

    (projects_dir / pid / "chats" / "_groups.json").write_text(json.dumps({
        "version": 1,
        "groups": [{"id": "grp_infra", "name": "Infra", "createdAt": OLD_MS,
                    "parentId": None, "order": 0}],
    }))

    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer",
        lambda: type("X", (), {"is_expired": lambda *a, **kw: False})(),
        raising=False)
    from app.storage import chat_index
    chat_index.invalidate()
    from app import context as _ctx
    t1 = _ctx._request_conversation_id.set(cur)
    t2 = _ctx._request_project_root.set(str(project_root.resolve()))
    yield {"ziya_home": ziya_home, "pid": pid, "cur": cur, "old": old,
           "chats_dir": projects_dir / pid / "chats"}
    _ctx._request_project_root.reset(t2)
    _ctx._request_conversation_id.reset(t1)


def _groups_on_disk(env):
    return {g["id"]: g for g in json.loads(
        (env["chats_dir"] / "_groups.json").read_text())["groups"]}


def _chat_on_disk(env, cid):
    return json.loads((env["chats_dir"] / f"{cid}.json").read_text())


# ── folders ────────────────────────────────────────────────────────

class TestFolders:

    def test_create_nested_and_list_shows_path(self, env):
        r = run(FolderCreateTool().execute(name="Parsers", parent_id="grp_infra"))
        assert r.get("success"), r
        fid = r["folder"]["folderId"]
        assert r["folder"]["path"] == "Infra / Parsers"
        on_disk = _groups_on_disk(env)[fid]
        assert on_disk["parentId"] == "grp_infra"          # nesting persisted
        assert on_disk["updatedAt"] >= NOW_MS               # serverFolderWins sees it
        rows = {f["folderId"]: f for f in run(FolderListTool().execute())["folders"]}
        assert rows[fid]["path"] == "Infra / Parsers" and rows[fid]["parentId"] == "grp_infra"
        assert rows["grp_infra"]["subfolderCount"] == 1
        # The read-side join in chat_history_tools sees the same hierarchy.
        assert _folder_paths(env["ziya_home"], env["pid"])[fid] == "Infra / Parsers"

    def test_create_top_level_with_root_sentinel_and_bad_parent(self, env):
        r = run(FolderCreateTool().execute(name="Misc", parent_id="root"))
        assert r["folder"]["parentId"] is None and r["folder"]["path"] == "Misc"
        assert run(FolderCreateTool().execute(name="X", parent_id="grp_nope"))["error"]
        assert run(FolderCreateTool().execute(name="   "))["error"]

    def test_update_rename_reparent_pin(self, env):
        a = run(FolderCreateTool().execute(name="A"))["folder"]["folderId"]
        before = _groups_on_disk(env)[a]["updatedAt"]
        time.sleep(0.002)
        r = run(FolderUpdateTool().execute(folder_id=a, name="Alpha",
                                           parent_id="grp_infra", pinned=True))
        assert r.get("success"), r
        assert r["folder"]["path"] == "Infra / Alpha" and r["folder"]["pinned"] is True
        on_disk = _groups_on_disk(env)[a]
        assert on_disk["name"] == "Alpha" and on_disk["parentId"] == "grp_infra"
        assert on_disk["pinned"] is True
        assert on_disk["updatedAt"] > before
        # Move back to root with the sentinel; unpin.
        r = run(FolderUpdateTool().execute(folder_id=a, parent_id="root", pinned=False))
        assert r["folder"]["parentId"] is None and r["folder"]["pinned"] is False
        assert _groups_on_disk(env)[a]["parentId"] is None

    def test_update_refuses_cycles_and_noops(self, env):
        a = run(FolderCreateTool().execute(name="A"))["folder"]["folderId"]
        b = run(FolderCreateTool().execute(name="B", parent_id=a))["folder"]["folderId"]
        c = run(FolderCreateTool().execute(name="C", parent_id=b))["folder"]["folderId"]
        assert run(FolderUpdateTool().execute(folder_id=a, parent_id=c))["error"]   # grandchild
        assert run(FolderUpdateTool().execute(folder_id=a, parent_id=a))["error"]   # self
        assert _groups_on_disk(env)[a]["parentId"] is None                           # untouched
        assert run(FolderUpdateTool().execute(folder_id=a))["error"]                 # nothing to do
        assert run(FolderUpdateTool().execute(folder_id="grp_nope", name="x"))["error"]
        # Positive control: a legal re-parent of the leaf works.
        assert run(FolderUpdateTool().execute(folder_id=c, parent_id=a))["folder"]["path"] == "A / C"

    def test_delete_only_when_empty(self, env):
        a = run(FolderCreateTool().execute(name="A"))["folder"]["folderId"]
        b = run(FolderCreateTool().execute(name="B", parent_id=a))["folder"]["folderId"]
        assert run(FolderDeleteTool().execute(folder_id=a))["error"]        # has a subfolder
        run(ChatMoveTool().execute(chat_id=env["old"], folder_id=b))
        assert run(FolderDeleteTool().execute(folder_id=b))["error"]        # has a chat
        run(ChatMoveTool().execute(chat_id=env["old"], folder_id="root"))
        assert run(FolderDeleteTool().execute(folder_id=b))["success"]
        assert run(FolderDeleteTool().execute(folder_id=a))["success"]
        assert a not in _groups_on_disk(env) and b not in _groups_on_disk(env)


# ── conversations ──────────────────────────────────────────────────

class TestChatMoveRename:

    def test_move_sets_group_bumps_version_keeps_recency(self, env):
        before = _chat_on_disk(env, env["old"])
        r = run(ChatMoveTool().execute(chat_id=env["old"], folder_id="grp_infra"))
        assert r.get("success"), r
        assert r["folder"] == "Infra"
        after = _chat_on_disk(env, env["old"])
        assert after["groupId"] == "grp_infra" and after["folderId"] == "grp_infra"
        assert after["_version"] > before["_version"]      # stale browser push loses
        assert after["lastActiveAt"] == before["lastActiveAt"]   # recency untouched
        assert after["messages"] == before["messages"]
        # Visible through the read-side tools with the folder path joined.
        rows = {c["conversationId"]: c for c in run(ChatListTool().execute())["conversations"]}
        assert rows[env["old"]]["folder"] == "Infra"
        assert run(ChatReadTool().execute(chat_id=env["old"]))["folder"] == "Infra"

    def test_move_to_root_is_explicit_null(self, env):
        run(ChatMoveTool().execute(chat_id=env["old"], folder_id="grp_infra"))
        r = run(ChatMoveTool().execute(chat_id=env["old"], folder_id="root"))
        assert r["folder"] == "root"
        after = _chat_on_disk(env, env["old"])
        assert "groupId" in after and after["groupId"] is None   # server EXPLICITLY says root

    def test_move_rejects_unknown_folder_and_chat(self, env):
        assert run(ChatMoveTool().execute(chat_id=env["old"], folder_id="grp_nope"))["error"]
        assert run(ChatMoveTool().execute(chat_id="c_nope", folder_id="grp_infra"))["error"]
        assert _chat_on_disk(env, env["old"])["groupId"] is None

    def test_move_the_current_conversation_is_allowed(self, env):
        # The executor overwrites kwargs['conversation_id']; chat_id must win.
        r = run(ChatMoveTool().execute(chat_id=env["cur"], folder_id="grp_infra",
                                       conversation_id=env["old"]))
        assert r["conversation"]["conversationId"] == env["cur"]
        assert _chat_on_disk(env, env["cur"])["groupId"] == "grp_infra"
        assert _chat_on_disk(env, env["old"])["groupId"] is None

    def test_rename(self, env):
        before = _chat_on_disk(env, env["old"])
        r = run(ChatRenameTool().execute(chat_id=env["old"], title="  Parser:   empty input  "))
        assert r.get("success"), r
        assert r["previousTitle"] == "Old parser work"
        after = _chat_on_disk(env, env["old"])
        assert after["title"] == "Parser: empty input"       # whitespace collapsed
        assert after["_version"] > before["_version"]
        assert after["lastActiveAt"] == before["lastActiveAt"]
        assert run(ChatRenameTool().execute(chat_id=env["old"], title="   "))["error"]
        assert run(ChatRenameTool().execute(chat_id=env["old"], title="x" * 201))["error"]
        assert _chat_on_disk(env, env["old"])["title"] == "Parser: empty input"


class TestChatFork:

    def test_fork_copies_transcript_and_stamps_lineage(self, env):
        src = _chat_on_disk(env, env["old"])
        r = run(ChatForkTool().execute(chat_id=env["old"]))
        assert r.get("success"), r
        new_id = r["conversation"]["conversationId"]
        assert new_id != env["old"]
        assert r["sourceConversationId"] == env["old"]
        fork = _chat_on_disk(env, new_id)
        assert fork["messages"] == src["messages"]
        assert fork["title"] == "Fork: Old parser work"
        assert fork["groupId"] == src["groupId"]           # inherits the source's folder
        # forkLineageFields twin: point at source, clear the seam, share root,
        # not handed off.
        assert fork["branchedFrom"] == env["old"]
        assert fork["lineageKind"] == "fork"
        assert fork["lineageRootId"] == "c_trunk"
        assert fork["branchedAtMessageIndex"] is None and fork["branchedFromLabel"] is None
        assert fork["handedOffTo"] is None
        assert fork["_deletedMessages"] == ["m_gone"]      # tombstones travel
        assert fork["createdAt"] >= NOW_MS and fork["_version"] >= NOW_MS
        assert fork["isActive"] is True and fork["hasUnreadResponse"] is False
        # Source untouched.
        assert _chat_on_disk(env, env["old"]) == src
        # Findable by the read-side tools (index updated) and listed with lineage.
        assert run(ChatReadTool().execute(chat_id=new_id))["branchedFrom"] == env["old"]
        rows = {c["conversationId"]: c for c in run(ChatListTool().execute())["conversations"]}
        assert rows[new_id]["branchedFrom"] == env["old"]

    def test_fork_with_title_and_folder(self, env):
        r = run(ChatForkTool().execute(chat_id=env["old"], title="Parser v2",
                                       folder_id="grp_infra"))
        fork = _chat_on_disk(env, r["conversation"]["conversationId"])
        assert fork["title"] == "Parser v2" and fork["groupId"] == "grp_infra"
        r2 = run(ChatForkTool().execute(chat_id=env["old"], folder_id="root"))
        assert _chat_on_disk(env, r2["conversation"]["conversationId"])["groupId"] is None
        assert run(ChatForkTool().execute(chat_id=env["old"], folder_id="grp_nope"))["error"]

    def test_fork_of_a_trunk_is_rooted_at_the_trunk(self, env):
        r = run(ChatForkTool().execute(chat_id=env["cur"]))
        fork = _chat_on_disk(env, r["conversation"]["conversationId"])
        assert fork["lineageRootId"] == env["cur"]


# ── registration seam ──────────────────────────────────────────────

class TestRegistration:

    def test_category_registered_and_enabled(self):
        from app.mcp import builtin_tools as bt
        assert "conversation_organize" in bt.BUILTIN_TOOL_CATEGORIES
        assert bt.BUILTIN_TOOL_CATEGORIES["conversation_organize"]["enabled_by_default"] is True
        names = {cls().name for cls in bt.get_builtin_tools_for_category("conversation_organize")}
        assert names == set(ORGANIZE_TOOL_NAMES) == {
            "folder_list", "folder_create", "folder_update", "folder_delete",
            "chat_move", "chat_rename", "chat_fork"}

    def test_results_are_medium_trust(self):
        from app.mcp.tool_result_demarcation import classify_trust
        for n in ORGANIZE_TOOL_NAMES:
            assert classify_trust(n) == "medium"

    def test_no_tool_takes_conversation_id(self):
        # The executor overwrites that kwarg with the calling chat's id.
        from app.mcp.tools import conversation_organize_tools as m
        for cls in m.ORGANIZE_TOOLS:
            assert "conversation_id" not in cls.InputSchema.model_fields, cls


# ── frontend seam (static): tool_display → window event → sync ─────

class TestFrontendSeam:
    """chatApi.ts must raise the event for every organize tool and
    ChatContext must answer it, or a mid-turn rename/move of the CURRENT
    conversation is pushed back stale after the turn."""

    def test_chat_api_dispatches_for_every_tool(self):
        import re
        src = open("frontend/src/apis/chatApi.ts", encoding="utf-8").read()
        m = re.search(r"/\^\(folder_\(([^)]*)\)\|chat_\(([^)]*)\)\)\$/", src)
        assert m, "organize tool_display handler missing in chatApi.ts"
        covered = {f"folder_{x}" for x in m.group(1).split("|")} | \
                  {f"chat_{x}" for x in m.group(2).split("|")}
        assert covered == set(ORGANIZE_TOOL_NAMES)
        assert "conversationOrganizedFromBackend" in src

    def test_chat_context_listens_and_syncs(self):
        src = open("frontend/src/context/ChatContext.tsx", encoding="utf-8").read()
        i = src.index("window.addEventListener('conversationOrganizedFromBackend'")
        handler = src[src.rindex("const handleOrganizedFromBackend", 0, i):i]
        assert "handleFoldersChanged()" in handler
        assert "syncWithServerRef.current?.()" in handler
        assert "window.removeEventListener('conversationOrganizedFromBackend'" in src
