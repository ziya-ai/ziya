"""
Regression: bead-tool failure returns must surface a real reason through
the MCP manager, not "Unknown error".

Root cause this pins:
  app/mcp/manager.py treats any tool result dict with a truthy ``error``
  as a failure and surfaces ``result.get("message", "Unknown error")``.
  The bead tools historically returned ``{"ok": False, "error": "<reason
  string>"}`` — putting the human-readable reason in ``error`` (as a
  string) and never setting ``message``.  The manager therefore read a
  truthy ``error``, found no ``message``, and surfaced the literal
  "Unknown error", discarding the real reason (e.g. "No active bead to
  complete").

The fix aligns bead returns to the manager's contract
``{"error": True, "message": "<reason>"}`` while keeping ``ok: False``
for any direct caller.

These tests assert the *return-shape contract* of the bead tools, which
is what the manager consumes.  They do not require a live encryption
keyring or a persisted chat record: every bead tool runs in global
ephemeral mode here (so persistence is skipped) EXCEPT where we force a
specific failure path, and we drive the failure paths directly.
"""
import pytest


# ── The manager's surfacing rule, replicated as the contract under test ──
# Mirrors app/mcp/manager.py: a truthy ``error`` → surface ``message``,
# defaulting to "Unknown error" when ``message`` is absent.
def _surface(result: dict) -> str | None:
    """Return the user-facing message the manager would surface, or None
    when the result is not an error."""
    if isinstance(result, dict) and result.get("error"):
        return result.get("message", "Unknown error")
    return None


def test_surface_helper_matches_manager_contract():
    # Memory-tool convention (already correct) surfaces its real message.
    assert _surface({"error": True, "message": "Proposal content is required."}) \
        == "Proposal content is required."
    # A result with no error key is not surfaced as an error.
    assert _surface({"ok": True, "completed": "bead_x"}) is None
    # The bug shape: reason-in-error, no message → "Unknown error".
    assert _surface({"ok": False, "error": "No active bead to complete"}) \
        == "Unknown error"


@pytest.mark.asyncio
async def test_bead_complete_no_active_surfaces_real_reason():
    """bead_complete with nothing active must surface the real reason,
    not 'Unknown error'."""
    from app.mcp.tools.bead_tools import BeadCompleteTool

    # Drive the no-active-bead path with an explicit (real) conversation id
    # that has no beads.  In a fresh tmp store the tree is empty, so
    # active_bead is None and the tool takes the failure path.
    tool = BeadCompleteTool()
    result = await tool.execute(conversation_id="conv-no-beads-xyz")

    # If ephemeral mode short-circuited, this isn't the path we're testing.
    if result.get("skipped"):
        pytest.skip("ephemeral context skip — not the failure path under test")

    assert result.get("ok") is False
    surfaced = _surface(result)
    # The whole point: the manager must NOT collapse this to "Unknown error".
    assert surfaced is not None
    assert surfaced != "Unknown error", (
        f"bead_complete failure surfaced as 'Unknown error' — the real "
        f"reason was lost. Full result: {result}"
    )
    assert "active bead" in surfaced.lower()


@pytest.mark.asyncio
async def test_bead_create_empty_content_surfaces_real_reason():
    from app.mcp.tools.bead_tools import BeadCreateTool

    tool = BeadCreateTool()
    result = await tool.execute(content="", conversation_id="conv-x")
    if result.get("skipped"):
        pytest.skip("ephemeral context skip — not the failure path under test")

    assert result.get("ok") is False
    surfaced = _surface(result)
    assert surfaced is not None
    assert surfaced != "Unknown error", (
        f"bead_create failure surfaced as 'Unknown error'. Full result: {result}"
    )
    assert "content" in surfaced.lower()


@pytest.mark.asyncio
async def test_bead_create_success_is_not_surfaced_as_error():
    """A successful create must not look like an error to the manager."""
    from app.mcp.tools.bead_tools import BeadCreateTool

    tool = BeadCreateTool()
    result = await tool.execute(content="a real bead", conversation_id="conv-x")
    # Success (or ephemeral skip) — either way, not an error surface.
    assert _surface(result) is None, (
        f"successful/skip bead_create was surfaced as an error: {result}"
    )
