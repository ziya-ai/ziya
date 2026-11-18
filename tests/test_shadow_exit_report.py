"""Real exit codes for lines shadow_send types, with nothing installed on the host.

Observed (session 53db20, zsh -> ssh dev desk, 2026-11-15): every command
ended "→ done (exit code unknown)".  The remote shell emits no OSC 133, and
installing hooks there is not acceptable.  In a prompt-heuristic stretch
send_line now types the line plus ``; _zx=$?; printf '<report>' $_zx;
(exit $_zx)``: plain POSIX shell, run by the host's own shell, restoring
``$?``.  The segmenter ends the command on the report with its real code,
keeps the cmd record as the line the model sent, and drops the longer echo.

Seams: when a line gets a report (and when it is left alone), the report
reaching the sink as an exit with the right code and only for its own
command and session, the echo of the extended line not reaching output,
and a real ``sh -i`` driven through the shadow_send tool.
"""
import asyncio
import sys
import threading
import time

import pytest

from app.shadow.segmenter import (Segmenter, IDLE_FLUSH_S, MODE_OSC133, MODE_PROMPT,
                                  _report_separator)

PROMPT = b"dev-dsk % "


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

    def exits_for(self, seq):
        return [e["code"] for e in self.of("exit") if e["cmd_seq"] == seq]

    def out_for(self, seq):
        return "".join(o["text"] for o in self.of("output") if o["cmd_seq"] == seq)


def _idle(s):
    s._last_output_at = 1000.0
    s.tick(now=1000.0 + IDLE_FLUSH_S + 0.1)


def _learned():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(PROMPT)
    s.feed_input(b"true")
    s.feed_output(b"true")
    s.feed_input(b"\r")
    s.on_enter(masked=False)
    s.feed_output(b"\r\n" + PROMPT)
    _idle(s)
    assert s.mode == MODE_PROMPT and s.state == "idle"
    return s, sink


def _send(s, line: str):
    """What ShadowCore.send_line_to_child does."""
    s.feed_input(line.encode())
    seq = s.on_enter(masked=False)
    return seq, s.with_exit_report(line, seq)


def _report(s, seq, code, token=None):
    return b"\x1b]6973;%s;%d;%d\x07" % ((token or s.exit_token).encode(), seq, code)


@pytest.mark.parametrize("line,sep", [
    ("ls -l", "; "), ("make;", " "), ("a && b", "; "), ("{ a; }", "; "),
    ("echo 'x;y'", "; "), ("ls /bin", "; "),
    ("sleep 9 &", None), ("a &&", None), ("a |", None), ("a \\", None),
    ("echo hi # note", None), ("cat <<EOF", None), ("echo 'open", None),
    ("for x in a b; do", None), ("if true; then", None), ("case x in", None),
])
def test_which_lines_get_a_report(line, sep):
    assert _report_separator(line) == sep


def test_report_ends_the_command_with_its_code_and_the_echo_is_dropped():
    s, sink = _learned()
    seq, typed = _send(s, "grep -q x /nope")
    assert typed.startswith("grep -q x /nope; _zx=$?; printf ") and typed.endswith("(exit $_zx)")
    assert sink.records[seq - 1]["text"] == "grep -q x /nope"      # cmd record unchanged
    # The extended echo arrives late and wraps at the margin (bare CR).
    s.feed_output(typed[:30].encode() + b" \r" + typed[30:].encode() + b"\r\n")
    s.feed_output(b"grep: /nope: No such file\r\n" + _report(s, seq, 2) + PROMPT)
    assert sink.exits_for(seq) == [2]
    assert s.state == "idle"
    _idle(s)
    assert sink.exits_for(seq) == [2]                              # no second, unknown exit
    assert sink.out_for(seq) == "grep: /nope: No such file\n"


def test_output_without_a_trailing_newline_is_kept():
    s, sink = _learned()
    seq, typed = _send(s, "printf foo")
    s.feed_output(typed.encode() + b"\r\nfoo" + _report(s, seq, 0) + PROMPT)
    assert sink.exits_for(seq) == [0] and sink.out_for(seq) == "foo"


def test_reports_for_another_session_or_an_ended_command_are_ignored():
    s, sink = _learned()
    seq, typed = _send(s, "true")
    s.feed_output(typed.encode() + b"\r\n" + _report(s, seq, 5, token="deadbeef"))
    s.feed_output(_report(s, seq - 3, 7))
    assert sink.exits_for(seq) == []
    s.feed_output(PROMPT)
    _idle(s)
    assert sink.exits_for(seq) == [None]                           # heuristic still ends it


def test_lines_that_are_not_new_commands_are_sent_unchanged():
    # Input to a running program: on_enter opens no command.
    s, sink = _learned()
    seq, _ = _send(s, "cat")
    s.feed_output(b"cat\r\n")
    _idle(s)
    s.feed_input(b"hello")
    assert s.with_exit_report("hello", s.on_enter(masked=False)) == "hello"
    # osc133: the shell's own marks report the code.
    sink2 = Sink()
    s2 = Segmenter(sink2)
    s2.feed_output(b"\x1b]133;A\x07z% \x1b]133;B\x07")
    assert s2.mode == MODE_OSC133
    s2.feed_input(b"ls")
    assert s2.with_exit_report("ls", s2.on_enter(masked=False)) == "ls"
    # raw: no prompt, no command boundary to attach a code to.
    s3 = Segmenter(Sink())
    s3.feed_input(b"ls")
    assert s3.with_exit_report("ls", s3.on_enter(masked=False)) == "ls"


# -- real shell through the tool ------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")
def test_shadow_send_reports_real_exit_codes_from_a_plain_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PS1", "tst$ ")
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-i"], label="codes", headless=True, control_ceiling="unrestricted")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    conv = "conv-exit-report"
    try:
        time.sleep(0.6)
        for ch in b"true":
            core.handle_input(bytes([ch]))
            time.sleep(0.05)
        time.sleep(0.2)
        core.handle_input(b"\r")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and core.segmenter.mode != MODE_PROMPT:
            time.sleep(0.05)
        assert core.segmenter.mode == MODE_PROMPT
        time.sleep(1.0)
        from app.mcp.tools.shadow_tools import ShadowControlTool, ShadowSendTool
        sid = core.entry.session_id
        acq = _run(ShadowControlTool().execute(session=sid, conversation_id=conv,
                                               restriction="unrestricted", policy="none"))
        assert acq.get("ok") and acq.get("granted"), acq
        send = ShadowSendTool()

        def go(line):
            r = _run(send.execute(session=sid, conversation_id=conv, line=line,
                                  quiet_ms=400, timeout_ms=8000))
            assert r["ok"], r
            assert not r.get("still_running"), r
            assert "_zx" not in r["transcript"], r                 # report never shows
            return r["transcript"]

        assert "→ exit 1" in go("false")
        tr = go("echo ST_$?")                                      # $? restored for the user
        assert "ST_1" in tr and "→ exit 0" in tr
        assert "→ exit 3" in go("sh -c 'exit 3'")
        assert "exit code unknown" in go("echo hi # no report after a comment")
    finally:
        core.terminate_child()
        t.join(timeout=5)
        from app.mcp.tools import shadow_tools
        for k in list(shadow_tools._LEASES):
            shadow_tools._forget_lease(k)
