"""Tests for the task_card_launch tool and its launch-policy gate.

THE DEFECT THIS EXISTS FOR
--------------------------
There was no way for the model to START a saved task card; launch was
wired only to the human Run button and the REST endpoint.  A fully
authored, non-escalating card (reads + floor-safe writes only) could not
be run without a human click, even though launching it commits no
privilege the floor policy had not already conceded.  task_card_launch
closes that, behind a single threshold constant MODEL_LAUNCH_POLICY.

WHAT IS ASSERTED
----------------
The gate is the decision-relevant surface, so it is tested directly and
at every policy value, plus the two ends the tool must honour: a clean
card actually reaches the launch helper, and an escalating card is
REFUSED without the helper ever being called (fail-closed).  The escalating
test would pass against a tool that launched everything only because the
helper-called flag would flip — so it asserts BOTH that launched is False
AND that the helper was never invoked.
"""
import asyncio

import pytest

from app.mcp.tools import task_card_launch as tcl


# ── the gate, at every policy value ──────────────────────────────────────

def _row(*, escalating=True, signed=False, name="blk"):
    return {"hasEscalation": escalating, "needsSignature": not signed,
            "authorized": signed, "name": name, "blockId": name}


def test_non_escalating_card_is_allowed_under_every_policy(monkeypatch):
    # No escalation rows at all → allowed regardless of the knob.
    for policy in ("non_escalating", "signed", "always"):
        monkeypatch.setattr(tcl, "MODEL_LAUNCH_POLICY", policy)
        allowed, reason = tcl._model_launch_gate([])
        assert allowed is True, policy
        assert reason == ""


def test_default_policy_refuses_any_escalation(monkeypatch):
    monkeypatch.setattr(tcl, "MODEL_LAUNCH_POLICY", "non_escalating")
    allowed, reason = tcl._model_launch_gate([_row(signed=True)])
    # Even a SIGNED escalating block is refused under the default policy —
    # the threshold is escalation-presence, not signature status.
    assert allowed is False
    assert "escalating" in reason.lower()


def test_signed_policy_allows_only_when_all_signed(monkeypatch):
    monkeypatch.setattr(tcl, "MODEL_LAUNCH_POLICY", "signed")
    all_signed = [_row(signed=True), _row(signed=True, name="b2")]
    one_unsigned = [_row(signed=True), _row(signed=False, name="b2")]
    assert tcl._model_launch_gate(all_signed)[0] is True
    allowed, reason = tcl._model_launch_gate(one_unsigned)
    assert allowed is False
    assert "b2" in reason


def test_always_policy_allows_unsigned_escalation(monkeypatch):
    monkeypatch.setattr(tcl, "MODEL_LAUNCH_POLICY", "always")
    assert tcl._model_launch_gate([_row(signed=False)])[0] is True


# ── the tool, end to end (helper + escalation check stubbed) ──────────────

class _Card:
    def __init__(self, cid="card-1", name="WS6"):
        self.id = cid
        self.name = name


class _Storage:
    def __init__(self, card):
        self._card = card

    def get(self, cid):
        return self._card if self._card and cid == self._card.id else None


def _stub_resolution(monkeypatch, card):
    monkeypatch.setattr(tcl, "_resolve_card_storage", lambda: {
        "ok": True, "storage": _Storage(card), "project": object(),
        "project_id": "proj-1",
    })


def _stub_launch(monkeypatch):
    """Record whether the real launch helper was reached."""
    called = {}

    class _Run:
        id = "run-9"
        status = "running"

    async def _fake(**kwargs):
        called["kwargs"] = kwargs
        return _Run()

    import app.api.task_cards as tc
    monkeypatch.setattr(tc, "_launch_run_for_card", _fake)
    return called


def test_clean_card_launches_and_returns_run_id(monkeypatch):
    card = _Card()
    _stub_resolution(monkeypatch, card)
    monkeypatch.setattr(tcl, "_escalation_rows_for", lambda c, p: [])
    called = _stub_launch(monkeypatch)
    monkeypatch.setattr(
        "app.context.get_conversation_id_or_none", lambda: "chat-1")

    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="card-1"))

    assert out["success"] is True
    assert out["launched"] is True
    assert out["run_id"] == "run-9"
    assert "kwargs" in called  # helper WAS reached


# ── the seam: a launched run must be BOUND into the launching chat ────────
#
# The chat renders task monitors from TaskBindings, not from runs.  Every
# human launch path records one; the tool originally did not, so a
# model-launched run executed with no tile anywhere.  This exercises the
# REAL TaskBindingStorage against a tmp project dir, so it asserts the
# binding the chat would actually read back — not a flag the test set.

def _real_binding_storage(monkeypatch, tmp_path):
    from app.storage.task_bindings import TaskBindingStorage
    monkeypatch.setattr(
        "app.utils.paths.get_project_dir", lambda project_id: tmp_path)
    return TaskBindingStorage(tmp_path)


def test_launch_binds_run_into_current_conversation(monkeypatch, tmp_path):
    card = _Card()
    _stub_resolution(monkeypatch, card)
    monkeypatch.setattr(tcl, "_escalation_rows_for", lambda c, p: [])
    _stub_launch(monkeypatch)
    monkeypatch.setattr(
        "app.context.get_conversation_id_or_none", lambda: "chat-1")
    store = _real_binding_storage(monkeypatch, tmp_path)

    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="card-1"))

    assert out["success"] is True
    bound = store.list_for_chat("chat-1")
    assert len(bound) == 1, "exactly one monitor tile in the launching chat"
    b = bound[0]
    assert b.card_id == "card-1"
    assert b.run_id == "run-9"          # bound, not staged
    assert b.anchor_message_id is None  # mid-turn: renders at the tail
    # The result names the binding so the frontend can key its refresh.
    assert out["binding_id"] == b.id
    assert "binding_id" in out and out["binding_id"]


def test_launch_without_conversation_binds_nothing(monkeypatch, tmp_path):
    """CLI / headless: no chat to bind into.  Launch still succeeds, and
    the result says so rather than claiming a tile exists."""
    card = _Card()
    _stub_resolution(monkeypatch, card)
    monkeypatch.setattr(tcl, "_escalation_rows_for", lambda c, p: [])
    _stub_launch(monkeypatch)
    monkeypatch.setattr(
        "app.context.get_conversation_id_or_none", lambda: None)
    _real_binding_storage(monkeypatch, tmp_path)

    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="card-1"))

    assert out["success"] is True
    assert out["launched"] is True
    assert out.get("binding_id") is None
    assert not list(tmp_path.glob("**/*.bindings.json"))


def test_binding_failure_does_not_unlaunch(monkeypatch, tmp_path):
    """The run is already executing when the binding is written; a
    storage fault there must degrade to 'no tile', not report failure."""
    card = _Card()
    _stub_resolution(monkeypatch, card)
    monkeypatch.setattr(tcl, "_escalation_rows_for", lambda c, p: [])
    _stub_launch(monkeypatch)
    monkeypatch.setattr(
        "app.context.get_conversation_id_or_none", lambda: "chat-1")

    def _boom(project_id):
        raise OSError("disk full")

    monkeypatch.setattr("app.utils.paths.get_project_dir", _boom)

    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="card-1"))

    assert out["success"] is True
    assert out["run_id"] == "run-9"
    assert out.get("binding_id") is None


def test_escalating_card_is_refused_without_launching(monkeypatch):
    card = _Card()
    _stub_resolution(monkeypatch, card)
    monkeypatch.setattr(tcl, "MODEL_LAUNCH_POLICY", "non_escalating")
    monkeypatch.setattr(
        tcl, "_escalation_rows_for", lambda c, p: [_row(name="npm-build")])
    called = _stub_launch(monkeypatch)

    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="card-1"))

    assert out.get("launched") is False
    assert "npm-build" in out["reason"]
    assert "kwargs" not in called  # fail-closed: helper NEVER reached


def test_unknown_card_errors(monkeypatch):
    _stub_resolution(monkeypatch, None)
    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="nope"))
    assert out.get("error") is True
    assert "not found" in out["message"].lower()


def test_escalation_check_failure_fails_closed(monkeypatch):
    card = _Card()
    _stub_resolution(monkeypatch, card)

    def _boom(c, p):
        raise RuntimeError("scope oracle down")

    monkeypatch.setattr(tcl, "_escalation_rows_for", _boom)
    called = _stub_launch(monkeypatch)

    out = asyncio.get_event_loop().run_until_complete(
        tcl.TaskCardLaunchTool().execute(card_id="card-1"))

    assert out.get("error") is True
    assert "kwargs" not in called  # a gate fault must NOT launch
