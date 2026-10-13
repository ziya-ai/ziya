"""Raw segmentation and the fast-typed-Enter path (Docs/design/shadow-sessions.md §4.1).

Raw mode (no prompt learned -- e.g. a prompt ending in a letter with no
trailing space) has no way to see a command end.  Before this fix the first
Enter opened a command and the segmenter stayed in ``output`` forever: every
later command was journaled as output of the first, ``send_line`` returned
``cmd_seq=None``, and ``shadow_send``'s hint then told the model to call
``shadow_wait(cmd_seq=None, until=['exit'])`` -- a wait for any exit, in a
session that never writes one, so it slept to the 240 s timeout.

Seams:
  * segmenter: each raw Enter opens its own cmd group, on_enter returns None
    (no completion will ever be reported), no fabricated exit;
  * segmenter: a prompt that came back with the next command already echoed
    after it (typed/pasted inside the 0.5 s idle window) still finishes the
    previous command and starts the new one;
  * shadow_send: with no cmd_seq the hint waits for quiet, not exit;
  * shadow_wait: an exit-only wait on a raw session is refused at once,
    while a prompt-heuristic session still reaches client.wait.
"""
import threading
from unittest.mock import MagicMock

import pytest

from app.shadow.segmenter import Segmenter, MODE_RAW, IDLE_FLUSH_S


class Sink:
    def __init__(self):
        self.records = []

    def _add(self, rec):
        rec["seq"] = len(self.records) + 1
        self.records.append(rec)
        return rec["seq"]

    def cmd(self, text):
        return self._add({"t": "cmd", "text": text})

    def output(self, text, cmd_seq):
        return [self._add({"t": "output", "text": text, "cmd_seq": cmd_seq})]

    def exit(self, cmd_seq, code):
        return self._add({"t": "exit", "cmd_seq": cmd_seq, "code": code})

    def meta(self, event, data=None):
        return self._add({"t": "meta", "event": event, "data": data or {}})

    def of(self, t):
        return [r for r in self.records if r["t"] == t]


def _idle(s, t0=1000.0):
    s._last_output_at = t0
    s.tick(now=t0 + IDLE_FLUSH_S + 0.1)


def _type(s, cmd: bytes):
    s.feed_input(cmd)
    s.feed_output(cmd)
    return s.on_enter(masked=False)


# ── segmenter: raw ───────────────────────────────────────────────────────

def test_raw_each_enter_is_its_own_group_and_reports_no_cmd_seq():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"host1")                      # unlearnable prompt
    for c in (b"ls", b"pwd", b"date"):
        assert _type(s, c) is None, "raw must not hand out a cmd_seq nothing can finish"
        s.feed_output(b"\r\nout-" + c + b"\r\nhost1")
        _idle(s)
    assert s.mode == MODE_RAW
    cmds = sink.of("cmd")
    assert [c["text"] for c in cmds] == ["ls", "pwd", "date"]
    # Each command's output is grouped under that command, not the first.
    for c, name in zip(cmds, ("ls", "pwd", "date")):
        outs = [o for o in sink.of("output") if o["cmd_seq"] == c["seq"]]
        assert any(f"out-{name}" in o["text"] for o in outs), (name, sink.records)
    assert sink.of("exit") == [], "raw has no completion signal; do not invent one"


# ── segmenter: Enter beat the idle tick ──────────────────────────────────

def test_prompt_back_with_next_command_already_echoed_finishes_the_previous():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"dev-dsk % ")
    first = _type(s, b"true")
    s.feed_output(b"\r\ndev-dsk % ")             # prompt back; tick never ran
    second = _type(s, b"pwd")                    # echo lands before Enter
    assert second is not None and second != first
    assert [c["text"] for c in sink.of("cmd")] == ["true", "pwd"]
    exits = sink.of("exit")
    assert [e["cmd_seq"] for e in exits] == [first] and exits[0]["seq"] < second


def test_program_input_is_still_program_input():
    """Negative control for the fix above: an echoed answer after a
    non-prompt line is not mistaken for the prompt returning."""
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"$ ")
    seq = _type(s, b"apt install x")
    s.feed_output(b"\r\nContinue? [y/n] ")
    assert _type(s, b"y") is None
    assert len(sink.of("cmd")) == 1 and sink.of("exit") == [] and s.cmd_seq == seq


# ── tools ────────────────────────────────────────────────────────────────

async def _send(monkeypatch, cmd_seq, segmentation="raw"):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    monkeypatch.setattr(client, "resolve_one",
                        lambda ref: MagicMock(session_id="s1", segmentation=segmentation))
    monkeypatch.setattr(client, "send_line", lambda *a, **k: {"cmd_seq": cmd_seq, "sent_at_seq": 5})
    monkeypatch.setattr(client, "wait_idle", lambda *a, **k: {"idle": False, "timed_out": True})
    monkeypatch.setattr(client, "read", lambda *a, **k: {"records": [], "next_seq": 6})
    key = shadow_tools._lease_key("c1", "s1")
    with shadow_tools._LEASES_LOCK:
        shadow_tools._LEASES[key] = {"lease_id": "L"}
    try:
        return await shadow_tools.ShadowSendTool().execute(session="s1", line="x", conversation_id="c1")
    finally:
        shadow_tools._forget_lease(key)


@pytest.mark.asyncio
async def test_send_hint_without_cmd_seq_waits_for_quiet(monkeypatch):
    out = await _send(monkeypatch, None)
    assert out["still_running"] is True
    assert "until=['quiet']" in out["next"] and "until=['exit']" not in out["next"]
    assert "cmd_seq=None" not in out["next"]


@pytest.mark.asyncio
async def test_send_hint_on_osc133_without_cmd_seq_keeps_waiting_for_exit(monkeypatch):
    # osc133 writes the cmd record at marker C, after send_line returned, so
    # cmd_seq is None there too -- but an exit WILL arrive, and the next one
    # is this command's.
    out = await _send(monkeypatch, None, segmentation="osc133")
    assert "until=['exit']" in out["next"] and "cmd_seq=None" not in out["next"]


@pytest.mark.asyncio
async def test_send_hint_with_cmd_seq_still_waits_for_exit(monkeypatch):
    out = await _send(monkeypatch, 7)
    assert "cmd_seq=7" in out["next"] and "until=['exit']" in out["next"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"until": ["exit"]}, {"cmd_seq": 3}, {"cmd_seq": 3, "until": "exit"}])
async def test_wait_refuses_exit_only_on_a_raw_session(monkeypatch, kw):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    called = threading.Event()
    monkeypatch.setattr(client, "resolve_one", lambda ref: MagicMock(session_id="s1", segmentation="raw"))
    monkeypatch.setattr(client, "wait", lambda *a, **k: called.set() or {"reason": "timeout"})
    # A short budget, so the unguarded code fails this test instead of
    # chaining instantly-returning fake hops forever.
    out = await shadow_tools.ShadowWaitTool().execute(session="s1", timeout_ms=1000, **kw)
    assert out["ok"] is False and out["error"] == "no_exit_signal", out
    assert "quiet" in out["message"] and not called.is_set()


@pytest.mark.asyncio
async def test_wait_on_raw_with_another_condition_proceeds(monkeypatch):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    called = threading.Event()
    monkeypatch.setattr(client, "resolve_one", lambda ref: MagicMock(session_id="s1", segmentation="raw"))
    monkeypatch.setattr(client, "wait", lambda *a, **k: called.set() or {
        "reason": "quiet", "records": [], "from_seq": 1, "next_seq": 1})
    out = await shadow_tools.ShadowWaitTool().execute(session="s1", until=["exit", "quiet"], timeout_ms=1000)
    assert out["ok"] and out["reason"] == "quiet" and called.is_set()


@pytest.mark.asyncio
async def test_wait_exit_on_prompt_heuristic_session_proceeds(monkeypatch):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    called = threading.Event()
    monkeypatch.setattr(client, "resolve_one",
                        lambda ref: MagicMock(session_id="s1", segmentation="prompt-heuristic"))
    monkeypatch.setattr(client, "wait", lambda *a, **k: called.set() or {
        "reason": "exit", "exit_code": None, "records": [], "from_seq": 1, "next_seq": 2})
    out = await shadow_tools.ShadowWaitTool().execute(session="s1", cmd_seq=3, timeout_ms=1000)
    assert out["ok"] and out["reason"] == "exit" and called.is_set()
