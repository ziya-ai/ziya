"""osc133 mode lapsing when the marks stop.

Once a stretch was osc133 the segmenter never left it: from an
instrumented shell into an un-instrumented one (ssh, ``sh``, or the local
shell after a dropped ssh) every later command went unrecorded.  An Enter
on a prompted line while a marked command runs now lapses to the heuristic
until the next mark.

Seams: segmenter lapse and recovery (and the codes it must not misassign),
type-ahead staying input, the C fallback when nothing was echoed, and a
real zsh instrumented the way the C-x C-z i menu does it (typed into the
live shell, nothing in any rc file), driven through shadow_send, with an
uninstrumented nested shell.
"""
import asyncio
import shutil
import sys
import threading
import time

import pytest

from app.shadow.segmenter import Segmenter, IDLE_FLUSH_S, MODE_OSC133

A, B, C = b"\x1b]133;A\x07", b"\x1b]133;B\x07", b"\x1b]133;C\x07"


def D(code):
    return b"\x1b]133;D;%d\x07" % code


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

    def seq_of(self, text):
        return [r["seq"] for r in self.of("cmd") if r["text"] == text][-1]

    def exits_for(self, seq):
        return [e["code"] for e in self.of("exit") if e["cmd_seq"] == seq]

    def out_for(self, seq):
        return "".join(o["text"] for o in self.of("output") if o["cmd_seq"] == seq)


def _type(s, line: bytes):
    """Keystroke typing: the echo precedes Enter."""
    s.feed_input(line)
    s.feed_output(line)
    s.feed_input(b"\r")
    return s.on_enter(masked=False)


def _idle(s):
    s._last_output_at = 1000.0
    s.tick(now=1000.0 + IDLE_FLUSH_S + 0.1)


def _marked(s, line: bytes, output: bytes, code: int):
    s.feed_output(A + b"loc% " + B)
    _type(s, line)
    s.feed_output(C + b"\r\n" + output + D(code))


def test_unmarked_shell_lapses_to_heuristic_and_marks_recover():
    sink = Sink()
    s = Segmenter(sink)
    _marked(s, b"false", b"", 1)
    assert s.mode == MODE_OSC133
    assert sink.exits_for(sink.seq_of("false")) == [1]
    # An instrumented shell starts an un-instrumented one.
    s.feed_output(A + b"loc% " + B)
    _type(s, b"ssh h")
    s.feed_output(C + b"\r\nremote $ ")
    seq_ls = _type(s, b"ls")
    assert s.mode != MODE_OSC133
    assert seq_ls == sink.seq_of("ls")                 # recorded, not swallowed
    assert sink.exits_for(sink.seq_of("ssh h")) == [None]
    s.feed_output(b"\r\nfile1\r\nremote $ ")
    _idle(s)
    assert sink.out_for(seq_ls) == "file1\n"
    assert sink.exits_for(seq_ls) == [None]
    # Still running when the marks come back: D's 0 is the outer shell's.
    _type(s, b"exit")
    s.feed_output(b"\r\nlogout\r\n" + D(0) + A + b"loc% " + B)
    assert s.mode == MODE_OSC133
    assert sink.exits_for(sink.seq_of("exit")) == [None]
    _type(s, b"true")
    s.feed_output(C + b"\r\n" + D(0))
    assert sink.exits_for(sink.seq_of("true")) == [0]


def test_type_ahead_under_a_marked_command_stays_input():
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(A + b"loc% " + B)
    _type(s, b"sleep 5")
    s.feed_output(C + b"\r\n")
    assert _type(s, b"ls") is None                     # tty echo at column 0
    assert s.mode == MODE_OSC133
    assert [c["text"] for c in sink.of("cmd")] == ["sleep 5"]


def test_c_falls_back_to_the_entered_line_when_nothing_was_echoed():
    """A theme that rebuilt the prompt after the hook leaves no B: the
    echo is then discarded with the prompt, and the line taken at Enter
    names the command."""
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(A + b"loc% ")                        # no B
    _type(s, b"uptime")
    s.feed_output(C + b"\r\nup 3 days\r\n" + D(0))
    seq = sink.seq_of("uptime")
    assert sink.exits_for(seq) == [0]
    assert sink.out_for(seq) == "up 3 days\n"


# -- real shell ----------------------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _wait_mode(core, mode, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and core.segmenter.mode != mode:
        time.sleep(0.05)
    return core.segmenter.mode


@pytest.mark.skipif(sys.platform.startswith("win") or not shutil.which("zsh"), reason="needs zsh")
def test_menu_instrumented_zsh_records_codes_and_survives_a_nested_plain_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZDOTDIR", str(tmp_path))
    (tmp_path / ".zshrc").write_text("PROMPT='z%# '\n")
    from app.shadow.pty_host import ShadowCore, INSTRUMENT_SNIPPETS, _PASTE_BEGIN, _PASTE_END
    core = ShadowCore(["zsh", "-i"], label="menu", headless=True, control_ceiling="unrestricted")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    conv = "conv-osc133-menu"
    try:
        time.sleep(1.0)
        # What the menu's "instrument" action types (pty_host._compose_done).
        snippet = INSTRUMENT_SNIPPETS["zsh"].encode()
        if core.bracketed_paste:
            snippet = _PASTE_BEGIN + snippet + _PASTE_END
        core.handle_input(snippet + b"\r")
        assert _wait_mode(core, MODE_OSC133) == MODE_OSC133, "instrument snippet emitted no marks"
        time.sleep(0.5)
        from app.mcp.tools.shadow_tools import ShadowControlTool, ShadowSendTool
        sid = core.entry.session_id
        acq = _run(ShadowControlTool().execute(session=sid, conversation_id=conv,
                                               restriction="unrestricted", policy="none"))
        assert acq.get("ok") and acq.get("granted"), acq
        send = ShadowSendTool()

        def go(line, timeout_ms=8000):
            r = _run(send.execute(session=sid, conversation_id=conv, line=line,
                                  quiet_ms=400, timeout_ms=timeout_ms))
            assert r["ok"], r
            return r

        assert "→ exit 1" in go("false")["transcript"]
        r = go("echo HOOKED_$((40+2))")
        assert "HOOKED_42" in r["transcript"] and "→ exit 0" in r["transcript"], r
        go("PS1='tst$ ' sh -i", timeout_ms=3000)
        r = go("echo PLAIN_$((2+3))", timeout_ms=3000)
        assert "PLAIN_5" in r["transcript"], r
        from app.shadow.journal import JournalReader
        recs = JournalReader(core.entry.journal).read(1, 2000)["records"]
        plain = [x["seq"] for x in recs if x.get("t") == "cmd" and x.get("text") == "echo PLAIN_$((2+3))"]
        assert plain, "the nested shell's command was not recorded"
        out = "".join(x["text"] for x in recs if x.get("t") == "output" and x.get("cmd_seq") == plain[-1])
        assert "PLAIN_5" in out and "$((2+3))" not in out
        go("exit", timeout_ms=3000)
        assert _wait_mode(core, MODE_OSC133) == MODE_OSC133
        assert "→ exit 3" in go("sh -c 'exit 3'")["transcript"]
    finally:
        core.terminate_child()
        t.join(timeout=5)
        from app.mcp.tools import shadow_tools
        for k in list(shadow_tools._LEASES):
            shadow_tools._forget_lease(k)



def test_optional_hooks_flag_prints_the_rc_block():
    """--hooks is optional: exact codes for hand-typed commands on hosts the
    user chooses to hook.  Nothing else depends on it (see
    test_shadow_exit_report.py for the no-install default)."""
    import subprocess
    from app.shadow.prompt_marker import hooks_snippet
    r = subprocess.run([sys.executable, "-c",
                        "import sys; from app.cli import main; sys.argv=['ziya','shadow','--hooks','zsh']; main()"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert hooks_snippet("zsh") in r.stdout and "133;D" in r.stdout
