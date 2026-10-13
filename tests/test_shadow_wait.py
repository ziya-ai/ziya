"""``shadow_wait`` — event-driven waiting on a shadow session (design/shadow-wait.md).

Three seams:
  * the socket op ``wait`` on a real ``ShadowSocketServer`` + ``JournalWriter``
    (dispatch in-process; appends from another thread wake the waiter);
  * the ``shadow_wait`` tool: chains ≤60 s server hops into one long wait,
    runs them OFF the event loop, formats a bounded tail;
  * the executor: a tool's own ``exec_timeout_for`` lifts the 300 s outer
    guard, so a 1-hour wait is not killed as a "tool timeout".
"""
import asyncio
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def srv(home):
    from app.shadow import registry
    from app.shadow.journal import JournalWriter
    from app.shadow.sock_server import ShadowSocketServer
    entry = registry.create_session("t", ["/bin/sh"])
    jw = JournalWriter(entry.journal)
    server = ShadowSocketServer(entry, jw)
    server.jw = jw
    try:
        yield server
    finally:
        registry.remove(entry.session_id) if hasattr(registry, "remove") else None


def _wait(srv, **req):
    return srv.dispatch({"v": 1, "op": "wait", **req})


def _in_thread(fn):
    """Run fn in a thread; return (thread, box) where box['r'] is the result."""
    box = {}

    def run():
        box["r"] = fn()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


# ── socket op ────────────────────────────────────────────────────────────

def test_exit_fires_only_for_the_named_command_and_carries_the_code(srv):
    jw = srv.jw
    other = jw.cmd("sleep 5")
    build = jw.cmd("make")
    start = jw.output("compiling…\n", build)[-1] + 1

    t, box = _in_thread(lambda: _wait(srv, until=["exit"], cmd_seq=build, from_seq=start, timeout_ms=5000))
    time.sleep(0.15)
    jw.exit(other, 0)                       # a different command finishing must NOT wake us
    time.sleep(0.15)
    assert t.is_alive(), "exit of another cmd_seq fired the wait"
    t0 = time.monotonic()
    jw.output("error: boom\n", build)
    jw.exit(build, 2)
    t.join(3)
    assert not t.is_alive() and time.monotonic() - t0 < 1.0, "exit should wake the waiter promptly, not on timeout"
    r = box["r"]
    assert r["reason"] == "exit" and r["exit_code"] == 2 and r["still_running"] is False
    assert [x["t"] for x in r["records"]][-1] == "exit"
    assert r["new_records"] == 3                      # other's exit, output, build's exit


def test_replay_an_exit_that_landed_before_the_wait_started(srv):
    """Positive/negative pair: at or after from_seq it fires at once; before, it does not."""
    jw = srv.jw
    build = jw.cmd("make")
    exit_seq = jw.exit(build, 1)
    r = _wait(srv, until=["exit"], cmd_seq=build, from_seq=exit_seq, timeout_ms=0)
    assert r["reason"] == "exit" and r["exit_code"] == 1
    r = _wait(srv, until=["exit"], cmd_seq=build, from_seq=exit_seq + 1, timeout_ms=0)
    assert r["reason"] == "timeout" and r["still_running"] is True and r["new_records"] == 0


def test_pattern_fires_on_the_first_matching_new_line_not_history(srv):
    jw = srv.jw
    c = jw.cmd("make")
    jw.output("ERROR in the past\n", c)                 # history, before from_seq
    start = jw.output("ok\n", c)[-1] + 1
    t, box = _in_thread(lambda: _wait(srv, until=["pattern"], pattern="ERROR|FAIL", from_seq=start, timeout_ms=5000))
    time.sleep(0.15)
    assert t.is_alive(), "matched history before from_seq"
    seq = jw.output("line one\nFAIL: test_x\nline three\n", c)[-1]
    t.join(3)
    r = box["r"]
    assert r["reason"] == "pattern" and r["matched"] == {"seq": seq, "text": "FAIL: test_x"}


def test_lines_counts_output_lines_across_chunks(srv):
    jw = srv.jw
    c = jw.cmd("tail -f log")
    t, box = _in_thread(lambda: _wait(srv, until=["lines"], lines=5, from_seq=1, timeout_ms=5000))
    time.sleep(0.1)
    jw.output("1\n2\n", c)
    jw.output("3\n", c)
    time.sleep(0.15)
    assert t.is_alive()
    jw.output("4\n5\n", c)
    t.join(3)
    assert box["r"]["reason"] == "lines" and box["r"]["output_lines"] == 5


def test_change_fires_on_any_record_and_default_until_depends_on_cmd_seq(srv):
    jw = srv.jw
    t, box = _in_thread(lambda: _wait(srv, timeout_ms=5000))        # no until, no cmd_seq → change
    time.sleep(0.1)
    jw.meta("comment", {"text": "hi"})
    t.join(3)
    assert box["r"]["reason"] == "change"
    c = jw.cmd("x")
    r = _wait(srv, cmd_seq=c, timeout_ms=0)                           # no until, cmd_seq → exit
    assert r["reason"] == "timeout" and r["still_running"] is True


def test_quiet_matches_wait_idle_and_timeout_returns_the_tail(srv):
    jw = srv.jw
    c = jw.cmd("x")
    t0 = time.monotonic()
    r = _wait(srv, until=["quiet"], quiet_ms=200, from_seq=1, timeout_ms=5000)
    assert r["reason"] == "quiet" and 0.15 < time.monotonic() - t0 < 1.5
    for i in range(7):
        jw.output(f"l{i}\n", c)
    r = _wait(srv, until=["exit"], cmd_seq=c, from_seq=1, timeout_ms=100, max_tail_records=3)
    assert r["reason"] == "timeout" and r["timed_out"] is True
    assert r["new_records"] == 8 and len(r["records"]) == 3 and r["tail_truncated"] is True
    assert r["records"][-1]["text"] == "l6\n"


def test_ask_fires_on_a_pending_ask_or_a_new_ask_record(srv):
    jw = srv.jw
    srv.entry.pending_ask = True
    assert _wait(srv, until=["ask"], timeout_ms=0)["reason"] == "ask"
    srv.entry.pending_ask = False
    t, box = _in_thread(lambda: _wait(srv, until=["ask"], timeout_ms=5000))
    time.sleep(0.1)
    jw.meta("ask", {"question": "which env?"})
    t.join(3)
    assert box["r"]["reason"] == "ask"


def test_session_end_always_returns(srv):
    jw = srv.jw
    t, box = _in_thread(lambda: _wait(srv, until=["exit"], cmd_seq=99, timeout_ms=5000))
    time.sleep(0.1)
    jw.meta("exit_session", {"code": 0})
    t.join(3)
    assert box["r"]["reason"] == "session_ended"


def test_per_request_cap_and_bad_input(srv):
    from app.shadow.sock_server import ShadowSocketServer
    assert ShadowSocketServer.WAIT_CAP_MS == 60000
    assert "error" in _wait(srv, until=["nonsense"])
    assert "error" in _wait(srv, until=["pattern"])                   # pattern missing
    assert "error" in _wait(srv, until=["pattern"], pattern="(a+)+")  # unsafe regex, as search
    assert "error" in _wait(srv, until="exit")                        # must be a list


# ── tool ─────────────────────────────────────────────────────────────────

def test_tool_registered():
    from app.mcp.builtin_tools import get_builtin_tools_for_category
    assert "shadow_wait" in {t().name for t in get_builtin_tools_for_category("shadow")}


@pytest.mark.asyncio
async def test_tool_chains_hops_off_the_event_loop_and_formats_a_tail(monkeypatch):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    loop_thread = threading.get_ident()
    calls = []

    def fake_wait(ref, **p):
        calls.append((threading.get_ident(), dict(p)))
        if len(calls) < 3:
            return {"reason": "timeout", "from_seq": 10, "next_seq": 10, "new_records": 0,
                    "output_lines": 0, "records": [], "tail_truncated": False,
                    "still_running": True, "timed_out": True}
        recs = [{"t": "output", "seq": s, "cmd_seq": 7, "text": f"line {s}\n"} for s in range(10, 40)]
        recs.append({"t": "exit", "seq": 40, "cmd_seq": 7, "code": 3})
        return {"reason": "exit", "exit_code": 3, "exit_seq": 40, "from_seq": 10, "next_seq": 41,
                "new_records": 31, "output_lines": 30, "records": recs, "tail_truncated": False,
                "still_running": False, "timed_out": False}

    monkeypatch.setattr(client, "resolve_one", lambda ref: MagicMock(session_id="s1"))
    monkeypatch.setattr(client, "wait", fake_wait)
    out = await shadow_tools.ShadowWaitTool().execute(
        session="s1", cmd_seq=7, from_seq=None, timeout_ms=3 * client.WAIT_CHUNK_MS,
        tail_lines=5, conversation_id="c1")
    assert out["ok"] and out["reason"] == "exit" and out["exit_code"] == 3
    # Three hops of ≤ one chunk each; from_seq anchored after the first hop.
    assert len(calls) == 3 and all(p["timeout_ms"] <= client.WAIT_CHUNK_MS for _, p in calls)
    assert calls[0][1].get("from_seq") is None and calls[1][1]["from_seq"] == 10
    assert all(tid != loop_thread for tid, _ in calls), "client.wait ran on the event loop"
    # Tail is the LAST tail_lines lines; the full count is still reported.
    assert out["tail"].splitlines()[-1].endswith("→ exit 3")
    assert len(out["tail"].splitlines()) == 5 and out["tail_truncated"] is True
    assert out["new_records"] == 31 and out["next_seq"] == 41 and "shadow_read" in out["next"]


@pytest.mark.asyncio
async def test_tool_stops_chaining_when_the_budget_is_spent(monkeypatch):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    n = {"calls": 0}

    def fake_wait(ref, **p):
        n["calls"] += 1
        return {"reason": "timeout", "from_seq": 1, "next_seq": 1, "new_records": 0, "output_lines": 0,
                "records": [], "tail_truncated": False, "still_running": True, "timed_out": True}
    monkeypatch.setattr(client, "resolve_one", lambda ref: MagicMock(session_id="s1"))
    monkeypatch.setattr(client, "wait", fake_wait)
    out = await shadow_tools.ShadowWaitTool().execute(session="s1", cmd_seq=7, timeout_ms=1000)
    assert n["calls"] == 1 and out["reason"] == "timeout" and out["still_running"] is True
    assert "shadow_wait" in out["next"]


def test_tool_declares_its_own_exec_timeout():
    from app.mcp.tools.shadow_tools import ShadowWaitTool
    from app.mcp.tools.base import BaseMCPTool
    assert ShadowWaitTool().exec_timeout_for({"timeout_ms": 3_600_000}) == 3600.0
    assert ShadowWaitTool().exec_timeout_for({}) == 240.0

    class Plain(BaseMCPTool):
        name = "plain"

        async def execute(self, **kw):
            return {}
    assert Plain().exec_timeout_for({"timeout_ms": 10**9}) is None


# ── shadow_send must not block the loop either ───────────────────────────

@pytest.mark.asyncio
async def test_shadow_send_runs_blocking_client_calls_off_the_loop(monkeypatch):
    from app.mcp.tools import shadow_tools
    from app.shadow import client
    loop_thread = threading.get_ident()
    seen = {}
    monkeypatch.setattr(client, "resolve_one", lambda ref: MagicMock(session_id="s1"))
    monkeypatch.setattr(client, "send_line", lambda *a, **k: seen.setdefault("send", threading.get_ident()) and {"cmd_seq": 5, "sent_at_seq": 5})
    monkeypatch.setattr(client, "wait_idle", lambda *a, **k: seen.setdefault("idle", threading.get_ident()) and {"idle": False, "timed_out": True})
    monkeypatch.setattr(client, "read", lambda *a, **k: {"records": [], "next_seq": 6})
    key = shadow_tools._lease_key("c1", "s1")
    with shadow_tools._LEASES_LOCK:
        shadow_tools._LEASES[key] = {"lease_id": "L"}
    try:
        out = await shadow_tools.ShadowSendTool().execute(session="s1", line="make", conversation_id="c1")
    finally:
        shadow_tools._forget_lease(key)
    assert out["ok"] and seen.keys() == {"send", "idle"}, "both client calls must have run"
    assert seen["send"] != loop_thread and seen["idle"] != loop_thread
    # Not idle → the result tells the model what to do next instead of leaving it to sleep.
    assert out["still_running"] is True and "shadow_wait" in out["next"] and "cmd_seq=5" in out["next"]


# ── executor honours the tool's own timeout ──────────────────────────────

@pytest.mark.asyncio
async def test_executor_lifts_its_guard_for_a_tool_that_declares_a_long_wait(monkeypatch):
    from app.tool_execution import ToolExecContext, execute_single_tool
    from app.mcp.enhanced_tools import DirectMCPTool
    from app.mcp.tools.base import BaseMCPTool
    monkeypatch.setenv("TOOL_EXEC_TIMEOUT", "1")

    class Slow(BaseMCPTool):
        name = "slow_wait"
        description = "test tool that sleeps"

        def __init__(self, declare):
            self.declare = declare

        def exec_timeout_for(self, args):
            return 2.0 if self.declare else None

        async def execute(self, **kw):
            await asyncio.sleep(1.4)
            return {"ok": True, "done": True}

    async def run(declare):
        manager = MagicMock()
        manager._exceeds_turn_ceiling = lambda *_: False
        manager._is_repetitive_call = lambda *_: False
        tool = DirectMCPTool(Slow(declare))
        ctx = ToolExecContext(
            tool_id="t1", tool_name="slow_wait", actual_tool_name="slow_wait", args={},
            all_tools=[tool], internal_tool_names=set(), mcp_manager=manager,
            project_root=os.getcwd(), conversation_id="c-wait", conversation=[],
            recent_commands=[],
            inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                              'growth_factor': 2.5, 'last_was_throttled': False},
            iteration_start_time=0, track_yield_fn=lambda x: x,
            drain_feedback_fn=lambda: [], executor=MagicMock(),
        )
        events = []
        async for ev in execute_single_tool(ctx):
            events.append(ev)
        return [e for e in events if e.get("type") == "_tool_result"]

    lifted = await run(True)
    assert lifted and "done" in str(lifted[-1]["result"]), "declared timeout must let the call finish"
    plain = await run(False)                   # positive control: without the declaration the 1 s guard bites
    assert plain and "timed out" in str(plain[-1]["result"]).lower()
