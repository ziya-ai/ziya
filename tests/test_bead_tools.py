"""
Tests for the bead task-tree tracking system.

Covers:
  - BeadTree model properties (active, parked, path_to_root, children)
  - bead_create tool (active and parked creation, parent linking)
  - bead_complete tool (completion, parent resumption)
  - bead_status tool (tree rendering)
  - Storage round-trip (save/load via mock chat storage)
"""
import os
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
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=BeadTree()), \
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

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
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

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
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
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False):
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

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
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

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree):
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute()

    assert result["ok"] is False
    # Error-surfacing contract (see CHANGELOG): reason lives in "message";
    # "error" is a boolean flag the MCP manager keys on.
    assert result["error"] is True
    assert "No active bead" in result["message"]


# -- bead_status tool tests -------------------------------------------------

@pytest.mark.asyncio
async def test_bead_status_empty():
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=BeadTree()):
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
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree):
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


# -- Directive decoupling tests ---------------------------------------------
# The bead *directive* (system-prompt instructions) must be decoupled from
# per-conversation persistability.  Regression for: a transient
# _resolve_chat_storage() failure used to suppress the directive, so the
# model was never told beads exist and never created any ("not activated
# even deep in conversations").  The directive is now gated ONLY on the
# category being enabled and on global ephemeral mode.


def test_directive_survives_resolution_failure():
    """Per-conversation resolve failure must NOT suppress the directive."""
    from app.utils import bead_prompt
    with patch.dict(os.environ, {"ZIYA_EPHEMERAL": ""}), \
         patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
         patch("app.storage.beads._resolve_chat_storage",
               side_effect=ValueError("No project found for path: /x")):
        directive = bead_prompt.get_bead_directive()
    assert directive  # non-empty
    assert "bead_create" in directive


def test_directive_present_when_chat_missing_on_disk():
    """Chat not yet persisted must NOT suppress the directive."""
    from app.utils import bead_prompt
    fake_storage = MagicMock()
    fake_storage.get.return_value = None  # chat not on disk
    with patch.dict(os.environ, {"ZIYA_EPHEMERAL": ""}), \
         patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
         patch("app.storage.beads._resolve_chat_storage",
               return_value=(fake_storage, "conv-1")):
        directive = bead_prompt.get_bead_directive()
    assert "bead_create" in directive


def test_directive_suppressed_by_global_ephemeral(monkeypatch):
    """Global ephemeral mode is the ONLY thing that suppresses the directive."""
    from app.utils import bead_prompt
    monkeypatch.setenv("ZIYA_EPHEMERAL", "1")
    with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True):
        assert bead_prompt.get_bead_directive() == ""


def test_directive_suppressed_when_category_disabled(monkeypatch):
    from app.utils import bead_prompt
    monkeypatch.setenv("ZIYA_EPHEMERAL", "")
    with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=False):
        assert bead_prompt.get_bead_directive() == ""


def test_global_ephemeral_helper(monkeypatch):
    from app.utils import bead_prompt
    monkeypatch.setenv("ZIYA_EPHEMERAL", "1")
    assert bead_prompt._is_global_ephemeral() is True
    monkeypatch.setenv("ZIYA_EPHEMERAL", "true")
    assert bead_prompt._is_global_ephemeral() is True
    monkeypatch.setenv("ZIYA_EPHEMERAL", "")
    assert bead_prompt._is_global_ephemeral() is False


# -- message_index seam tests (bead-branching prerequisite) -----------------
# Bead.message_index records where in the user-visible conversation a bead was
# spawned — the seam that branch-from-bead truncates at (design/bead-branching.md).
# It was declared on the model but never populated; these pin the wiring.


class _FakeChat:
    def __init__(self, messages):
        self.messages = messages


def test_message_count_returns_len_when_chat_resolves():
    from app.storage import beads
    storage = MagicMock()
    storage.get.return_value = _FakeChat([{}, {}, {}])
    with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "c1")):
        assert beads.get_conversation_message_count("c1") == 3


def test_message_count_none_when_no_conversation_id():
    from app.storage import beads
    with patch.object(beads, "_get_conversation_id", return_value=None):
        assert beads.get_conversation_message_count(None) is None


def test_message_count_none_when_chat_unresolvable():
    from app.storage import beads
    with patch.object(beads, "_resolve_chat_storage",
                      side_effect=ValueError("No project found")):
        assert beads.get_conversation_message_count("c1") is None


def test_message_count_none_when_chat_missing():
    from app.storage import beads
    storage = MagicMock()
    storage.get.return_value = None
    with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "c1")):
        assert beads.get_conversation_message_count("c1") is None


def test_message_count_none_when_messages_not_list():
    from app.storage import beads
    storage = MagicMock()
    storage.get.return_value = _FakeChat(None)
    with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "c1")):
        assert beads.get_conversation_message_count("c1") is None


@pytest.mark.asyncio
async def test_bead_create_stamps_message_index():
    """bead_create records the seam from the live message count."""
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=BeadTree()), \
         patch("app.storage.beads.get_conversation_message_count", return_value=7), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCreateTool
        tool = BeadCreateTool()
        result = await tool.execute(content="investigate microburst drops", status="parked")

    assert result["ok"] is True
    saved_tree = mock_save.call_args[0][0]
    assert saved_tree.beads[0].message_index == 7


@pytest.mark.asyncio
async def test_bead_create_message_index_none_when_unavailable():
    """Seam unavailable (CLI / not-yet-synced) → message_index None, not an error."""
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=BeadTree()), \
         patch("app.storage.beads.get_conversation_message_count", return_value=None), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCreateTool
        tool = BeadCreateTool()
        result = await tool.execute(content="some thread", status="active")

    assert result["ok"] is True
    saved_tree = mock_save.call_args[0][0]
    assert saved_tree.beads[0].message_index is None


# -- inherit_beads_for_seam tests (bead-fork core) --------------------------
# The timeline rule that decides which beads come along on a split
# (design/bead-branching.md): message_index <= seam, chosen promoted active.


def _seam_tree(*beads):
    return BeadTree(beads=list(beads))


def test_inherit_seam_filters_by_message_index():
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(
        Bead(id="root", content="root task", status="parked", message_index=1),
        Bead(id="mid", content="microburst drops", status="parked", message_index=3),
        Bead(id="late", content="later thread", status="active", message_index=5),
    )
    seam, inherited, label = inherit_beads_for_seam(tree, "mid")
    assert seam == 3
    assert label == "microburst drops"
    # Inherited beads carry fresh ids; identify them by origin_bead_id.
    assert {b.origin_bead_id for b in inherited} == {"root", "mid"}  # late (5>3) dropped
    assert next(b for b in inherited if b.origin_bead_id == "mid").status == "active"
    assert next(b for b in inherited if b.origin_bead_id == "root").status == "parked"


def test_inherit_seam_parks_other_active():
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(
        Bead(id="root", content="root", status="active", message_index=1),
        Bead(id="target", content="target", status="parked", message_index=2),
    )
    _seam, inherited, _label = inherit_beads_for_seam(tree, "target")
    assert next(b for b in inherited if b.origin_bead_id == "target").status == "active"
    assert next(b for b in inherited if b.origin_bead_id == "root").status == "parked"


def test_inherit_seam_drops_none_message_index():
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(
        Bead(id="legacy", content="pre-feature", status="parked", message_index=None),
        Bead(id="target", content="target", status="parked", message_index=2),
    )
    _seam, inherited, _label = inherit_beads_for_seam(tree, "target")
    assert {b.origin_bead_id for b in inherited} == {"target"}   # legacy (None) dropped


def test_inherit_seam_does_not_mutate_source():
    from app.storage.beads import inherit_beads_for_seam
    src_active = Bead(id="root", content="root", status="active", message_index=1)
    tree = _seam_tree(
        src_active,
        Bead(id="target", content="target", status="parked", message_index=2),
    )
    inherit_beads_for_seam(tree, "target")
    assert src_active.status == "active"                    # copy semantics — source intact


def test_inherit_seam_missing_bead_raises():
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(Bead(id="a", content="a", status="parked", message_index=1))
    with pytest.raises(ValueError):
        inherit_beads_for_seam(tree, "nonexistent")


def test_inherit_seam_no_message_index_raises():
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(Bead(id="a", content="a", status="parked", message_index=None))
    with pytest.raises(ValueError):
        inherit_beads_for_seam(tree, "a")


def test_inherit_seam_assigns_fresh_ids():
    """Inherited beads get fresh ids — no cross-conversation id collision."""
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(
        Bead(id="root", content="root", status="parked", message_index=1),
        Bead(id="mid", content="mid", status="parked", message_index=2),
    )
    _seam, inherited, _label = inherit_beads_for_seam(tree, "mid", "src-conv")
    new_ids = {b.id for b in inherited}
    assert new_ids.isdisjoint({"root", "mid"})              # none reuse a source id
    assert all(b.id.startswith("bead_") for b in inherited)
    assert len(new_ids) == 2                                # still unique within the set


def test_inherit_seam_stamps_origin():
    """Origin backlink records source conversation + source bead id."""
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(
        Bead(id="root", content="root", status="parked", message_index=1),
        Bead(id="mid", content="mid", status="parked", message_index=2),
    )
    _seam, inherited, _label = inherit_beads_for_seam(tree, "mid", "src-conv")
    assert all(b.origin_conversation_id == "src-conv" for b in inherited)
    assert {b.origin_bead_id for b in inherited} == {"root", "mid"}
    # origin_bead_id is the SOURCE id, distinct from the bead's own fresh id
    for b in inherited:
        assert b.origin_bead_id != b.id


def test_inherit_seam_remaps_parent_chain():
    """parent_id is remapped to the parent's NEW id, preserving the tree."""
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(
        Bead(id="root", content="root", status="parked", message_index=1, parent_id=None),
        Bead(id="mid", content="mid", status="parked", message_index=2, parent_id="root"),
    )
    _seam, inherited, _label = inherit_beads_for_seam(tree, "mid", "src-conv")
    root_new = next(b for b in inherited if b.origin_bead_id == "root")
    mid_new = next(b for b in inherited if b.origin_bead_id == "mid")
    assert root_new.parent_id is None                       # root stays rootless
    assert mid_new.parent_id == root_new.id                 # remapped to fresh parent id
    assert mid_new.parent_id != "root"                      # not the stale source id


def test_inherit_seam_origin_none_when_source_not_given():
    """source_conversation_id is optional; origin_conversation_id falls back to None."""
    from app.storage.beads import inherit_beads_for_seam
    tree = _seam_tree(Bead(id="a", content="a", status="parked", message_index=1))
    _seam, inherited, _label = inherit_beads_for_seam(tree, "a")
    assert inherited[0].origin_conversation_id is None
    assert inherited[0].origin_bead_id == "a"               # source bead id still recorded


# -- resolve_origin_bead tests (completion-propagation along lineage) --------
# Completing a forked bead resolves its origin (design/bead-branching.md):
# the origin's parked note for that thread is now a stale lie.


def test_resolve_origin_completes_parked_origin():
    from app.storage import beads
    origin_tree = BeadTree(beads=[
        Bead(id="o_root", content="root", status="active", message_index=1),
        Bead(id="o_mid", content="microburst drops", status="parked",
             message_index=2, parent_id="o_root"),
    ])
    storage = MagicMock()
    with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "origin-conv")), \
         patch.object(beads, "load_bead_tree", return_value=origin_tree), \
         patch.object(beads, "save_bead_tree") as mock_save:
        resolved = beads.resolve_origin_bead("origin-conv", "o_mid")

    assert resolved == "o_mid"
    saved = mock_save.call_args[0][0]
    assert next(b for b in saved.beads if b.id == "o_mid").status == "completed"
    # parent was active already; stays active (only parked parents resume)
    assert next(b for b in saved.beads if b.id == "o_root").status == "active"


def test_resolve_origin_resumes_parked_parent():
    from app.storage import beads
    origin_tree = BeadTree(beads=[
        Bead(id="o_root", content="root", status="parked", message_index=1),
        Bead(id="o_mid", content="thread", status="parked",
             message_index=2, parent_id="o_root"),
    ])
    storage = MagicMock()
    with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "origin-conv")), \
         patch.object(beads, "load_bead_tree", return_value=origin_tree), \
         patch.object(beads, "save_bead_tree") as mock_save:
        beads.resolve_origin_bead("origin-conv", "o_mid")

    saved = mock_save.call_args[0][0]
    assert next(b for b in saved.beads if b.id == "o_root").status == "active"


def test_resolve_origin_skips_terminal_origin():
    from app.storage import beads
    for terminal in ("completed", "abandoned"):
        origin_tree = BeadTree(beads=[
            Bead(id="o_mid", content="thread", status=terminal, message_index=2),
        ])
        storage = MagicMock()
        with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "origin-conv")), \
             patch.object(beads, "load_bead_tree", return_value=origin_tree), \
             patch.object(beads, "save_bead_tree") as mock_save:
            resolved = beads.resolve_origin_bead("origin-conv", "o_mid")
        assert resolved is None                              # terminal → untouched
        mock_save.assert_not_called()


def test_resolve_origin_missing_bead_is_noop():
    from app.storage import beads
    origin_tree = BeadTree(beads=[Bead(id="other", content="x", status="parked", message_index=1)])
    storage = MagicMock()
    with patch.object(beads, "_resolve_chat_storage", return_value=(storage, "origin-conv")), \
         patch.object(beads, "load_bead_tree", return_value=origin_tree), \
         patch.object(beads, "save_bead_tree") as mock_save:
        resolved = beads.resolve_origin_bead("origin-conv", "gone")
    assert resolved is None
    mock_save.assert_not_called()


def test_resolve_origin_unresolvable_conversation_is_noop():
    from app.storage import beads
    with patch.object(beads, "_resolve_chat_storage",
                      side_effect=ValueError("origin chat deleted")):
        assert beads.resolve_origin_bead("gone-conv", "o_mid") is None


def test_resolve_origin_cascades_one_hop():
    """Fork-of-a-fork: resolving an origin that itself has an origin cascades."""
    from app.storage import beads
    # conv B's bead points back to conv A; completing in C resolves B, then A.
    tree_b = BeadTree(beads=[
        Bead(id="b_mid", content="thread", status="parked", message_index=2,
             origin_conversation_id="conv-a", origin_bead_id="a_mid"),
    ])
    tree_a = BeadTree(beads=[
        Bead(id="a_mid", content="thread", status="parked", message_index=2),
    ])
    storage = MagicMock()
    trees = {"conv-b": tree_b, "conv-a": tree_a}
    with patch.object(beads, "_resolve_chat_storage", side_effect=lambda c: (storage, c)), \
         patch.object(beads, "load_bead_tree", side_effect=lambda **kw: trees[kw["conversation_id"]]), \
         patch.object(beads, "save_bead_tree") as mock_save:
        beads.resolve_origin_bead("conv-b", "b_mid")

    # Both hops resolved: B's bead and A's bead.
    saved_convs = {call.kwargs["conversation_id"] for call in mock_save.call_args_list}
    assert saved_convs == {"conv-b", "conv-a"}
    assert tree_b.beads[0].status == "completed"
    assert tree_a.beads[0].status == "completed"


# -- _resolve_bead_by_id tests (prefix-tolerant completion) ------------------
# bead_status truncates ids to 8 chars; bead_complete must accept that prefix
# as a completion key (the displayed id was previously unusable to complete a
# specific thread).  Exact-first, then unique-prefix, then ambiguity guard.


def test_resolve_bead_by_id_exact():
    from app.mcp.tools.bead_tools import _resolve_bead_by_id
    b = Bead(id="bead_abc123def456", content="x", status="parked")
    target, err = _resolve_bead_by_id(BeadTree(beads=[b]), "bead_abc123def456")
    assert err is None
    assert target is b


def test_resolve_bead_by_id_unique_prefix():
    from app.mcp.tools.bead_tools import _resolve_bead_by_id
    b = Bead(id="bead_abc123def456", content="x", status="parked")
    target, err = _resolve_bead_by_id(BeadTree(beads=[b]), "bead_abc")
    assert err is None
    assert target is b


def test_resolve_bead_by_id_ambiguous_prefix():
    from app.mcp.tools.bead_tools import _resolve_bead_by_id
    tree = BeadTree(beads=[
        Bead(id="bead_abc111000000", content="a", status="parked"),
        Bead(id="bead_abc222000000", content="b", status="parked"),
    ])
    target, err = _resolve_bead_by_id(tree, "bead_abc")
    assert target is None
    assert err["ok"] is False
    assert err["error"] is True
    assert "mbiguous" in err["message"]


def test_resolve_bead_by_id_not_found():
    from app.mcp.tools.bead_tools import _resolve_bead_by_id
    tree = BeadTree(beads=[Bead(id="bead_real00000000", content="x", status="active")])
    target, err = _resolve_bead_by_id(tree, "bead_ghost")
    assert target is None
    assert err["error"] is True
    assert "No bead matching" in err["message"]


# -- bead_complete explicit-id tests (uses the resolver above) ---------------


@pytest.mark.asyncio
async def test_bead_complete_by_explicit_full_id():
    """Explicit full id completes that bead, not the active one."""
    active = Bead(id="bead_active000000", content="active", status="active")
    parked = Bead(id="bead_parked000000", content="parked thread", status="parked")
    tree = BeadTree(beads=[active, parked])

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute(bead_id="bead_parked000000")

    assert result["ok"] is True
    assert result["completed"] == "bead_parked000000"
    saved = mock_save.call_args[0][0]
    assert next(b for b in saved.beads if b.id == "bead_parked000000").status == "completed"
    assert next(b for b in saved.beads if b.id == "bead_active000000").status == "active"


@pytest.mark.asyncio
async def test_bead_complete_by_eight_char_prefix():
    """The 8-char id bead_status shows is a valid completion key."""
    active = Bead(id="bead_active000000", content="active", status="active")
    parked = Bead(id="bead_25aabbccddee", content="step 2 fork", status="parked")
    tree = BeadTree(beads=[active, parked])

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        # "bead_25a" is exactly what bead_status renders (id[:8]).
        result = await tool.execute(bead_id="bead_25a")

    assert result["ok"] is True
    assert result["completed"] == "bead_25aabbccddee"
    saved = mock_save.call_args[0][0]
    assert next(b for b in saved.beads if b.id == "bead_25aabbccddee").status == "completed"


@pytest.mark.asyncio
async def test_bead_complete_ambiguous_prefix_rejected_no_save():
    """A prefix matching multiple beads errors and writes nothing."""
    tree = BeadTree(beads=[
        Bead(id="bead_25a111111111", content="one", status="parked"),
        Bead(id="bead_25a222222222", content="two", status="parked"),
    ])

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute(bead_id="bead_25a")

    assert result["ok"] is False
    assert result["error"] is True
    assert "mbiguous" in result["message"]
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_bead_complete_unknown_id_rejected_no_save():
    """Unknown id returns a not-found error, distinct from the no-id path."""
    tree = BeadTree(beads=[Bead(id="bead_real00000000", content="x", status="active")])

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute(bead_id="bead_ghost")

    assert result["ok"] is False
    assert result["error"] is True
    assert "No bead matching" in result["message"]
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_bead_complete_exact_match_wins_over_prefix():
    """A full id that is also a prefix of a longer id resolves to the exact one."""
    exact = Bead(id="bead_x", content="exact", status="parked")
    longer = Bead(id="bead_xy", content="longer", status="parked")
    tree = BeadTree(beads=[exact, longer])

    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        tool = BeadCompleteTool()
        result = await tool.execute(bead_id="bead_x")

    assert result["ok"] is True
    assert result["completed"] == "bead_x"
