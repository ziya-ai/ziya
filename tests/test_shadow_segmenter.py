"""Segmentation ladder tests (Docs/design/shadow-sessions.md §4.1)."""
import pytest

from app.shadow.segmenter import (
    Segmenter, MODE_RAW, MODE_PROMPT, MODE_OSC133, IDLE_FLUSH_S,
    clean_output, _split_incomplete_escape,
)


class Sink:
    """Records every call the segmenter makes, in order."""

    def __init__(self):
        self.records = []
        self._seq = 0

    def _add(self, rec):
        self._seq += 1
        rec["seq"] = self._seq
        self.records.append(rec)
        return self._seq

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


@pytest.fixture
def seg():
    sink = Sink()
    return Segmenter(sink), sink


def _idle(s, t0=1000.0):
    """Advance the idle clock past IDLE_FLUSH_S."""
    s._last_output_at = t0
    s.tick(now=t0 + IDLE_FLUSH_S + 0.1)


# -- helpers ---------------------------------------------------------------------

def test_clean_output_strips_ansi_and_resolves_cr():
    raw = "\x1b[32mok\x1b[0m\r\nprogress 10%\rprogress 100%\r\nab\bc\n"
    assert clean_output(raw) == "ok\nprogress 100%\nac\n"


def test_split_incomplete_escape_carries_partial_osc_and_csi():
    body, carry = _split_incomplete_escape("hello\x1b]133;")
    assert body == "hello" and carry == "\x1b]133;"
    body, carry = _split_incomplete_escape("x\x1b[1;")
    assert body == "x" and carry == "\x1b[1;"
    body, carry = _split_incomplete_escape("x\x1b[1;31m")
    assert body == "x\x1b[1;31m" and carry == ""


# -- raw ----------------------------------------------------------------------------

def test_raw_mode_flushes_complete_lines_on_idle_with_null_cmd_seq(seg):
    s, sink = seg
    s.feed_output(b"line one\nline two\npartial")
    _idle(s)
    outs = sink.of("output")
    assert len(outs) == 1
    assert outs[0]["text"] == "line one\nline two\n"
    assert outs[0]["cmd_seq"] is None
    assert s.mode == MODE_RAW
    # The unterminated line is held (it may be a prompt), not journaled yet.
    assert "partial" not in outs[0]["text"]


# -- prompt heuristic ---------------------------------------------------------------

def test_prompt_heuristic_learns_prompt_and_segments_commands(seg):
    s, sink = seg
    prompt = b"user@host:~$ "
    s.feed_output(prompt)
    # Type "ls" — the shell echoes it after the prompt.
    s.feed_input(b"ls")
    s.feed_output(b"ls")
    s.feed_input(b"\r")
    cmd_seq = s.on_enter(masked=False)
    assert cmd_seq is not None
    assert sink.of("cmd")[-1]["text"] == "ls"
    assert s.mode == MODE_PROMPT
    modes = [m for m in sink.of("meta") if m["event"] == "segmentation"]
    assert modes and modes[0]["data"] == {"from": MODE_RAW, "to": MODE_PROMPT}

    # Command output, then the prompt reappears.
    s.feed_output(b"\r\nfile_a\r\nfile_b\r\n" + prompt)
    _idle(s)
    outs = sink.of("output")
    assert len(outs) == 1
    assert outs[0]["cmd_seq"] == cmd_seq
    assert outs[0]["text"] == "file_a\nfile_b\n"
    assert s.state == "idle"

    # Second command uses the echo path (prompt now known), so tab-completed
    # text that never appeared in the typed stream is still captured.
    s.feed_input(b"cat fi\t")
    s.feed_output(b"cat file_a")
    s.on_enter(masked=False)
    assert sink.of("cmd")[-1]["text"] == "cat file_a"


def test_masked_enter_emits_no_cmd_and_drops_echo_tail(seg):
    s, sink = seg
    s.feed_output(b"$ ")
    s.feed_input(b"true")
    s.feed_output(b"true")
    s.on_enter(masked=False)                     # learn "$ "
    s.feed_output(b"\r\nPassword: ")
    s.feed_input(b"hunter2")                     # echo off: nothing echoed
    assert s.on_enter(masked=True) is None
    texts = " ".join(r.get("text", "") for r in sink.records)
    assert "hunter2" not in texts
    assert "Password" not in " ".join(r["text"] for r in sink.of("cmd"))


def test_enter_while_command_running_is_journaled_as_output_not_cmd(seg):
    s, sink = seg
    s.feed_output(b"$ ")
    s.feed_input(b"apt install x")
    s.feed_output(b"apt install x")
    s.on_enter(masked=False)
    s.feed_output(b"\r\nContinue? [y/n] ")
    s.feed_input(b"y")
    s.feed_output(b"y")
    assert s.on_enter(masked=False) is None
    assert len(sink.of("cmd")) == 1
    assert any("Continue? [y/n] y" in o["text"] for o in sink.of("output"))
    assert s.state == "output"


# -- OSC 133 -----------------------------------------------------------------------------

def test_osc133_markers_give_exact_boundaries_and_exit_code(seg):
    s, sink = seg
    A, B, C = b"\x1b]133;A\x07", b"\x1b]133;B\x07", b"\x1b]133;C\x07"
    s.feed_output(A + b"prompt$ " + B)
    s.feed_output(b"false --flag")               # echoed command between B and C
    s.feed_output(C + b"\r\nsome output\r\n")
    s.feed_output(b"\x1b]133;D;1\x07" + A + b"prompt$ " + B)
    assert s.mode == MODE_OSC133
    cmds, outs, exits = sink.of("cmd"), sink.of("output"), sink.of("exit")
    assert [c["text"] for c in cmds] == ["false --flag"]
    assert outs == [{"t": "output", "text": "some output\n",
                     "cmd_seq": cmds[0]["seq"], "seq": outs[0]["seq"]}]
    assert exits[0]["code"] == 1 and exits[0]["cmd_seq"] == cmds[0]["seq"]


def test_osc133_marker_split_across_reads(seg):
    s, sink = seg
    s.feed_output(b"\x1b]133;A\x07x$ \x1b]13")
    s.feed_output(b"3;B\x07echo hi\x1b]133;C\x07hi\r\n\x1b]133;D;0\x07")
    assert sink.of("cmd")[0]["text"] == "echo hi"
    assert sink.of("exit")[0]["code"] == 0


# -- altscreen ------------------------------------------------------------------------------

def test_altscreen_output_is_not_journaled_only_summarized(seg):
    s, sink = seg
    s.feed_output(b"$ ")
    s.feed_input(b"vim")
    s.feed_output(b"vim")
    s.on_enter(masked=False)
    s.feed_output(b"\r\n\x1b[?1049h")
    s.feed_output(b"~\r\n~\r\nSECRET BUFFER CONTENTS\r\n")
    s.feed_output(b"\x1b[?1049l")
    s.feed_output(b"$ ")
    _idle(s)
    assert not any("SECRET" in o["text"] for o in sink.of("output"))
    alts = [m for m in sink.of("meta") if m["event"] == "altscreen"]
    assert len(alts) == 1 and "duration_s" in alts[0]["data"]
    assert s.altscreen is False


def test_close_flushes_held_partial_line(seg):
    s, sink = seg
    s.feed_output(b"no newline at end")
    s.close()
    assert sink.of("output")[-1]["text"] == "no newline at end"
