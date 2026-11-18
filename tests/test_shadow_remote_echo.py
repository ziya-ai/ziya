"""A command line written in one go must not be journaled as its own output.

Observed (session 53db20, zsh -> ssh dev desk, 184 columns, 2026-11-15):
every ``shadow_send`` line, and every line the human pasted, was recorded
with the tail of its own echo as the first output line -- e.g. cmd 534
(343 chars after a 28-char prompt) opened its output with ``' ls'``, the
last 3 = (28 + 343) mod 184 characters of the command.

Cause: ``send_line_to_child`` and ``handle_input`` run ``on_enter`` before
the line reaches the child, so for a line delivered in one write the echo
is not yet in the buffer.  Over ssh it arrives about a second later, after
the cmd record, and only its leading newline was eaten.  Keystroke typing
was unaffected (the echo precedes Enter).

Seams covered: segmenter -> sink for a delayed short echo, a delayed
wrapped echo (bare-CR row redraw), an echo split across reads, a command
whose real first line must be kept, a prompt-only command that must still
finish, and the ShadowCore.send_line_to_child path that produced the bug.
"""
import os
import sys
import threading
import time

import pytest

from app.shadow.segmenter import Segmenter, IDLE_FLUSH_S

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


def _idle(s, t0=1000.0):
    s._last_output_at = t0
    s.tick(now=t0 + IDLE_FLUSH_S + 0.1)


def _learned():
    """A segmenter that has learned PROMPT from a keystroke-typed command."""
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(PROMPT)
    s.feed_input(b"true")
    s.feed_output(b"true")
    s.feed_input(b"\r")
    s.on_enter(masked=False)
    s.feed_output(b"\r\n" + PROMPT)
    _idle(s)
    assert s.state == "idle" and s._prompt_suffix
    return s, sink


def _send_whole_line(s, line: bytes):
    """What send_line_to_child does: input + Enter before any echo exists."""
    s.feed_input(line)
    return s.on_enter(masked=False)


def _outputs_of(sink, cmd_seq):
    return "".join(o["text"] for o in sink.of("output") if o["cmd_seq"] == cmd_seq)


def test_delayed_short_echo_is_not_output():
    s, sink = _learned()
    seq = _send_whole_line(s, b"hostname")
    assert seq is not None and sink.records[seq - 1]["text"] == "hostname"
    s.feed_output(b"hostname\r\n")              # the echo, arriving late
    s.feed_output(b"dev-dsk-dcohn-2a\r\n" + PROMPT)
    _idle(s)
    assert _outputs_of(sink, seq) == "dev-dsk-dcohn-2a\n"
    assert [e["cmd_seq"] for e in sink.of("exit")][-1] == seq


def test_delayed_wrapped_echo_keeps_no_row_of_the_command():
    """The 53db20 shape: zsh wraps the echo at the margin with a bare CR, and
    clean_output keeps only the last row -- which must not leak either."""
    s, sink = _learned()
    cmd = ("until grep -qs CHAIN_DONE /tmp/chain.log; do sleep 15; done; "
           "cat /tmp/chain.log; echo WAIT_DONE")
    seq = _send_whole_line(s, cmd.encode())
    cut = 40
    s.feed_output(cmd[:cut].encode() + b" \r" + cmd[cut:].encode() + b"\r\n")
    s.feed_output(b"KuiperCT-courier EXIT=0\r\nWAIT_DONE\r\n" + PROMPT)
    _idle(s)
    out = _outputs_of(sink, seq)
    assert out == "KuiperCT-courier EXIT=0\nWAIT_DONE\n"
    assert cmd[cut:] not in out


def test_echo_split_across_reads_is_dropped():
    s, sink = _learned()
    seq = _send_whole_line(s, b"git log --oneline -2")
    s.feed_output(b"git log --one")
    s.feed_output(b"line -2\r")
    s.feed_output(b"\ncd4c784 feat(qos)\r\n" + PROMPT)
    _idle(s)
    assert _outputs_of(sink, seq) == "cd4c784 feat(qos)\n"


def test_first_line_that_is_not_the_echo_is_kept():
    """Remote echo off (or a shell that does not echo): the first line is
    real output and must survive the check."""
    s, sink = _learned()
    seq = _send_whole_line(s, b"printf hello")
    s.feed_output(b"hello world\r\n" + PROMPT)
    _idle(s)
    assert _outputs_of(sink, seq) == "hello world\n"


def test_real_output_that_is_a_suffix_of_the_command_is_kept():
    """Only a wrapped (bare-CR) echo may match by suffix; ``seq 9 | tail -1``
    printing ``1`` with no echo in front is output, not echo."""
    s, sink = _learned()
    seq = _send_whole_line(s, b"seq 9 | tail -1")
    s.feed_output(b"1\r\n" + PROMPT)
    _idle(s)
    assert _outputs_of(sink, seq) == "1\n"


def test_prompt_only_return_still_finishes_the_command():
    """No echo and no output: the held text is the returning prompt, which
    must be released so the heuristic can see it and journal the exit."""
    s, sink = _learned()
    seq = _send_whole_line(s, b"true")
    s.feed_output(PROMPT)
    _idle(s)
    assert s.state == "idle"
    assert [e["cmd_seq"] for e in sink.of("exit")][-1] == seq


def test_keystroke_typing_is_unchanged():
    """Echo before Enter: the cmd comes from the tail and nothing is held."""
    s, sink = _learned()
    s.feed_input(b"ls")
    s.feed_output(b"ls")
    s.feed_input(b"\r")
    seq = s.on_enter(masked=False)
    s.feed_output(b"\r\nls\r\n" + PROMPT)       # a file literally named "ls"
    _idle(s)
    assert _outputs_of(sink, seq) == "ls\n"


# -- the real PTY path ------------------------------------------------------------

@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")
def test_send_line_to_child_does_not_journal_its_own_echo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.shadow.pty_host import ShadowCore
    from app.shadow.journal import JournalReader
    core = ShadowCore(["sh", "-i"], label="echo", headless=True, control_ceiling="unrestricted")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        time.sleep(0.6)
        # Learn the prompt with keystroke-style input first.
        core.handle_input(b"true\r")
        time.sleep(1.0)
        line = "echo ECHOCHECK_" + "x" * 8 + " | tr x y"
        r = core.send_line_to_child(line)
        assert "error" not in r, r
        # Anchor on the cmd record (cmd_seq is None when the prompt is not
        # learned, which is itself a shape that leaked), then its output.
        deadline = time.monotonic() + 6
        joined = ""
        while time.monotonic() < deadline:
            recs = JournalReader(core.entry.journal).read(1, 500)["records"]
            seqs = [x["seq"] for x in recs if x.get("t") == "cmd" and x.get("text") == line]
            if seqs:
                joined = "".join(x["text"] for x in recs
                                 if x.get("t") == "output" and x.get("cmd_seq") == seqs[-1])
                if "ECHOCHECK_yyyyyyyy" in joined:
                    break
            time.sleep(0.1)
        assert "ECHOCHECK_yyyyyyyy" in joined          # the path ran
        assert "| tr x y" not in joined                # and the echo did not land
    finally:
        core.terminate_child()
        t.join(timeout=5)
