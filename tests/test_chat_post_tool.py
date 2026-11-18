"""The two entry points to app/utils/chat_post.py: the ``chat_post`` builtin
tool and ``POST /api/v1/chats/{id}/posts``.  The logic is covered in
tests/test_chat_post.py; here only that each surface reaches it, with the
source conversation taken from the right place, and that the tool is
actually registered where the model's tool list is built.
"""
import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.chat import ChatCreate
from app.storage.chats import ChatStorage
from app.utils import chat_post as cp
from app.utils.paths import get_project_dir

P = "proj-p"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / ".ziya"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    from app.storage import chat_index
    chat_index.invalidate()
    cp._reset_for_tests()
    pdir = home / "projects" / P
    (pdir / "chats").mkdir(parents=True)
    now = int(time.time() * 1000)
    (pdir / "project.json").write_text(json.dumps({
        "id": P, "name": P, "path": f"/tmp/{P}",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": now, "lastAccessedAt": now}))
    src = ChatStorage(pdir).create(ChatCreate(title="Src"))
    dst = ChatStorage(pdir).create(ChatCreate(title="Dst"))
    return src.id, dst.id


def _messages(cid):
    return json.loads((get_project_dir(P) / "chats" / f"{cid}.json").read_text())["messages"]


def test_tool_posts_from_the_current_conversation(env):
    src, dst = env
    from app.context import set_conversation_id
    from app.mcp.tools.chat_post_tool import ChatPostTool
    set_conversation_id(src)
    out = asyncio.run(ChatPostTool().execute(chat_id=dst, text="hello over there"))
    assert out["success"] and out["delivery"] == "appended"
    assert out["link"] == f"ziya://chat/{dst}/0"
    m = _messages(dst)[-1]
    assert m["role"] == "peer" and m["peerPost"]["fromChatId"] == src
    assert m["peerPost"]["text"] == "hello over there"
    # Refusal comes back as a tool error, not an exception.
    bad = asyncio.run(ChatPostTool().execute(chat_id=src, text="x"))
    assert bad["error"] is True and "itself" in bad["message"]
    assert _messages(src) == []


def test_tool_is_in_the_conversation_organize_category():
    from app.mcp.builtin_tools import get_builtin_tools_for_category
    names = [t().name for t in get_builtin_tools_for_category("conversation_organize")]
    assert "chat_post" in names and "chat_move" in names


def test_endpoint_posts_and_maps_refusals_to_status(env):
    src, dst = env
    from app.api.chats import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post(f"/api/v1/chats/{dst}/posts", json={"sourceChatId": src, "text": "via http"})
    assert r.status_code == 200, r.text
    assert r.json()["delivery"] == "appended"
    assert _messages(dst)[-1]["peerPost"]["text"] == "via http"
    assert client.post(f"/api/v1/chats/{src}/posts",
                       json={"sourceChatId": src, "text": "x"}).status_code == 400
    assert client.post("/api/v1/chats/nope/posts",
                       json={"sourceChatId": src, "text": "x"}).status_code == 404
    assert client.post(f"/api/v1/chats/{dst}/posts",
                       json={"sourceChatId": src, "text": "x", "extra": 1}).status_code == 422
