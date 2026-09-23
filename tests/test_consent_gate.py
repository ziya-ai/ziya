"""Consent gate unit tests (design/consent-runtime.md step 3).

These run the gate generator directly with an isolated ledger directory and a
patched permissions config, so no ~/.ziya state is read or written.
"""

import asyncio

import pytest

from app.utils import consent_gate as cg


class _GitLike:
    name = "git"
    consent_default = "ask"
    consent_op_key = "op"

    def preflight(self, **args):
        return {"would_stage": ["a.py"], "op_seen": args.get("op")}


class _Plain:
    name = "file_list"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    yield
    cg.reset_for_tests()


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


def _types(events):
    return [e["type"] for e in events]


# ── mode resolution ───────────────────────────────────────────────────────

def test_mode_defaults_to_off_for_an_undeclared_tool():
    assert cg.consent_mode("file_list", "*", _Plain()) == "off"


def test_mode_honours_the_tool_declared_default():
    assert cg.consent_mode("git", "commit", _GitLike()) == "ask"


def test_config_overrides_declared_default_per_op_then_per_tool(monkeypatch):
    monkeypatch.setattr(cg, "_consent_config", lambda: {
        "tools": {"git": {"default": "off", "ops": {"push": "ask"}}}})
    assert cg.consent_mode("git", "commit", _GitLike()) == "off"
    assert cg.consent_mode("git", "push", _GitLike()) == "ask"


def test_op_is_read_from_the_declared_key_else_star():
    assert cg.op_for_call(_GitLike(), {"op": "commit"}) == "commit"
    assert cg.op_for_call(_GitLike(), {}) == "*"
    assert cg.op_for_call(_Plain(), {"op": "commit"}) == "*"


# ── the gate ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_off_mode_approves_silently_without_asking():
    events = await _drain(cg.consent_gate(
        tool_name="file_list", tool_id="t1", args={}, conversation_id="c1", tool=_Plain()))
    assert _types(events) == [cg.DECISION_EVENT]
    assert events[0]["approved"] is True and events[0]["asked"] is False
    assert cg.ledger().open_for("c1") == []


@pytest.mark.asyncio
async def test_ask_mode_opens_a_record_and_approval_flows_through():
    gen = cg.consent_gate(tool_name="git", tool_id="t1", args={"op": "commit"},
                          conversation_id="c1", tool=_GitLike())
    opened = await gen.__anext__()
    assert opened["type"] == "consent_opened"
    rid = opened["request_id"]
    assert rid == "conv:c1:t1"
    assert opened["payload"]["kind"] == "tool_call"
    assert opened["payload"]["op"] == "commit"
    assert opened["payload"]["preflight"] == {"would_stage": ["a.py"], "op_seen": "commit"}
    assert opened["scopes"] == list(cg.SCOPES)
    # Visible to a reconnecting client.
    assert [r.request_id for r in cg.ledger().open_for("c1")] == [rid]

    task = asyncio.create_task(_drain(gen))
    await asyncio.sleep(0.02)
    assert not task.done(), "must hold until answered"

    cg.ledger().record_answer(rid, "approve", scope="once", answered_by="alice")
    rest = await asyncio.wait_for(task, 5)
    assert _types(rest) == ["consent_answered", cg.DECISION_EVENT]
    assert rest[0]["decision"] == "approve" and rest[0]["answered_by"] == "alice"
    assert rest[1]["approved"] is True and rest[1]["asked"] is True
    assert cg.ledger().open_for("c1") == [], "closed after the answer"
    # once → no standing grant
    assert not cg.grants().covers("git", "commit", "c1")


@pytest.mark.asyncio
async def test_rejection_is_reported_and_leaves_no_grant():
    gen = cg.consent_gate(tool_name="git", tool_id="t2", args={"op": "push"},
                          conversation_id="c1", tool=_GitLike())
    opened = await gen.__anext__()
    cg.ledger().record_answer(opened["request_id"], "reject", answer="not yet", answered_by="bob")
    rest = await asyncio.wait_for(_drain(gen), 5)
    decision = rest[-1]
    assert decision["approved"] is False
    text = cg.refusal_text("git", "push", decision["answer"])
    assert "bob" in text and "not yet" in text and "git push" in text
    assert not cg.grants().covers("git", "push", "c1")


@pytest.mark.asyncio
async def test_conversation_scope_is_remembered_per_op_and_per_conversation():
    gen = cg.consent_gate(tool_name="git", tool_id="t3", args={"op": "commit"},
                          conversation_id="c1", tool=_GitLike())
    opened = await gen.__anext__()
    cg.ledger().record_answer(opened["request_id"], "approve", scope="conversation")
    await asyncio.wait_for(_drain(gen), 5)
    assert cg.grants().covers("git", "commit", "c1")
    assert not cg.grants().covers("git", "push", "c1"), "commit must not imply push"
    assert not cg.grants().covers("git", "commit", "c2"), "a fork/other conversation starts cold"

    # Second call in the same conversation is silent.
    events = await _drain(cg.consent_gate(tool_name="git", tool_id="t4", args={"op": "commit"},
                                          conversation_id="c1", tool=_GitLike()))
    assert _types(events) == [cg.DECISION_EVENT] and events[0]["asked"] is False


@pytest.mark.asyncio
async def test_always_scope_is_a_session_grant_flagged_pending_signature():
    gen = cg.consent_gate(tool_name="git", tool_id="t5", args={"op": "commit"},
                          conversation_id="c1", tool=_GitLike())
    opened = await gen.__anext__()
    cg.ledger().record_answer(opened["request_id"], "approve", scope="always")
    rest = await asyncio.wait_for(_drain(gen), 5)
    assert rest[0]["always_pending_signature"] is True
    assert cg.grants().covers("git", "commit", "c-other"), "session scope spans conversations"


@pytest.mark.asyncio
async def test_cancel_while_waiting_is_a_reject_and_closes_the_record():
    flag = {"cancel": False}
    gen = cg.consent_gate(tool_name="git", tool_id="t6", args={"op": "commit"},
                          conversation_id="c1", tool=_GitLike(),
                          cancel_requested=lambda: flag["cancel"])
    opened = await gen.__anext__()
    task = asyncio.create_task(_drain(gen))
    await asyncio.sleep(0.02)
    flag["cancel"] = True
    rest = await asyncio.wait_for(task, 5)
    assert rest[-1]["approved"] is False
    assert rest[-1]["answer"]["answer"] == "cancelled"
    assert cg.ledger().open_for("c1") == []


@pytest.mark.asyncio
async def test_no_conversation_refuses_rather_than_hanging():
    events = await asyncio.wait_for(_drain(cg.consent_gate(
        tool_name="git", tool_id="t7", args={"op": "commit"},
        conversation_id=None, tool=_GitLike())), 2)
    assert _types(events) == [cg.DECISION_EVENT]
    assert events[0]["approved"] is False


@pytest.mark.asyncio
async def test_a_wrapped_tool_instance_is_unwrapped_for_declarations():
    class Wrapper:
        def __init__(self, inner):
            self.tool_instance = inner
    gen = cg.consent_gate(tool_name="git", tool_id="t8", args={"op": "commit"},
                          conversation_id="c1", tool=Wrapper(_GitLike()))
    opened = await gen.__anext__()
    assert opened["payload"]["op"] == "commit"
    cg.ledger().record_answer(opened["request_id"], "approve")
    await asyncio.wait_for(_drain(gen), 5)
