"""
Tests for the bead task-tree tracking system.

Covers:
  - BeadTree model properties (active, parked, path_to_root, children)
  - bead_create tool (active and parked creation, parent linking)
  - bead_complete tool (completion, parent resumption)
  - bead_status tool (tree rendering)
  - Storage round-trip (save/load via mock chat storage)
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.models.bead import Bead, BeadTree


# -- BeadTree model tests ---------------------------------------------------

def test_bead_tree_empty():
    tree = BeadTree()
    assert tree.active_bead is None
    assert tree.parked_beads == []
    assert tree.get_children("nonexistent") == []
    assert tree.get_path_to_root("nonexistent") == []


def test_bead_tree_single_active():
    b = Bead(content="root task", status="active")
    tree = BeadTree(beads=[b])
    assert tree.active_bead == b
    assert tree.parked_beads == []


def test_bead_tree_active_and_parked():
    root = Bead(id="b_root", content="main", status="parked")
    child = Bead(id="b_child", content="subtask", status="active", parent_id="b_root")
    parked = Bead(id="b_aside", content="side thread", status="parked", parent_id="b_root")
    tree = BeadTree(beads=[root, child, parked])

    assert tree.active_bead == child
    assert set(b.id for b in tree.parked_beads) == {"b_root", "b_aside"}


def test_bead_tree_get_children():
    root = Bead(id="b_root", content="main", status="active")
    c1 = Bead(id="b_c1", content="child 1", status="parked", parent_id="b_root")
    c2 = Bead(id="b_c2", content="child 2", status="completed", parent_id="b_root")
    tree = BeadTree(beads=[root, c1, c2])
    children = tree.get_children("b_root")
    assert len(children) == 2
    assert {c.id for c in children} == {"b_c1", "b_c2"}


def test_bead_tree_path_to_root():
    root = Bead(id="b_root", content="root", status="parked")
    mid = Bead(id="b_mid", content="mid", status="parked", parent_id="b_root")
    leaf = Bead(id="b_leaf", content="leaf", status="active", parent_id="b_mid")
    tree = BeadTree(beads=[root, mid, leaf])

    path = tree.get_path_to_root("b_leaf")
    assert [b.id for b in path] == ["b_leaf", "b_mid", "b_root"]


def test_bead_tree_path_to_root_single_node():
    root = Bead(id="b_root", content="root", status="active")
    tree = BeadTree(beads=[root])
    path = tree.get_path_to_root("b_root")
    assert [b.id for b in path] == ["b_root"]


# -- bead_create tool tests -------------------------------------------------

@pytest.mark.asyncio
async def test_bead_create_first_bead():
    """First bead in an empty tree has no parent."""
    with patch("app.storage.beads.load_bead_tree", return_value=BeadTree()), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCreateTool
        tool = BeadCreateTool()
        result = await tool.execute(content="fix the bug", status="active")

    assert result["ok"] is True
    assert result["status"] == "active"
    # The saved tree should have one bead with no parent
    saved_tree = mock_save.call_args[0][0]
    assert len(saved_tree.beads) == 1
    assert saved_tree.beads[0].parent_id is None
    assert saved_tree.beads[0].content == "fix the bug"


@pytest.mark.asyncio
async def test_bead_create_parks_active_when_new_active():
    """Creating an active bead parks the current active one."""
    existing = Bead(id="b_old", content="old task", status="active")
    tree = BeadTree(beads=[existing])

    with patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCreateTool
        tool = BeadCreateTool()
        result = await tool.execute(content="new subtask", status="active")

    assert result["ok"] is True
    saved_tree = mock_save.call_args[0][0]
    old = next(b for b in saved_tree.beads if b.id == "b_old")
    assert old.status == "parked"
    new = next(b for b in saved_tree.beads if b.id != "b_old")
    assert new.status == "active"
    assert new.parent_id == "b_old"


@pytest.mark.asyncio
async def test_bead_create_parked_does_not_change_active():
    """Creating a parked bead leaves the active one unchanged."""
    existing = Bead(id="b_active", content="main task", status="active")
    tree = BeadTree(beads=[existing])

    with patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCreateTool
        tool = BeadCreateTool()
        result = await tool.execute(content="note for later", status="parked")

    assert result["ok"] is True
    saved_tree = mock_save.call_args[0][0]
    active = next(b for b in saved_tree.beads if b.id == "b_active")
    assert active.status == "active"  # unchanged
    parked = next(b for b in saved_tree.beads if b.id != "b_active")
    assert parked.status == "parked"
    assert parked.parent_id == "b_active"


@pytest.mark.asyncio
async def test_bead_create_empty_content_rejected():
    from app.mcp.tools.bead_tools import BeadCreateTool
    tool = BeadCreateTool()
    result = await tool.execute(content="", status="active")
    assert result["ok"] is False


# -- bead_complete tool tests -----------------------------------------------

@pytest.mark.asyncio
async def test_bead_complete_resumes_parent():
    """Completing a bead resumes its parked parent."""
    parent = Bead(id="b_parent", content="main", status="parked")
    child = Bead(id="b_child", content="subtask", status="active", parent_id="b_parent")
    tree = BeadTree(beads=[parent, child])

    with patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute()

    assert result["ok"] is True
    assert result["completed"] == "b_child"
    assert result["resumed"] == "b_parent"
    saved_tree = mock_save.call_args[0][0]
    assert next(b for b in saved_tree.beads if b.id == "b_child").status == "completed"
    assert next(b for b in saved_tree.beads if b.id == "b_parent").status == "active"


@pytest.mark.asyncio
async def test_bead_complete_no_active_returns_error():
    tree = BeadTree(beads=[Bead(id="b_done", content="old", status="completed")])

    with patch("app.storage.beads.load_bead_tree", return_value=tree):
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute()

    assert result["ok"] is False
    assert "No active bead" in result["error"]


# -- bead_status tool tests -------------------------------------------------

@pytest.mark.asyncio
async def test_bead_status_empty():
    with patch("app.storage.beads.load_bead_tree", return_value=BeadTree()):
        from app.mcp.tools.bead_tools import BeadStatusTool
        tool = BeadStatusTool()
        result = await tool.execute()

    assert result["ok"] is True
    assert "empty" in result["tree"] or "No beads" in result.get("message", "")


@pytest.mark.asyncio
async def test_bead_status_shows_parked():
    tree = BeadTree(beads=[
        Bead(id="b_a", content="active task", status="active"),
        Bead(id="b_p", content="parked aside", status="parked",
             parent_id="b_a", context_hint="was discussing X"),
    ])
    with patch("app.storage.beads.load_bead_tree", return_value=tree):
        from app.mcp.tools.bead_tools import BeadStatusTool
        tool = BeadStatusTool()
        result = await tool.execute()

    assert result["ok"] is True
    assert "parked aside" in result["tree"]
    assert "active task" in result["tree"]
    assert "PARKED" in result["tree"]


# -- Ephemeral guard tests --------------------------------------------------

@pytest.mark.asyncio
async def test_bead_create_skips_in_global_ephemeral_mode(monkeypatch):
    """ZIYA_EPHEMERAL=1 means beads silently no-op."""
    monkeypatch.setenv("ZIYA_EPHEMERAL", "1")
    from app.mcp.tools.bead_tools import BeadCreateTool
    tool = BeadCreateTool()
    result = await tool.execute(content="should be skipped", status="active")
    assert result["ok"] is True
    assert result.get("skipped") is True


@pytest.mark.asyncio
async def test_bead_create_skips_for_ephemeral_conversation():
    """Conversation with no backing chat record → skip (frontend ephemeral)."""
    # Simulate: _resolve_chat_storage returns a storage where get() → None
    mock_storage = MagicMock()
    mock_storage.get.return_value = None

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=True):
        from app.mcp.tools.bead_tools import BeadCreateTool
        tool = BeadCreateTool()
        result = await tool.execute(content="should be skipped", status="active")

    assert result["ok"] is True
    assert result.get("skipped") is True
