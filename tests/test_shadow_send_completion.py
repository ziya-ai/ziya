"""shadow_send must not report a silent, still-running command as finished.

Observed (session 53db20, 2026-11-15): ``until grep -qs CHAIN_DONE ...;
do sleep 15; done`` ran for 10 and 28 minutes, yet shadow_send returned
``idle: true`` with no ``still_running`` and no ``next`` hint, because
``wait_idle`` only measures 800 ms of journal silence.  The model then
waited without a cmd_seq.  Commands whose first output arrives over ssh
after the quiet window came back with only their echo.

In prompt-heuristic and osc133 sessions the journal knows the command has
no exit yet; shadow_send now waits the rest of timeout_ms for that exit
and otherwise reports still_running with a cmd_seq wait hint.

Real headless ``sh -i`` sessions, driven through the chat-side tools.
"""
import asyncio
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")

CONV = "conv-send-completion"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def learned(tmp_path, monkeypatch):
    """A controllable session whose prompt the segmenter has learned."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PS1", "tst$ ")
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-i"], label="done", headless=True, control_ceiling="unrestricted")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.6)
    # Keystroke by keystroke, so the echo precedes Enter and the prompt is
    # learned (whole-line input alone never teaches it).
    for ch in b"true":
        core.handle_input(bytes([ch]))
        time.sleep(0.05)
    time.sleep(0.2)
    core.handle_input(b"\r")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and core.segmenter.mode != "prompt-heuristic":
        time.sleep(0.05)
    assert core.segmenter.mode == "prompt-heuristic", "prompt was not learned"
    time.sleep(1.0)
    from app.mcp.tools.shadow_tools import ShadowControlTool, ShadowSendTool
    sid = core.entry.session_id
    acq = _run(ShadowControlTool().execute(session=sid, conversation_id=CONV,
                                           restriction="unrestricted", policy="none"))
    assert acq.get("ok") and acq.get("granted"), acq
    yield core, sid, ShadowSendTool()
    core.terminate_child()
    t.join(timeout=5)
    from app.mcp.tools import shadow_tools
    for k in list(shadow_tools._LEASES):
        shadow_tools._forget_lease(k)


def test_silent_command_that_finishes_within_timeout_is_returned_complete(learned):
    core, sid, send = learned
    t0 = time.monotonic()
    r = _run(send.execute(session=sid, conversation_id=CONV, quiet_ms=300, timeout_ms=10000,
                          line="sleep 2; echo SILENT_$((40+2))"))
    assert r["ok"], r
    assert r["cmd_seq"] is not None                   # the heuristic path ran
    assert "SILENT_42" in r["transcript"]             # waited past the silence
    # (the arithmetic keeps the marker out of the echoed command text)
    assert "→ done" in r["transcript"] or "→ exit" in r["transcript"]
    assert not r.get("still_running"), r
    assert time.monotonic() - t0 >= 2.0


def test_silent_command_still_running_at_timeout_says_so_with_a_cmd_seq_hint(learned):
    core, sid, send = learned
    r = _run(send.execute(session=sid, conversation_id=CONV, quiet_ms=300, timeout_ms=2000,
                          line="sleep 6; echo LATE_$((1+1))"))
    assert r["ok"], r
    cs = r["cmd_seq"]
    assert cs is not None
    assert r.get("still_running") is True, r
    assert f"cmd_seq={cs}" in r.get("next", "") and "until=['exit']" in r["next"]
    assert "LATE_2" not in r["transcript"]


def test_unfinished_command_helper_by_segmentation():
    from app.mcp.tools.shadow_tools import _unfinished_command as uf
    recs = [{"t": "cmd", "seq": 11, "text": "make"}, {"t": "output", "seq": 12, "cmd_seq": 11}]
    assert uf(recs, 11, 10, "prompt-heuristic") == 11
    assert uf(recs + [{"t": "exit", "seq": 13, "cmd_seq": 11}], 11, 10, "prompt-heuristic") is None
    # osc133: cmd_seq is assigned after send_line returns; take the first cmd.
    assert uf(recs, None, 10, "osc133") == 11
    assert uf(recs, None, 11, "osc133") is None       # no cmd after the send yet
    # raw never records an end: nothing to wait for.
    assert uf(recs, None, 10, "raw") is None
