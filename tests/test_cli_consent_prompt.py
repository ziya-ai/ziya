"""``ziya chat`` answers tool-consent requests at the terminal.

Reported 2026-10-27: in ``ziya chat`` every ``file_write`` to a path outside
the write policy printed "Executing file write..." and then sat until ^C,
three turns running.  The consent gate had opened a ledger request and was
waiting (24 h TTL) for an answer that only the browser's ConsentPanel could
give; the CLI stream handler silently dropped ``consent_opened``.

These tests drive the REAL gate with the REAL file_write preflight and
assert the terminal answer reaches the gate, plus the CLI stream handler
seam that calls the prompt.
"""
import asyncio
import io
import sys
from types import SimpleNamespace

import pytest

from app.utils import cli_consent, consent_gate


@pytest.fixture
def gate_root(tmp_path):
    consent_gate.reset_for_tests(tmp_path / "consents")
    root = tmp_path / "consents" / "project"
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "a.ts").write_text("export const x = 1;\n")
    yield str(root)
    consent_gate.reset_for_tests(None)


def _drive_gate(root, reply, *, tty=True, out=None):
    """Run the real gate for an out-of-policy file_write; answer each
    consent_opened from the 'terminal'.  Returns (events, decision)."""
    from app.mcp.tools.fileio import FileWriteTool
    events, decision = [], None

    async def go():
        nonlocal decision
        gen = consent_gate.consent_gate(
            tool_name="file_write", tool_id="t1",
            args={"path": "frontend/a.ts", "patch": "x = 1", "content": "x = 2"},
            conversation_id="cli_20261027_test", tool=FileWriteTool(),
            project_root=root, ttl_ms=5000,
        )
        async for ev in gen:
            if ev.get("type") == consent_gate.DECISION_EVENT:
                decision = ev
                continue
            events.append(ev)
            if ev.get("type") == "consent_opened":
                cli_consent.answer_from_terminal(
                    ev, input_fn=lambda _p: reply, isatty=lambda: tty,
                    out=(out.append if out is not None else (lambda _s: None)))

    asyncio.run(asyncio.wait_for(go(), timeout=10))
    return events, decision


def test_positive_control_the_write_does_ask(gate_root):
    events, decision = _drive_gate(gate_root, "y")
    opened = [e for e in events if e["type"] == "consent_opened"]
    assert len(opened) == 1, "the out-of-policy write must open a consent request"
    assert opened[0]["payload"]["outside_policy"][0]["path"] == "frontend/a.ts"


def test_yes_approves_this_call_for_exactly_that_path(gate_root):
    _, decision = _drive_gate(gate_root, "y")
    assert decision["approved"] is True and decision["asked"] is True
    assert decision["write_grant"]["paths"] == ["frontend/a.ts"]
    assert decision["answer"]["answered_by"] == "terminal"
    assert decision["answer"]["scope"] == "once"


def test_no_rejects_with_a_refusal_the_model_can_read(gate_root):
    _, decision = _drive_gate(gate_root, "n")
    assert decision["approved"] is False
    text = consent_gate.refusal_text("file_write", "*", decision["answer"])
    assert "declined" in text and "terminal" in text


def test_always_records_a_conversation_grant(gate_root):
    _, decision = _drive_gate(gate_root, "a")
    assert decision["approved"] is True
    assert decision["answer"]["scope"] == "conversation"
    # The next identical write in this conversation does not ask again.
    events, again = _drive_gate(gate_root, "n")
    assert not [e for e in events if e["type"] == "consent_opened"]
    assert again["approved"] is True


def test_no_tty_rejects_instead_of_hanging(gate_root):
    shown = []
    _, decision = _drive_gate(gate_root, "y", tty=False, out=shown)
    assert decision["approved"] is False
    assert any("not a TTY" in s for s in shown)


def test_describe_shows_the_diff_and_the_path(gate_root):
    shown = []
    _drive_gate(gate_root, "y", out=shown)
    joined = "\n".join(shown)
    assert "frontend/a.ts" in joined
    assert "x = 2" in joined and "x = 1" in joined   # diff lines


def test_ctrl_c_at_the_prompt_rejects():
    def boom(_p):
        raise KeyboardInterrupt
    led = SimpleNamespace(calls=[], record_answer=lambda rid, d, **kw: led.calls.append((rid, d, kw)) or object())
    r = cli_consent.answer_from_terminal(
        {"request_id": "conv:c:t", "payload": {"tool": "git", "op": "commit"}, "scopes": ["once"]},
        input_fn=boom, isatty=lambda: True, out=lambda _s: None, ledger=led)
    assert r["decision"] == "reject" and r["recorded"] is True
    assert led.calls[0][2]["answer"] == "cancelled at the terminal"


def test_human_only_op_offers_no_always():
    prompts = []
    led = SimpleNamespace(record_answer=lambda *a, **k: object())
    r = cli_consent.answer_from_terminal(
        {"request_id": "conv:c:t", "payload": {"tool": "git", "op": "push_force"}, "scopes": ["once"]},
        input_fn=lambda p: prompts.append(p) or "a", isatty=lambda: True,
        out=lambda _s: None, ledger=led)
    assert "[a]lways" not in prompts[0]
    assert r["decision"] == "reject"   # 'a' is not an offered answer here


# ── seam: the CLI stream handler prompts on consent_opened ───────────────

def test_cli_stream_handler_answers_consent_opened(monkeypatch):
    from app import cli as cli_mod
    seen = []
    monkeypatch.setattr(cli_consent, "answer_from_terminal",
                        lambda ev, **kw: seen.append(ev["request_id"]) or {"decision": "approve"})

    async def gen():
        yield {"type": "consent_opened", "request_id": "conv:c:t1",
               "payload": {"tool": "file_write"}, "scopes": ["once"]}
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}

    inst = object.__new__(cli_mod.CLI)
    inst._partial_response = ""
    out = asyncio.run(inst._stream_handler(gen(), False))
    assert seen == ["conv:c:t1"], "consent_opened must reach the terminal prompt"
    assert out == "done", "the stream continues after the answer"
