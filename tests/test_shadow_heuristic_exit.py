"""Prompt-heuristic sessions must journal command completion.

Observed (session d0fc34, zsh -> ssh dev desk, 2026-10-07): 60 journal
records, zero ``exit`` records.  The segmenter detected each command's end
(prompt reappeared) but wrote nothing, so ``shadow_wait(cmd_seq=N)`` --
default ``until=['exit']``, and what ``shadow_send``'s ``next`` hint asks
for -- slept the full timeout (240 s by default) on commands that finished
in under a second.  prompt-heuristic is the mode every ssh'd shell lands in.

Seams covered:
  * segmenter -> sink: a heuristic finish emits exit(cmd_seq, code=None),
    on both finish paths (idle tick, and Enter before tick noticed);
  * osc133 still emits exactly one exit, with its real code;
  * segmenter -> JournalWriter -> sock_server wait: a cmd_seq wait wakes on
    the heuristic finish, not on timeout;
  * renderers: a null code reads as "exit code unknown", not "exit None".
"""
import os
import sys
import threading
import time

import pytest

from app.shadow.segmenter import Segmenter, IDLE_FLUSH_S

os.environ.setdefault("ZIYA_TESTING", "1")


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


PROMPT = b"dev-dsk % "


def _run(s, cmd: bytes):
    s.feed_input(cmd)
    s.feed_output(cmd)
    return s.on_enter(masked=False)


def _learned():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(PROMPT)
    return s, sink


def test_heuristic_finish_on_idle_emits_exit_with_unknown_code():
    s, sink = _learned()
    seq = _run(s, b"ls")
    assert seq is not None
    s.feed_output(b"\r\na  b\r\n" + PROMPT)
    _idle(s)
    exits = sink.of("exit")
    assert len(exits) == 1, sink.records
    assert exits[0]["cmd_seq"] == seq and exits[0]["code"] is None
    # The exit follows the command's output, never precedes it.
    out = sink.of("output")[-1]
    assert out["cmd_seq"] == seq and out["seq"] < exits[0]["seq"]


def test_heuristic_finish_on_next_enter_emits_exit_before_the_next_cmd():
    s, sink = _learned()
    first = _run(s, b"true")
    s.feed_output(b"\r\n" + PROMPT)          # prompt back, tick never ran
    second = _run(s, b"pwd")
    assert second is not None and second != first
    exits = sink.of("exit")
    assert [e["cmd_seq"] for e in exits] == [first]
    assert exits[0]["seq"] < second


def test_bare_enter_and_running_program_input_emit_no_exit():
    s, sink = _learned()
    s.on_enter(masked=False)                 # bare Enter at the prompt
    seq = _run(s, b"apt install x")
    s.feed_output(b"\r\nContinue? [y/n] ")
    s.feed_input(b"y"); s.feed_output(b"y")
    s.on_enter(masked=False)                 # answer to the program
    assert sink.of("exit") == []
    s.feed_output(b"\r\ndone\r\n" + PROMPT)
    _idle(s)
    assert [e["cmd_seq"] for e in sink.of("exit")] == [seq]


def test_osc133_still_emits_one_exit_with_the_real_code():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"\x1b]133;A\x07$ \x1b]133;B\x07")
    s.feed_output(b"false")
    s.on_enter(masked=False)
    s.feed_output(b"\x1b]133;C\x07\r\n\x1b]133;D;1\x07\x1b]133;A\x07$ \x1b]133;B\x07")
    exits = sink.of("exit")
    assert len(exits) == 1 and exits[0]["code"] == 1


def test_osc133_missing_d_still_closes_the_command():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"\x1b]133;A\x07$ \x1b]133;B\x07")
    s.feed_output(b"sleep 1")
    s.on_enter(masked=False)
    s.feed_output(b"\x1b]133;C\x07\r\nhi\r\n")
    s.feed_output(b"\x1b]133;A\x07$ ")       # A with no D before it
    exits = sink.of("exit")
    assert len(exits) == 1 and exits[0]["code"] is None


def test_renderers_say_unknown_rather_than_none():
    from app.shadow.client import format_records
    from app.shadow.journal import searchable_text
    txt = format_records([{"t": "exit", "seq": 9, "cmd_seq": 8, "code": None}])
    assert "None" not in txt and "exit code unknown" in txt
    assert "None" not in searchable_text({"t": "exit", "code": None})
    # Known codes are unchanged.
    assert "→ exit 3" in format_records([{"t": "exit", "seq": 9, "cmd_seq": 8, "code": 3}])


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")
def test_cmd_seq_wait_wakes_on_heuristic_finish_not_timeout(tmp_path, monkeypatch):
    """End to end: real Segmenter -> real JournalWriter -> real wait op."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.shadow import registry
    from app.shadow.journal import JournalWriter
    from app.shadow.sock_server import ShadowSocketServer
    entry = registry.create_session("t", ["/bin/zsh"])
    jw = JournalWriter(entry.journal)
    srv = ShadowSocketServer(entry, jw)
    s = Segmenter(jw)
    s.feed_output(PROMPT)
    seq = _run(s, b"ls")
    start = seq + 1

    box = {}
    t = threading.Thread(target=lambda: box.update(r=srv.dispatch(
        {"v": 1, "op": "wait", "cmd_seq": seq, "from_seq": start, "timeout_ms": 5000})),
        daemon=True)
    t.start()
    time.sleep(0.15)
    assert t.is_alive(), "wait returned before the command finished"
    t0 = time.monotonic()
    s.feed_output(b"\r\na  b\r\n" + PROMPT)
    _idle(s)
    t.join(3)
    assert not t.is_alive() and time.monotonic() - t0 < 1.0, "wait slept to timeout"
    r = box["r"]
    assert r["reason"] == "exit", r
    assert r["exit_code"] is None and r["still_running"] is False
