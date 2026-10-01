"""ziya:// links: the model-writable address of a project / conversation / turn.

Pinned end to end:
  - app/utils/ziya_links.py builds and parses the three shapes and rejects
    anything path-like (a link is followed by the browser, so an id that
    smuggles a slash must not parse);
  - chat_search / chat_read / chat_list attach a ``link`` to every
    conversation and match they return, and their descriptions carry the
    format hint, so the model learns the href where it gets the ids;
  - GET /api/v1/chats/{id}/locate resolves a conversation to its project
    across projects (what the frontend needs when a link points into a
    project other than the current one) and 404s for an unknown id.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.ziya_links import LINK_HINT, chat_link, parse_ziya_link, project_link


def test_build_and_parse_roundtrip():
    assert chat_link("abc-123") == "ziya://chat/abc-123"
    assert chat_link("abc-123", 12) == "ziya://chat/abc-123/12"
    assert chat_link("abc-123", None) == "ziya://chat/abc-123"
    assert project_link("p1") == "ziya://project/p1"
    assert parse_ziya_link("ziya://chat/abc-123/12") == {
        "kind": "chat", "conversationId": "abc-123", "messageIndex": 12}
    assert parse_ziya_link("ziya://chat/abc-123") == {
        "kind": "chat", "conversationId": "abc-123", "messageIndex": None}
    assert parse_ziya_link("ziya://project/p1/") == {"kind": "project", "projectId": "p1"}


@pytest.mark.parametrize("bad", [
    "https://example.com/chat/x", "ziya://chat/", "ziya://chat/../etc/passwd",
    "ziya://chat/a/b/c", "ziya://chat/a/notanumber", "ziya://folder/x", "", None,
])
def test_parse_rejects_non_links(bad):
    assert parse_ziya_link(bad) is None


# ── chat-history tools attach links ─────────────────────────────────────


def _seed(ziya_home: Path, project_id: str, chat_id: str, title: str, texts):
    d = ziya_home / "projects" / project_id
    (d / "chats").mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(
        {"id": project_id, "name": f"Project {project_id}", "path": str(ziya_home / project_id),
         "createdAt": 1_700_000_000_000, "lastAccessedAt": 1_700_000_000_000,
         "settings": {"defaultContextIds": [], "defaultSkillIds": []}}))
    msgs = [{"id": f"m{i}", "role": "human" if i % 2 == 0 else "assistant",
             "content": t, "timestamp": 1_700_000_000_000 + i} for i, t in enumerate(texts)]
    (d / "chats" / f"{chat_id}.json").write_text(json.dumps(
        {"id": chat_id, "projectId": project_id, "title": title, "messages": msgs,
         "createdAt": 1_700_000_000_000, "lastActiveAt": 1_700_000_000_500}))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    from app.storage import chat_index
    chat_index.invalidate()
    _seed(tmp_path, "p1", "conv-one", "Flux capacitor design",
          ["how does the flux capacitor work", "It needs 1.21 gigawatts."])
    _seed(tmp_path, "p2", "conv-two", "Other project", ["gigawatts again", "yes"])
    yield tmp_path
    chat_index.invalidate()


@pytest.mark.asyncio
async def test_chat_tools_attach_links(home):
    from app.mcp.tools.chat_history_tools import ChatListTool, ChatReadTool, ChatSearchTool
    scope = {"ziya_home": home, "project_id": "p1", "conversation_id": None}
    with patch("app.mcp.tools.chat_history_tools._resolve_scope", return_value=scope):
        search = await ChatSearchTool().execute(query="gigawatts", all_projects=True)
        assert not search.get("error"), search
        by_id = {r["conversationId"]: r for r in search["results"]}
        assert by_id["conv-one"]["link"] == "ziya://chat/conv-one"
        hit = by_id["conv-one"]["matches"][0]
        assert hit["link"] == chat_link("conv-one", hit["messageIndex"])

        read = await ChatReadTool().execute(chat_id="conv-two")
        assert read["link"] == "ziya://chat/conv-two"
        assert read["projectLink"] == "ziya://project/p2"
        assert read["messages"][0]["link"] == "ziya://chat/conv-two/0"

        listed = await ChatListTool().execute(all_projects=True)
        assert {c["link"] for c in listed["conversations"]} >= {
            "ziya://chat/conv-one", "ziya://chat/conv-two"}

    for tool in (ChatSearchTool, ChatReadTool, ChatListTool):
        assert LINK_HINT in tool().description


# ── locate endpoint ──────────────────────────────────────────────────────


def test_locate_endpoint_resolves_across_projects(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.chats import router
    app = FastAPI(); app.include_router(router)
    tc = TestClient(app)
    r = tc.get("/api/v1/chats/conv-two/locate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projectId"] == "p2"
    assert body["conversationId"] == "conv-two"
    assert body["title"] == "Other project"
    assert body["projectName"] == "Project p2"
    assert body["messageCount"] == 2
    assert body["link"] == "ziya://chat/conv-two"
    assert tc.get("/api/v1/chats/does-not-exist/locate").status_code == 404
    # Path-like ids never reach the filesystem.
    assert tc.get("/api/v1/chats/..%2Fetc/locate").status_code in (404, 422)
