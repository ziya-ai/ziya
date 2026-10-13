"""
resolve_chat_home and the server paths that used to act on the window's
project instead of the conversation's home.

Origin of the 2026-10-02 shadow: context_management._resolve_chat_for_request
looked the conversation up only in the project of the request's root (the
WINDOW's project) and, not finding a global conversation homed elsewhere,
auto-vivified an empty "New Conversation" record there (no projectId) and
pointed chat_index at it.  That 0-message copy is what the handoff commit
later ran against.

DIRECTION: against the unpatched context_management, the global-home test
creates A/chats/<id>.json and leaves B's record untouched.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

from app.storage.chats import ChatStorage
from app.utils.chat_home import resolve_chat_home

CID = "conv-global"


def _msgs(n):
    return [{"id": f"m{i}", "role": "human" if i % 2 == 0 else "assistant",
             "content": f"message {i}", "timestamp": i} for i in range(n)]


def _put(project_dir: Path, chat_id=CID, **extra):
    (project_dir / "chats").mkdir(parents=True, exist_ok=True)
    s = ChatStorage(project_dir)
    now = int(time.time() * 1000)
    rec = {"id": chat_id, "title": extra.pop("title", "t"),
           "messages": extra.pop("messages", []), "createdAt": now,
           "lastActiveAt": now, "_version": now, "contextIds": [], "skillIds": [],
           "additionalFiles": [], **extra}
    s._write_json(s._chat_file(chat_id), rec)


# ── helper rules ────────────────────────────────────────────────────

@pytest.fixture
def projects(tmp_path):
    root = tmp_path / "projects"
    for p in ("A", "B", "C"):
        (root / p / "chats").mkdir(parents=True)
    (root / "_path_index.json").write_text("{}")
    return root


def test_local_copy_with_messages_wins(projects):
    _put(projects / "A", messages=_msgs(2))
    _put(projects / "B", messages=_msgs(9))
    pid, _, raw = resolve_chat_home("A", projects / "A", CID)
    assert pid == "A" and len(raw["messages"]) == 2


def test_empty_local_copy_is_displaced_by_fullest_sibling(projects):
    _put(projects / "A", messages=[], title="New Conversation")
    _put(projects / "B", messages=_msgs(3))
    _put(projects / "C", messages=_msgs(6))
    pid, storage, raw = resolve_chat_home("A", projects / "A", CID)
    assert pid == "C" and len(raw["messages"]) == 6
    assert storage._chat_file(CID).parent.parent.name == "C"


def test_empty_local_copy_kept_when_siblings_are_empty_too(projects):
    _put(projects / "A", messages=[], title="here")
    _put(projects / "B", messages=[], title="there")
    pid, _, raw = resolve_chat_home("A", projects / "A", CID)
    assert pid == "A" and raw["title"] == "here"


def test_missing_locally_resolves_an_empty_sibling(projects):
    """A handoff continuation has no messages by construction; loading it
    from a window on another project must still find it."""
    _put(projects / "B", messages=[], title="meta fixes (2)", lineageKind="handoff")
    pid, _, raw = resolve_chat_home("A", projects / "A", CID)
    assert pid == "B" and raw["title"] == "meta fixes (2)"


def test_missing_everywhere_is_none(projects):
    assert resolve_chat_home("A", projects / "A", CID) is None


# ── server seams: window on A, global conversation homed in B ──────

@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    roots, index = {}, {}
    for pid in ("proj_A", "proj_B"):
        root = tmp_path / pid
        root.mkdir()
        (root / "f.txt").write_text("hello\n")
        roots[pid] = str(root.resolve())
        pdir = projects_dir / pid
        (pdir / "chats").mkdir(parents=True)
        now = int(time.time() * 1000)
        (pdir / "project.json").write_text(json.dumps({
            "id": pid, "name": pid, "path": roots[pid], "createdAt": now,
            "lastAccessedAt": now,
            "settings": {"defaultContextIds": [], "defaultSkillIds": []}}))
        index[roots[pid]] = pid
    (projects_dir / "_path_index.json").write_text(json.dumps(index))
    _put(projects_dir / "proj_B", messages=_msgs(4), title="meta fixes",
         projectId="proj_B", isGlobal=True)

    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    monkeypatch.setattr("app.mcp.tools.context_management.get_ziya_home",
                        lambda: ziya_home, raising=False)
    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer",
        lambda: type("X", (), {"is_expired": lambda *a, **kw: False})(),
        raising=False)
    from app.storage import chat_index
    chat_index.invalidate()
    from app.context import set_conversation_id, set_project_root
    set_conversation_id(CID)
    set_project_root(roots["proj_A"])
    return projects_dir, roots


def _read(projects_dir, pid):
    s = ChatStorage(projects_dir / pid)
    return s._read_json(s._chat_file(CID))


def test_context_tool_from_other_window_writes_the_real_record(two_projects):
    projects_dir, _ = two_projects
    from app.mcp.tools.context_management import ContextAddFileTool
    result = asyncio.get_event_loop().run_until_complete(
        ContextAddFileTool().execute(path="f.txt"))
    assert result.get("success") is True, result
    # No shadow in the window's project ...
    assert not (projects_dir / "proj_A" / "chats" / f"{CID}.json").exists()
    # ... and the real record received the write, messages intact.
    home = _read(projects_dir, "proj_B")
    assert "f.txt" in home["additionalFiles"]
    assert len(home["messages"]) == 4


def test_auto_context_from_other_window_opens_the_real_record(two_projects):
    projects_dir, roots = two_projects
    from app.utils.auto_context import _open_chat
    opened = _open_chat(CID, roots["proj_A"])
    assert opened is not None
    storage, chat_file, chat_data = opened
    assert chat_file.parent.parent.name == "proj_B"
    assert len(chat_data["messages"]) == 4
    assert not (projects_dir / "proj_A" / "chats" / f"{CID}.json").exists()
