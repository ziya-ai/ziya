"""
A model revision of a task card (task_card_write) moves the card's tile to
the turn that revised it, instead of leaving it, unchanged-looking, where it
was first staged.

Seams covered:
  * TaskCardWriteTool in a conversation -> the staged binding is re-staged
    (created_at now, anchor cleared, previous placement in ``revisions``),
    and the tool result carries ``binding_id`` (what chatApi.ts keys the
    tile refresh on);
  * a card that was only RUN in the chat gets a new staged binding; the
    launched one is left alone;
  * no binding of the card in the chat / no conversation -> nothing;
  * the revision fields survive the GET /task-bindings read path.
"""
import json
import time
import uuid

import pytest

from tests.test_task_card_tools import _make_card, _make_env


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _make_env(tmp_path)
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: e["ziya_home"])
    from app.context import set_conversation_id, set_project_root
    set_project_root(e["project_root"])
    yield e
    set_conversation_id("")


def _bindings(env, chat_id):
    p = env["project_dir"] / "chats" / f"{chat_id}.bindings.json"
    return json.loads(p.read_text()) if p.exists() else []


def _seed(env, chat_id, card_id, run_id=None, created_at=1_000, anchor=None):
    p = env["project_dir"] / "chats" / f"{chat_id}.bindings.json"
    rows = _bindings(env, chat_id)
    bid = "b_" + uuid.uuid4().hex[:8]
    rows.append({"id": bid, "chat_id": chat_id, "card_id": card_id, "run_id": run_id,
                 "anchor_message_id": anchor, "created_at": created_at})
    p.write_text(json.dumps(rows))
    return bid


async def _write(chat_id, card_id, **kw):
    from app.context import set_conversation_id
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    set_conversation_id(chat_id)
    return await TaskCardWriteTool().execute(card_id=card_id, **kw)


@pytest.mark.asyncio
async def test_write_moves_the_staged_tile_to_now(env):
    cid = _make_card(env)
    chat = "c_" + uuid.uuid4().hex[:8]
    bid = _seed(env, chat, cid, created_at=1_000, anchor="m-early")
    before = int(time.time() * 1000)
    out = await _write(chat, cid, name="Renamed")
    assert out["success"] is True
    assert out["binding_id"] == bid
    [b] = _bindings(env, chat)
    assert b["created_at"] >= before
    assert b["anchor_message_id"] is None
    assert b["revision"] == 1
    assert b["revisions"] == [{"created_at": 1_000, "anchor_message_id": "m-early"}]
    # A second revision keeps the history and counts it.
    first_move = b["created_at"]
    await _write(chat, cid, description="again")
    [b] = _bindings(env, chat)
    assert b["revision"] == 2
    assert [r["created_at"] for r in b["revisions"]] == [1_000, first_move]


@pytest.mark.asyncio
async def test_card_only_run_here_gets_a_new_staged_tile(env):
    cid = _make_card(env)
    chat = "c_" + uuid.uuid4().hex[:8]
    launched = _seed(env, chat, cid, run_id="r_1", created_at=1_000)
    out = await _write(chat, cid, name="v2")
    rows = {b["id"]: b for b in _bindings(env, chat)}
    assert out["binding_id"] in rows and out["binding_id"] != launched
    new = rows[out["binding_id"]]
    assert new["run_id"] is None and new["revision"] == 1
    assert new["revision_after_run_id"] == "r_1"
    assert rows[launched]["created_at"] == 1_000 and "revision" not in rows[launched]


@pytest.mark.asyncio
async def test_no_tile_of_this_card_in_the_chat_means_no_tile(env):
    cid = _make_card(env)
    other = _make_card(env, "Other")
    chat = "c_" + uuid.uuid4().hex[:8]
    _seed(env, chat, other)
    out = await _write(chat, cid, name="x")
    assert out["success"] is True and "binding_id" not in out
    assert len(_bindings(env, chat)) == 1


@pytest.mark.asyncio
async def test_without_a_conversation_nothing_moves(env):
    cid = _make_card(env)
    chat = "c_" + uuid.uuid4().hex[:8]
    _seed(env, chat, cid, created_at=1_000)
    out = await _write("", cid, name="x")
    assert out["success"] is True and "binding_id" not in out
    assert _bindings(env, chat)[0]["created_at"] == 1_000


@pytest.mark.asyncio
async def test_revision_fields_reach_the_bindings_read_path(env):
    from app.api.task_bindings import list_task_bindings
    cid = _make_card(env)
    chat = "c_" + uuid.uuid4().hex[:8]
    _seed(env, chat, cid, created_at=1_000)
    await _write(chat, cid, name="x")
    [b] = list_task_bindings(env["project_id"], chat)
    d = b.model_dump()
    assert d["revision"] == 1 and d["revisions"][0]["created_at"] == 1_000
