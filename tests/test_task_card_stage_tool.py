"""
Tests for the ``task_card_stage`` builtin (app/mcp/tools/task_card_stage.py).

The tool is the direct, in-tool-list competitor to external "delegate /
orchestrate" MCP tools.  It STAGES a card (save + bind with run_id=None,
the /goal path) — it never launches.  Covered:

  - grammar mode (no root) returns the task_cards skill body
  - valid root → card saved (source="agent") + staged binding in this chat
  - invalid root → error, NOTHING saved
  - missing name with root → error, nothing saved
  - no conversation context → saved to deck, staged=False, no binding
  - registration: first in the task_cards category; surfaces as a
    ``[DIRECT]`` tool through create_secure_mcp_tools
  - usage rule 4 names the tool; skill prompt no longer routes to
    ``delegate-tasks``
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import Mock, patch

import pytest

# Reuse the project/ziya_home scaffolding from the sibling suite so the
# storage resolution path under test is identical.
from tests.test_task_card_tools import _make_env  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _make_env(tmp_path)
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: e["ziya_home"])
    from app.context import set_project_root
    set_project_root(e["project_root"])
    return e


VALID_ROOT = {
    "block_type": "group", "name": "pipeline", "on_failure": "stop",
    "body": [
        {"block_type": "task", "name": "a", "instructions": "do A, then say so", "body": []},
        {"block_type": "task", "name": "b", "instructions": "do B, then say so", "body": []},
    ],
}


def _cards(env):
    from app.storage.task_cards import TaskCardStorage
    return TaskCardStorage(env["project_dir"]).list()


def _bindings(env, chat_id):
    p = env["project_dir"] / "chats" / f"{chat_id}.bindings.json"
    return json.loads(p.read_text()) if p.exists() else []


# ── grammar mode ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_root_returns_task_cards_grammar(env):
    from app.mcp.tools.task_card_stage import TaskCardStageTool
    from app.data.built_in_skills import get_skill_by_id

    out = await TaskCardStageTool().execute()
    assert out.get("success") is True
    assert out.get("staged") is False
    assert out["grammar"] == get_skill_by_id("task_cards")["prompt"]
    assert "## Block grammar" in out["grammar"]
    assert _cards(env) == []  # nothing saved by a grammar request


# ── staging ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_root_saves_and_stages_in_current_chat(env):
    from app.mcp.tools.task_card_stage import TaskCardStageTool
    from app.context import set_conversation_id

    chat_id = "c_" + uuid.uuid4().hex[:8]
    set_conversation_id(chat_id)

    out = await TaskCardStageTool().execute(
        root=VALID_ROOT, name="Two-stage", description="A then B")
    assert out.get("success") is True, out
    assert out["staged"] is True
    assert out["binding_id"]

    cards = _cards(env)
    assert [c.id for c in cards] == [out["card_id"]]
    assert cards[0].name == "Two-stage"
    assert cards[0].source == "agent"
    assert cards[0].root.block_type == "group"

    binds = _bindings(env, chat_id)
    assert len(binds) == 1
    b = binds[0]
    assert b["id"] == out["binding_id"]
    assert b["card_id"] == out["card_id"]
    assert b["run_id"] is None, "tool must STAGE, never launch"
    assert b["anchor_message_id"] is None

    # The response must not tell the model the card is running.
    assert "running" not in out["message"].lower() or "do not claim" in out["message"]


@pytest.mark.asyncio
async def test_invalid_root_is_rejected_and_nothing_saved(env):
    from app.mcp.tools.task_card_stage import TaskCardStageTool
    from app.context import set_conversation_id

    chat_id = "c_" + uuid.uuid4().hex[:8]
    set_conversation_id(chat_id)

    # for_each with no source is a structural error the launch validator
    # refuses; the tool must refuse it too, before any write.
    bad = {"block_type": "repeat", "name": "loop", "repeat_mode": "for_each",
           "body": [{"block_type": "task", "name": "t",
                     "instructions": "x", "body": []}]}
    out = await TaskCardStageTool().execute(root=bad, name="Bad")
    assert out.get("error") is True
    assert out.get("staged") is False
    assert out["errors"], "expected the validator's findings to be surfaced"
    assert _cards(env) == []
    assert _bindings(env, chat_id) == []


@pytest.mark.asyncio
async def test_root_without_name_is_rejected(env):
    from app.mcp.tools.task_card_stage import TaskCardStageTool
    out = await TaskCardStageTool().execute(root=VALID_ROOT)
    assert out.get("error") is True
    assert "name" in out["message"]
    assert _cards(env) == []


@pytest.mark.asyncio
async def test_no_conversation_saves_to_deck_only(env, monkeypatch):
    from app.mcp.tools import task_card_stage as mod
    monkeypatch.setattr("app.context.get_conversation_id_or_none", lambda: None)

    out = await mod.TaskCardStageTool().execute(root=VALID_ROOT, name="Deck only")
    assert out.get("success") is True
    assert out["staged"] is False
    assert out["binding_id"] is None
    assert [c.name for c in _cards(env)] == ["Deck only"]
    assert not list((env["project_dir"] / "chats").glob("*.bindings.json"))


@pytest.mark.asyncio
async def test_no_project_context_degrades_cleanly(tmp_path, monkeypatch):
    from app.mcp.tools.task_card_stage import TaskCardStageTool
    from app.context import set_project_root
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: tmp_path / "empty_home")
    set_project_root(str(tmp_path / "unregistered"))
    monkeypatch.delenv("ZIYA_USER_CODEBASE_DIR", raising=False)
    out = await TaskCardStageTool().execute(root=VALID_ROOT, name="X")
    assert out.get("error") is True


# ── registration / seam ──────────────────────────────────────────────────────

def test_registered_first_in_task_cards_category():
    from app.mcp.builtin_tools import get_task_card_tools
    from app.mcp.tools.task_card_stage import TaskCardStageTool
    tools = get_task_card_tools()
    assert tools[0] is TaskCardStageTool
    assert len({t.__name__ for t in tools}) == len(tools)


def test_surfaces_as_direct_tool_in_secure_tool_list(monkeypatch):
    """Seam: the tool must reach the model's tool list, prefixed [DIRECT]."""
    from app.mcp import enhanced_tools
    enhanced_tools.invalidate_secure_tools_cache()
    monkeypatch.setenv("ZIYA_SECURE_MCP", "1")

    mgr = Mock()
    mgr.is_initialized = True
    mgr.server_configs = {}
    mgr.clients = {}
    mgr.get_all_tools.return_value = []

    pool = Mock()
    with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch("app.mcp.connection_pool.get_connection_pool", return_value=pool), \
         patch("app.mcp.builtin_tools.is_builtin_category_enabled",
               side_effect=lambda c: c == "task_cards"):
        from app.mcp.builtin_tools import invalidate_category_cache
        invalidate_category_cache()
        tools = enhanced_tools.create_secure_mcp_tools()
    enhanced_tools.invalidate_secure_tools_cache()

    stage = [t for t in tools if t.name == "task_card_stage"]
    assert len(stage) == 1
    assert stage[0].description.startswith("[DIRECT] ")
    assert "parallel" in stage[0].description.lower()


def test_usage_rule_names_the_tool(monkeypatch):
    from tests.test_builtin_tool_precedence import _render_usage_rules
    rules = _render_usage_rules(monkeypatch)
    assert "task_card_stage" in rules
    assert "task_decomposition" not in rules


def test_task_cards_skill_no_longer_routes_to_swarm():
    from app.data.built_in_skills import get_skill_by_id
    body = get_skill_by_id("task_cards")["prompt"]
    assert "delegate-tasks" not in body
    assert "task_card_stage" in body
    assert "Never state that the card is running" in body
