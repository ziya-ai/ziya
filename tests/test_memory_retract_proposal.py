"""
Tests for the memory_retract_proposal MCP tool.

The tool restores symmetry for ONE half of the propose/approve contract:
the agent may withdraw a proposal IT created THIS session, but may never
(a) approve a proposal, or (b) dismiss a proposal from another session /
a legacy proposal with no conversation stamp.  Those await the user's
review and are not the agent's to clear.

Ownership is proven by matching the proposal's stamped conversation_id
against the conversation_id the framework injects into the tool call.
The gate fails closed: missing stamp or mismatch → denied.

These run against the real ProposalsStore — the PROBATIONARY store, which
is what memory_propose writes to and what run_lifecycle_pass sweeps.  The
memories-store proposal list is a separate human-only queue that nothing
writes to any more.  Rows there are plain dicts from the event-log
projection, not MemoryProposal objects, so field access is by key.
Isolated to tmp_path by the autouse conftest fixtures (ZIYA_HOME redirect
plus Noop embeddings), so the queue and the ownership gate are exercised
end to end without touching the developer's real store.
"""
import pytest

from app.mcp.tools.memory_tools import (
    MemoryProposeTool,
    MemoryRetractProposalTool,
)
from app.storage.proposals import get_proposals_store
from app.models.memory import MemoryProposal


CONV_A = "conv-aaaaaaaa"
CONV_B = "conv-bbbbbbbb"


async def _propose(content: str, conversation_id):
    """Create a proposal via the tool, returning its prop_* id."""
    tool = MemoryProposeTool()
    result = await tool.execute(
        content=content,
        layer="domain_context",
        conversation_id=conversation_id,
    )
    assert result.get("success"), result
    return result["proposal_id"]


async def _retract(proposal_id, conversation_id):
    tool = MemoryRetractProposalTool()
    return await tool.execute(
        proposal_id=proposal_id,
        conversation_id=conversation_id,
    )


def _pending_ids():
    """Open (still-probationary) proposal ids.

    Only ``open`` counts as pending: retract archives rather than deletes,
    so the row survives in ``list_all()`` for audit and would otherwise
    still appear here after a successful retract.
    """
    return {p["id"] for p in get_proposals_store().list_open()}


def _active_contents():
    # Promotion targets the memories store, so this one stays put.
    from app.storage.memory import get_memory_storage
    return {m.content for m in get_memory_storage().list_memories(status="active")}


# ── propose stamps conversation_id ──────────────────────────────────

@pytest.mark.asyncio
async def test_propose_stamps_conversation_id():
    pid = await _propose("Kuiper GGMA merlin count is 4 per gateway.", CONV_A)
    proposal = get_proposals_store().get(pid)
    assert proposal is not None, f"{pid} not found in probationary store"
    assert proposal["conversation_id"] == CONV_A


# ── own-session retract succeeds ────────────────────────────────────

@pytest.mark.asyncio
async def test_retract_own_session_succeeds():
    pid = await _propose("A premature in-flight design decision.", CONV_A)
    assert pid in _pending_ids()

    result = await _retract(pid, CONV_A)
    assert result.get("success") is True, result
    assert result["proposal_id"] == pid
    # Gone from the queue...
    assert pid not in _pending_ids()


# ── cross-session retract is DENIED ─────────────────────────────────

@pytest.mark.asyncio
async def test_retract_other_session_denied():
    pid = await _propose("Proposal made by conversation A.", CONV_A)

    # Conversation B tries to retract A's proposal.
    result = await _retract(pid, CONV_B)
    assert result.get("error") is True, result
    assert "this conversation" in result["message"].lower()
    # Still pending — B could not clear A's proposal.
    assert pid in _pending_ids()


# ── legacy proposal (no conversation_id) is DENIED (fails closed) ───

@pytest.mark.asyncio
async def test_retract_legacy_unstamped_denied():
    # Simulate a proposal created before conversation stamping existed
    # (or by a path that doesn't stamp): write it directly with no
    # conversation_id.
    #
    # Written straight to the probationary store rather than through the
    # tool, because the tool always stamps.  This test passed even before
    # the store redirect, but for the wrong reason: retract looked in a
    # store this row was never in, so "not found" masqueraded as "denied".
    # It now genuinely exercises the fails-closed ownership gate.
    store = get_proposals_store()
    legacy = MemoryProposal(content="Legacy unstamped proposal.", layer="domain_context")
    assert legacy.conversation_id is None
    legacy_id = store.add(legacy, activity_count=0)

    result = await _retract(legacy_id, CONV_A)
    assert result.get("error") is True, result
    assert legacy_id in _pending_ids()  # untouched


# ── current call with no conversation_id cannot retract anything ────

@pytest.mark.asyncio
async def test_retract_without_current_conversation_denied():
    pid = await _propose("Stamped proposal.", CONV_A)
    # The retract call itself carries no conversation_id (e.g. CLI path
    # where the ContextVar wasn't set) → cannot prove ownership → denied.
    result = await _retract(pid, None)
    assert result.get("error") is True, result
    assert pid in _pending_ids()


# ── nonexistent / already-gone id errors cleanly ───────────────────

@pytest.mark.asyncio
async def test_retract_nonexistent_id_errors():
    result = await _retract("prop_doesnotexist", CONV_A)
    assert result.get("error") is True, result
    assert "no pending proposal" in result["message"].lower()


@pytest.mark.asyncio
async def test_retract_empty_id_errors():
    result = await _retract("   ", CONV_A)
    assert result.get("error") is True, result
    assert "required" in result["message"].lower()


# ── the never-approve invariant ─────────────────────────────────────

@pytest.mark.asyncio
async def test_retract_does_not_promote_to_active_memory():
    """Retracting must DISMISS, never approve.  The content must not appear
    in the active memory store after retraction."""
    content = "This must never become an active memory via retract."
    pid = await _propose(content, CONV_A)
    assert content not in _active_contents()

    result = await _retract(pid, CONV_A)
    assert result.get("success") is True, result

    # Dismissed, not promoted: absent from BOTH the queue and active memory.
    assert pid not in _pending_ids()
    assert content not in _active_contents()


# ── retract is idempotent-ish: second retract of same id errors ────

@pytest.mark.asyncio
async def test_double_retract_second_errors():
    pid = await _propose("Retract me once.", CONV_A)
    first = await _retract(pid, CONV_A)
    assert first.get("success") is True
    second = await _retract(pid, CONV_A)
    assert second.get("error") is True, second
    assert "no pending proposal" in second["message"].lower()


# ── isolation: retracting A's proposal leaves B's intact ────────────

@pytest.mark.asyncio
async def test_retract_is_targeted():
    pid_a = await _propose("A's proposal.", CONV_A)
    pid_b = await _propose("B's proposal.", CONV_B)

    result = await _retract(pid_a, CONV_A)
    assert result.get("success") is True

    pending = _pending_ids()
    assert pid_a not in pending
    assert pid_b in pending  # untouched
