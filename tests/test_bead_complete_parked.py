"""
Regression tests: completing a parked bead must be reachable from the tool layer.

Live failure this pinned:  bead_status reported "1 active" on a tree where
every bead was parked (the active count was hardcoded), so the caller
believed an active bead existed, called bead_complete with no bead_id, and
received "No active bead to complete" — a dead end, because neither the
error nor the tool description mentions that bead_id completes a parked
bead.  The bead_complete matcher itself was already correct.
"""
import pytest
from unittest.mock import patch

from app.models.bead import Bead, BeadTree


def _all_parked_tree():
    return BeadTree(beads=[
        Bead(id="bead_aaa111", content="fix drag ghost", status="parked"),
        Bead(id="bead_bbb222", content="audit seam index", status="parked"),
    ])


# -- bead_status must not claim an active bead that does not exist ----------

@pytest.mark.asyncio
async def test_status_reports_zero_active_when_all_parked():
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=_all_parked_tree()):
        from app.mcp.tools.bead_tools import BeadStatusTool
        result = await BeadStatusTool().execute()

    assert result["ok"] is True
    assert "0 active" in result["tree"], result["tree"]
    assert "1 active" not in result["tree"], result["tree"]


@pytest.mark.asyncio
async def test_status_still_reports_one_active_when_active_exists():
    """Positive control: the count must not be hardcoded to 0 either."""
    tree = BeadTree(beads=[
        Bead(id="bead_ccc333", content="live thread", status="active"),
        Bead(id="bead_aaa111", content="parked thread", status="parked"),
    ])
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree):
        from app.mcp.tools.bead_tools import BeadStatusTool
        result = await BeadStatusTool().execute()

    assert "1 active" in result["tree"], result["tree"]


@pytest.mark.asyncio
async def test_status_points_at_bead_id_when_nothing_active():
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=_all_parked_tree()):
        from app.mcp.tools.bead_tools import BeadStatusTool
        result = await BeadStatusTool().execute()

    assert "bead_id" in result["tree"], result["tree"]


@pytest.mark.asyncio
async def test_status_no_remediation_hint_when_active_exists():
    """The hint is for the dead-end case only; don't nag on a healthy tree."""
    tree = BeadTree(beads=[Bead(id="bead_ccc333", content="live", status="active")])
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree):
        from app.mcp.tools.bead_tools import BeadStatusTool
        result = await BeadStatusTool().execute()

    assert "bead_id" not in result["tree"], result["tree"]


# -- the no-active error must be self-remediating ---------------------------

@pytest.mark.asyncio
async def test_no_active_error_names_the_parked_candidates():
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=_all_parked_tree()):
        from app.mcp.tools.bead_tools import BeadCompleteTool
        result = await BeadCompleteTool().execute()

    assert result["ok"] is False
    assert result["error"] is True
    msg = result["message"]
    # Existing contract preserved (tests/test_bead_tools.py asserts this).
    assert "No active bead" in msg, msg
    # New: the way out is named, with usable ids.
    assert "bead_id" in msg, msg
    assert "bead_aaa111" in msg, msg
    assert "bead_bbb222" in msg, msg


@pytest.mark.asyncio
async def test_no_active_and_no_parked_keeps_plain_error():
    """Nothing to suggest when there is genuinely nothing open."""
    tree = BeadTree(beads=[Bead(id="bead_ddd444", content="old", status="completed")])
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree):
        from app.mcp.tools.bead_tools import BeadCompleteTool
        result = await BeadCompleteTool().execute()

    assert result["ok"] is False
    assert "No active bead" in result["message"]
    assert "bead_id" not in result["message"]


# -- affordance guard: the parked-by-id path itself works -------------------

@pytest.mark.asyncio
async def test_parked_bead_completes_by_full_id():
    tree = _all_parked_tree()
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree") as mock_save:
        from app.mcp.tools.bead_tools import BeadCompleteTool
        result = await BeadCompleteTool().execute(bead_id="bead_aaa111")

    assert result["ok"] is True
    assert result["completed"] == "bead_aaa111"
    saved = mock_save.call_args[0][0]
    assert next(b for b in saved.beads if b.id == "bead_aaa111").status == "completed"
    # The sibling thread is untouched.
    assert next(b for b in saved.beads if b.id == "bead_bbb222").status == "parked"


@pytest.mark.asyncio
async def test_parked_bead_completes_by_status_shown_prefix():
    """bead_status truncates ids to 8 chars; that string must be usable."""
    tree = _all_parked_tree()
    with patch("app.mcp.tools.bead_tools._is_ephemeral_context", return_value=False), \
         patch("app.storage.beads.load_bead_tree", return_value=tree), \
         patch("app.storage.beads.save_bead_tree"):
        from app.mcp.tools.bead_tools import BeadCompleteTool
        result = await BeadCompleteTool().execute(bead_id="bead_aaa"[:8])

    assert result["ok"] is True
    assert result["completed"] == "bead_aaa111"


# -- discoverability: the schema must advertise the parked path -------------

def test_bead_id_field_documents_parked_completion():
    from app.mcp.tools.bead_tools import BeadCompleteInput
    desc = BeadCompleteInput.model_fields["bead_id"].description or ""
    assert "parked" in desc.lower(), desc


def test_tool_description_documents_parked_completion():
    """Must name bead_id as the way to close a non-active thread.

    Asserting merely on the word "parked" is vacuous — the pre-fix
    description already said "they remain parked for later" while giving
    the caller no way to complete one.
    """
    from app.mcp.tools.bead_tools import BeadCompleteTool
    desc = BeadCompleteTool().description
    assert "bead_id" in desc, desc
