"""
Tests for the task_card_write scope-preservation guard.

Two layers:
  1. ``find_scope_violations`` — the pure comparison: preserved scopes
     pass; edited / dropped-id / added scopes are findings; None-vs-
     empty scope normalization does not false-positive.
  2. The TOOL seam — TaskCardWriteTool must actually call the guard
     and refuse the write (a guard that exists but is never invoked
     protects nothing), while text/structure edits still save.

Fixture pattern mirrors tests/test_task_card_tools.py.
"""

import json
import time
import uuid

import pytest

from app.utils.task_card_write_guard import find_scope_violations


# ── Layer 1: the pure guard ─────────────────────────────────────

def _tree(scope=None, bid="b-1", extra_child=None):
    body = [{
        "block_type": "task", "id": "t-1", "name": "step",
        "instructions": "do it", "body": [],
        **({"scope": scope} if scope is not None else {}),
    }]
    if extra_child:
        body.append(extra_child)
    return {"block_type": "group", "id": bid, "name": "", "body": body}


SHELL_SCOPE = {"paths": [], "tools": [], "skills": [],
               "shell_commands": ["pytest"]}


class TestGuard:
    def test_identical_trees_pass(self):
        assert find_scope_violations(
            _tree(SHELL_SCOPE), _tree(SHELL_SCOPE)) == []

    def test_text_edit_with_preserved_scope_passes(self):
        stored = _tree(SHELL_SCOPE)
        submitted = _tree(SHELL_SCOPE)
        submitted["body"][0]["instructions"] = "do it better"
        assert find_scope_violations(stored, submitted) == []

    def test_edited_scope_is_refused(self):
        widened = dict(SHELL_SCOPE, shell_commands=["pytest", "rm"])
        found = find_scope_violations(_tree(SHELL_SCOPE), _tree(widened))
        assert len(found) == 1
        assert "differs" in found[0]

    def test_dropped_id_on_scope_bearing_block_is_refused(self):
        stored = _tree(SHELL_SCOPE)
        submitted = _tree(SHELL_SCOPE)
        # The model rewrote the tree and lost the task's id — the
        # exact accident that orphans a signed approval.
        submitted["body"][0]["id"] = ""
        found = find_scope_violations(stored, submitted)
        # Missing at the stored id AND appearing as an id-less scope.
        assert any("orphan" in f for f in found)
        assert any("no id" in f for f in found)

    def test_added_scope_is_refused(self):
        found = find_scope_violations(_tree(), _tree(SHELL_SCOPE))
        assert len(found) == 1
        assert "cannot be granted" in found[0]

    def test_removed_scope_is_refused(self):
        found = find_scope_violations(_tree(SHELL_SCOPE), _tree())
        assert len(found) == 1
        assert "orphan" in found[0]

    def test_none_vs_empty_scope_is_not_a_violation(self):
        # An agent echoing scope:{} (or a fully-defaulted dump) where
        # the stored card has scope:null must not false-positive.
        empty = {"paths": [], "tools": [], "skills": []}
        assert find_scope_violations(_tree(), _tree(empty)) == []
        assert find_scope_violations(_tree(empty), _tree()) == []

    def test_sparse_vs_full_dump_of_same_scope_compare_equal(self):
        sparse = {"shell_commands": ["pytest"]}
        full = {"paths": [], "cwd": None, "tools": [], "skills": [],
                "shell_commands": ["pytest"], "shell_timeout_secs": None,
                "model_tier": None, "model_name": None,
                "model_id_override": None, "model_endpoint": None}
        assert find_scope_violations(_tree(sparse), _tree(full)) == []

    def test_structure_edit_without_scope_changes_passes(self):
        stored = _tree(SHELL_SCOPE)
        submitted = _tree(SHELL_SCOPE, extra_child={
            "block_type": "task", "name": "new step",
            "instructions": "added", "body": [],
        })
        assert find_scope_violations(stored, submitted) == []


# ── Layer 2: the tool seam ──────────────────────────────────────

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


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _make_env(tmp_path)
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: e["ziya_home"])
    from app.context import set_project_root
    set_project_root(e["project_root"])
    return e


def _make_scoped_card(env):
    """A card whose task block carries a shell escalation."""
    from app.storage.task_cards import TaskCardStorage
    from app.models.task_card import TaskCardCreate, Block
    storage = TaskCardStorage(env["project_dir"])
    card = storage.create(TaskCardCreate(name="Scoped", root=Block(**{
        "block_type": "group", "name": "",
        "body": [{"block_type": "task", "name": "step",
                  "instructions": "run tests",
                  "scope": {"shell_commands": ["pytest"]},
                  "body": []}],
    })))
    return storage, card


@pytest.mark.asyncio
async def test_tool_refuses_scope_edit(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_scoped_card(env)
    root = card.root.model_dump()
    root["body"][0]["scope"]["shell_commands"] = ["pytest", "rm"]
    out = await TaskCardWriteTool().execute(card_id=card.id, root=root)
    assert out.get("error") is True
    assert "permissions" in out["message"].lower()
    # And the card on disk is untouched.
    reloaded = storage.get(card.id)
    assert reloaded.root.body[0].scope.shell_commands == ["pytest"]


@pytest.mark.asyncio
async def test_tool_refuses_dropped_id_on_scoped_block(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_scoped_card(env)
    root = card.root.model_dump()
    root["body"][0]["id"] = ""  # would mint a fresh id → orphan approval
    out = await TaskCardWriteTool().execute(card_id=card.id, root=root)
    assert out.get("error") is True
    reloaded = storage.get(card.id)
    assert reloaded.root.body[0].scope.shell_commands == ["pytest"]


@pytest.mark.asyncio
async def test_tool_allows_text_edit_preserving_scope(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_scoped_card(env)
    root = card.root.model_dump()
    root["body"][0]["instructions"] = "run tests twice"
    out = await TaskCardWriteTool().execute(card_id=card.id, root=root)
    assert out.get("success") is True
    reloaded = storage.get(card.id)
    assert reloaded.root.body[0].instructions == "run tests twice"
    # Scope and id preserved — the approval key is intact.
    assert reloaded.root.body[0].id == card.root.body[0].id
    assert reloaded.root.body[0].scope.shell_commands == ["pytest"]


@pytest.mark.asyncio
async def test_tool_name_description_writes_bypass_guard(env):
    # A write with no root cannot change scope; guard must not block it.
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    _, card = _make_scoped_card(env)
    out = await TaskCardWriteTool().execute(card_id=card.id, name="Renamed")
    assert out.get("success") is True
