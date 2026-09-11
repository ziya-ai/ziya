"""
Tests for the task-card read/write MCP tools (app/mcp/tools/task_card_tools.py).

These tools let an agent read and edit saved Task Card *definitions*
through TaskCardStorage, resolved from the request-scoped project_root
ContextVar.  Covered:
  - list: returns id/name/root_block_type for project cards
  - list bound_to_current_chat: filters by TaskBinding for the conv
  - read: returns the full card definition; unknown id errors
  - write: replaces root / name / description; bumps the card; unknown id errors
  - write with nothing to change errors
  - round-trip: write a new root, read it back, see the change
  - no project context degrades to a clean error (not a crash)
"""

import json
import time
import uuid
from pathlib import Path

import pytest


def _make_env(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    projects_dir.mkdir(parents=True)
    project_id = "p_" + uuid.uuid4().hex[:8]
    project_dir = projects_dir / project_id
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps({
        "id": project_id, "name": "Test",
        "path": str(project_root.resolve()),
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): project_id}))
    (project_dir / "task_cards").mkdir()
    (project_dir / "chats").mkdir()
    return {
        "project_root": str(project_root.resolve()),
        "ziya_home": ziya_home,
        "project_id": project_id,
        "project_dir": project_dir,
    }


def _make_card(env, name="Counter", root=None):
    """Create a card via TaskCardStorage; returns its id."""
    from app.storage.task_cards import TaskCardStorage
    from app.models.task_card import TaskCardCreate, Block
    storage = TaskCardStorage(env["project_dir"])
    root = root or {
        "block_type": "until", "name": "loop",
        "until_mode": "model", "until_condition": "done", "until_max": 5,
        "body": [{"block_type": "task", "name": "step",
                  "instructions": "do it", "body": []}],
    }
    card = storage.create(TaskCardCreate(name=name, root=Block(**root)))
    return card.id


def _write_binding(env, chat_id, card_id):
    bindings_path = env["project_dir"] / "chats" / f"{chat_id}.bindings.json"
    existing = []
    if bindings_path.exists():
        existing = json.loads(bindings_path.read_text())
    existing.append({
        "id": "b_" + uuid.uuid4().hex[:8], "chat_id": chat_id,
        "card_id": card_id, "run_id": "r_" + uuid.uuid4().hex[:8],
        "anchor_message_id": None, "created_at": int(time.time() * 1000),
    })
    bindings_path.write_text(json.dumps(existing))


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _make_env(tmp_path)
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: e["ziya_home"])
    from app.context import set_project_root
    set_project_root(e["project_root"])
    return e


# ── list ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_returns_project_cards(env):
    from app.mcp.tools.task_card_tools import TaskCardListTool
    cid = _make_card(env, "MyCard")
    out = await TaskCardListTool().execute()
    assert out["success"] is True
    ids = [c["id"] for c in out["cards"]]
    assert cid in ids
    card = next(c for c in out["cards"] if c["id"] == cid)
    assert card["name"] == "MyCard"
    assert card["root_block_type"] == "until"


@pytest.mark.asyncio
async def test_list_bound_to_current_chat_filters(env, monkeypatch):
    from app.mcp.tools.task_card_tools import TaskCardListTool
    from app.context import set_conversation_id
    bound = _make_card(env, "Bound")
    _make_card(env, "Unbound")  # exists but not bound
    chat_id = "c_" + uuid.uuid4().hex[:8]
    _write_binding(env, chat_id, bound)
    set_conversation_id(chat_id)
    out = await TaskCardListTool().execute(bound_to_current_chat=True)
    ids = [c["id"] for c in out["cards"]]
    assert ids == [bound]


@pytest.mark.asyncio
async def test_list_bound_without_conversation_fails_open(env):
    """With no resolvable conversation_id the binding filter cannot be
    applied, so the tool returns the UNFILTERED list plus a warning rather
    than erroring.

    Deliberate: ``bound_to_current_chat`` is a convenience filter, and
    failing the whole call over it would break a listing that is otherwise
    perfectly answerable.  This test previously asserted the opposite
    (hard error) and predates that change.
    """
    from app.mcp.tools.task_card_tools import TaskCardListTool
    from app.context import set_conversation_id
    set_conversation_id("")  # clear
    _make_card(env)
    out = await TaskCardListTool().execute(bound_to_current_chat=True)
    assert out.get("error") is not True
    assert out["count"] == 1          # unfiltered, not empty
    # The warning must name the reason so the model can tell an unfiltered
    # list from a filtered one that happened to match everything.
    assert "conversation_id" in str(out)


# ── read ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_returns_full_definition(env):
    from app.mcp.tools.task_card_tools import TaskCardReadTool
    cid = _make_card(env, "Readable")
    out = await TaskCardReadTool().execute(card_id=cid)
    assert out["success"] is True
    assert out["card"]["id"] == cid
    assert out["card"]["root"]["block_type"] == "until"
    assert out["card"]["root"]["body"][0]["instructions"] == "do it"


@pytest.mark.asyncio
async def test_read_unknown_id_errors(env):
    from app.mcp.tools.task_card_tools import TaskCardReadTool
    out = await TaskCardReadTool().execute(card_id="nope")
    assert out.get("error") is True
    assert "nope" in out["message"]


@pytest.mark.asyncio
async def test_read_empty_id_errors(env):
    from app.mcp.tools.task_card_tools import TaskCardReadTool
    out = await TaskCardReadTool().execute(card_id="")
    assert out.get("error") is True
    assert "required" in out["message"]


# ── write ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_replaces_root_and_round_trips(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool, TaskCardReadTool
    cid = _make_card(env, "Editable")
    # Replace the until body's instruction.
    new_root = {
        "block_type": "until", "name": "loop",
        "until_mode": "model", "until_condition": "counter is above 300",
        "until_max": 100,
        "body": [{"block_type": "task", "name": "step",
                  "instructions": "increase count by 20", "body": []}],
    }
    out = await TaskCardWriteTool().execute(card_id=cid, root=new_root)
    assert out["success"] is True
    # Read back and confirm the change landed.
    read = await TaskCardReadTool().execute(card_id=cid)
    root = read["card"]["root"]
    assert root["until_condition"] == "counter is above 300"
    assert root["until_max"] == 100
    assert root["body"][0]["instructions"] == "increase count by 20"


@pytest.mark.asyncio
async def test_write_name_and_description_only(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool, TaskCardReadTool
    cid = _make_card(env, "OldName")
    out = await TaskCardWriteTool().execute(
        card_id=cid, name="NewName", description="now described")
    assert out["success"] is True
    read = await TaskCardReadTool().execute(card_id=cid)
    assert read["card"]["name"] == "NewName"
    assert read["card"]["description"] == "now described"
    # Root unchanged (we didn't pass it).
    assert read["card"]["root"]["block_type"] == "until"


@pytest.mark.asyncio
async def test_write_nothing_to_change_errors(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    cid = _make_card(env)
    out = await TaskCardWriteTool().execute(card_id=cid)
    assert out.get("error") is True
    assert "Nothing to update" in out["message"]


@pytest.mark.asyncio
async def test_write_unknown_id_errors(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    out = await TaskCardWriteTool().execute(card_id="ghost", name="x")
    assert out.get("error") is True
    assert "ghost" in out["message"]


# ── no project context ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_project_context_degrades_cleanly(tmp_path, monkeypatch):
    # Point project_root at a path with no registered project.
    from app.context import set_project_root
    from app.mcp.tools.task_card_tools import TaskCardReadTool
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: tmp_path / "empty_home")
    set_project_root(str(tmp_path / "unregistered"))
    out = await TaskCardReadTool().execute(card_id="any")
    assert out.get("error") is True
    assert "project" in out["message"].lower()


# ── pre-run feedback: validate + write carry the launch validator ──
#
# A model authoring a card needs to learn about a defect BEFORE anyone
# spends a run on it.  Measured failure: a for_each source referencing the
# upstream block by NAME; nothing reported it until the loop ran with 0
# iterations.  Both tools now return the launch validator's findings.

_ROSTER_PIPELINE = {
    "block_type": "group", "name": "Pipeline", "body": [
        {"block_type": "task", "id": "plan", "name": "Build roster",
         "instructions": "emit the roster"},
        {"block_type": "repeat", "name": "Fan out", "repeat_mode": "for_each",
         "repeat_for_each_source": '{{sibling("Build roster").outputs.roster.docs}}',
         "body": [{"block_type": "task", "name": "One",
                   "instructions": "audit {{item}}"}]},
    ],
}


def _fixed(tree):
    import copy
    t = copy.deepcopy(tree)
    t["body"][1]["repeat_for_each_source"] = \
        '{{sibling("plan").outputs.roster.docs}}'
    return t


@pytest.mark.asyncio
async def test_validate_unsaved_root_reports_name_reference_with_fix(env):
    from app.mcp.tools.task_card_tools import TaskCardValidateTool
    out = await TaskCardValidateTool().execute(root=_ROSTER_PIPELINE)
    assert out["success"] is True
    assert out["ok"] is False
    assert out["launchable"] is False
    assert len(out["errors"]) == 1
    e = out["errors"][0]
    assert "block NAME" in e["message"]
    assert 'sibling("plan")' in e["message"]       # the fix, verbatim
    assert "Fan out" in e["path"]                  # locatable
    assert "refuse" in out["message"]


@pytest.mark.asyncio
async def test_validate_corrected_root_is_clean(env):
    from app.mcp.tools.task_card_tools import TaskCardValidateTool
    out = await TaskCardValidateTool().execute(root=_fixed(_ROSTER_PIPELINE))
    assert out["ok"] is True and out["errors"] == []
    assert "launchable" in out["message"]


@pytest.mark.asyncio
async def test_validate_saved_card_by_id(env):
    from app.mcp.tools.task_card_tools import TaskCardValidateTool
    cid = _make_card(env, "Broken", root=_ROSTER_PIPELINE)
    out = await TaskCardValidateTool().execute(card_id=cid)
    assert out["ok"] is False
    assert out["card_id"] == cid
    # The saved block carries the explicit id the author set.
    assert 'sibling("plan")' in out["errors"][0]["message"]


@pytest.mark.asyncio
async def test_validate_requires_root_or_card_id(env):
    from app.mcp.tools.task_card_tools import TaskCardValidateTool
    out = await TaskCardValidateTool().execute()
    assert out.get("error") is True


@pytest.mark.asyncio
async def test_validate_garbage_root_is_a_finding_not_a_crash(env):
    from app.mcp.tools.task_card_tools import TaskCardValidateTool
    out = await TaskCardValidateTool().execute(root={"block_type": 7})
    assert out["ok"] is False
    assert "not a valid block tree" in out["errors"][0]["message"]


@pytest.mark.asyncio
async def test_write_returns_validation_and_warns_in_message(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    cid = _make_card(env, "Card")
    out = await TaskCardWriteTool().execute(card_id=cid, root=_ROSTER_PIPELINE)
    assert out["success"] is True                  # the write still lands
    assert out["validation"]["ok"] is False
    assert "launch will refuse" in out["message"]
    assert 'sibling("plan")' in out["validation"]["errors"][0]["message"]
    # Fix it via the same tool: the response flips clean.
    out2 = await TaskCardWriteTool().execute(card_id=cid, root=_fixed(_ROSTER_PIPELINE))
    assert out2["validation"]["ok"] is True
    assert "Re-launch" in out2["message"]


@pytest.mark.asyncio
async def test_explicit_block_ids_survive_write(env):
    # The contract the skill now states: set an id, reference it, and the
    # save keeps it (only missing ids are generated).
    from app.mcp.tools.task_card_tools import TaskCardWriteTool, TaskCardReadTool
    cid = _make_card(env, "Card")
    await TaskCardWriteTool().execute(card_id=cid, root=_fixed(_ROSTER_PIPELINE))
    read = await TaskCardReadTool().execute(card_id=cid)
    body = read["card"]["root"]["body"]
    assert body[0]["id"] == "plan"
    assert body[1]["id"]                            # generated for the rest
    assert body[1]["repeat_for_each_source"] == \
        '{{sibling("plan").outputs.roster.docs}}'


def test_validate_tool_is_registered():
    from app.mcp.builtin_tools import get_task_card_tools
    names = [t.name for t in get_task_card_tools()]
    assert "task_card_validate" in names
