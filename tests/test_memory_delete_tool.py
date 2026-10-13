"""memory_delete: the agent can remove a durable memory, under consent.

Two classes of memory, two answers:

  own    saved by ``memory_save`` in THIS conversation.  Deleted without a
         prompt -- the agent is undoing its own unreviewed action, the same
         principle memory_retract_proposal applies to proposals.
  other  everything else: promoted from the proposal queue (reviewed),
         auto-extracted, REM-synthesised, or saved in another conversation.
         Asks; the approval panel shows the memory's text.

Ownership is not an argument, so the op comes from the tool's
``consent_op_for(args, conversation_id)`` hook, which the gate's
``op_for_call`` now consults.  These tests drive the REAL consent gate and
the REAL memory store (isolated by the autouse conftest ZIYA_HOME
redirect), and pin the executor call site that must pass the conversation
id, since a hook nobody feeds a conversation id classifies every memory
as ``other``.
"""

import asyncio
from pathlib import Path

import pytest

from app.models.memory import Memory
from app.storage.memory import get_memory_storage
from app.utils import consent_gate as cg

CONV = "conv-delete-aaaa"
OTHER = "conv-delete-bbbb"


@pytest.fixture(autouse=True)
def isolated_consent(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    yield
    cg.reset_for_tests()


def _save(content, learned_from="explicit_save", conversation=CONV):
    m = Memory(content=content, layer="domain_context", learned_from=learned_from)
    m.learned_from_conversation = conversation
    return get_memory_storage().save(m).id


def _tool():
    from app.mcp.tools.memory_tools import MemoryDeleteTool
    return MemoryDeleteTool()


async def _drain(gen):
    return [ev async for ev in gen]


# ── classification ─────────────────────────────────────────────────────

class TestOp:
    def test_own_explicit_save_in_this_conversation(self):
        mid = _save("own fact")
        assert _tool().consent_op_for({"memory_id": mid}, CONV) == "own"

    def test_saved_in_another_conversation_is_other(self):
        mid = _save("their fact", conversation=OTHER)
        assert _tool().consent_op_for({"memory_id": mid}, CONV) == "other"

    def test_promoted_proposal_is_other_even_from_this_conversation(self):
        # Promotion is a review outcome, not an agent action to undo.
        mid = _save("reviewed fact", learned_from="promoted_from_proposal")
        assert _tool().consent_op_for({"memory_id": mid}, CONV) == "other"

    def test_no_conversation_is_other(self):
        mid = _save("own fact")
        assert _tool().consent_op_for({"memory_id": mid}, None) == "other"

    def test_unstamped_memory_is_other(self):
        mid = _save("legacy fact", conversation=None)
        assert _tool().consent_op_for({"memory_id": mid}, CONV) == "other"

    def test_missing(self):
        assert _tool().consent_op_for({"memory_id": "m_nope"}, CONV) == "missing"

    def test_gate_reads_the_hook(self):
        mid = _save("own fact")
        assert cg.op_for_call(_tool(), {"memory_id": mid}, CONV) == "own"
        assert cg.op_for_call(_tool(), {"memory_id": mid}, OTHER) == "other"

    def test_fails_closed_without_a_conversation(self):
        # fails_closed has no conversation id, so an existing memory is
        # 'other' -> ask -> a broken gate refuses rather than deletes.
        mid = _save("own fact")
        assert cg.fails_closed(_tool(), {"memory_id": mid}) is True


# ── execute ────────────────────────────────────────────────────────────

class TestExecute:
    @pytest.mark.asyncio
    async def test_deletes_and_echoes(self):
        mid = _save("delete me")
        out = await _tool().execute(memory_id=mid, conversation_id=CONV)
        assert out.get("success") is True, out
        assert out["memory_id"] == mid and out["content"] == "delete me"
        assert get_memory_storage().get(mid) is None
        again = await _tool().execute(memory_id=mid, conversation_id=CONV)
        assert again.get("error") is True

    @pytest.mark.asyncio
    async def test_missing_id_is_an_error(self):
        out = await _tool().execute(memory_id="m_nope", conversation_id=CONV)
        assert out.get("error") is True and "m_nope" in out["message"]

    @pytest.mark.asyncio
    async def test_empty_id_is_an_error(self):
        out = await _tool().execute(memory_id="  ", conversation_id=CONV)
        assert out.get("error") is True


# ── seam: the real consent gate ────────────────────────────────────────

class TestGate:
    @pytest.mark.asyncio
    async def test_own_memory_is_approved_without_asking(self):
        mid = _save("own fact")
        events = await _drain(cg.consent_gate(
            tool_name="memory_delete", tool_id="t1", args={"memory_id": mid},
            conversation_id=CONV, tool=_tool()))
        assert [e["type"] for e in events] == [cg.DECISION_EVENT]
        assert events[0]["approved"] is True and events[0]["asked"] is False

    @pytest.mark.asyncio
    async def test_other_memory_asks_and_shows_the_text(self):
        mid = _save("their fact", conversation=OTHER)
        gen = cg.consent_gate(
            tool_name="memory_delete", tool_id="t2", args={"memory_id": mid},
            conversation_id=CONV, tool=_tool())
        opened = await gen.__anext__()
        assert opened["type"] == "consent_opened"
        assert opened["payload"]["op"] == "other"
        assert opened["payload"]["preflight"]["content"] == "their fact"
        cg.ledger().record_answer(opened["request_id"], "reject", answered_by="user")
        rest = await asyncio.wait_for(_drain(gen), 5)
        assert rest[-1]["approved"] is False
        # The gate never deletes; a rejected call leaves the memory alone.
        assert get_memory_storage().get(mid) is not None

    @pytest.mark.asyncio
    async def test_missing_memory_does_not_open_a_panel(self):
        events = await _drain(cg.consent_gate(
            tool_name="memory_delete", tool_id="t3", args={"memory_id": "m_nope"},
            conversation_id=CONV, tool=_tool()))
        assert [e["type"] for e in events] == [cg.DECISION_EVENT]
        assert cg.ledger().open_for(CONV) == []


# ── wiring ─────────────────────────────────────────────────────────────

def test_registered_with_the_memory_tools():
    from app.mcp.builtin_tools import get_memory_tools
    assert "MemoryDeleteTool" in [t.__name__ for t in get_memory_tools()]


def test_executor_passes_the_conversation_to_op_for_call():
    src = (Path(__file__).resolve().parents[1] / "app" / "tool_execution.py").read_text()
    assert "op_for_call(_consent._unwrap(_gate_tool), ctx.args, ctx.conversation_id)" in src
    gate = (Path(__file__).resolve().parents[1] / "app" / "utils" / "consent_gate.py").read_text()
    assert "op = op_for_call(inst, args, conversation_id)" in gate
