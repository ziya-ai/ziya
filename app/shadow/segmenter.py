"""Output-stream segmentation ladder (design doc §4.1).

Turns the raw PTY byte stream into journal records.  Three modes, best
available per *stretch* (a session can upgrade/downgrade mid-life, e.g.
an ssh hop from an instrumented local shell to a raw remote bash):

1. osc133           — OSC 133 A/B/C/D markers: exact cmd/output/exit
                      boundaries including exit codes.
2. prompt-heuristic — prompt-reappearance + local echo matching: good
                      boundaries, no exit codes.  The fleet-scale
                      workhorse.
3. raw              — timestamped output chunks, cmd_seq: null.

Mode transitions are journaled as meta records and reflected into the
registry entry by the pty host (the segmenter just reports them via
the ``on_mode_change`` callback).

Altscreen (§4): DECSET 1049 enter/exit is detected here; while inside,
output is NOT journaled — a single meta record summarizes the stretch.

The segmenter never touches the disk; it calls a ``sink`` that exposes
``cmd(text) -> seq``, ``output(text, cmd_seq)``, ``exit(cmd_seq, code)``
and ``meta(event, data)`` — i.e. a ``JournalWriter``.

Journaled text is cleaned for model consumption: ANSI CSI/OSC sequences
are stripped, ``\\r\\n`` collapses to ``\\n`` and a bare ``\\r`` keeps
only what was written after it (progress-bar redraws).  The user's own
terminal always receives the untouched bytes — cleaning applies only to
what is recorded.
"""
import codecs
import threading
import re
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

# OSC 133 markers: ESC ] 133 ; <letter> [; params] (BEL | ESC \)
_OSC133 = re.compile(r"\x1b\]133;([A-D])((?:;[^\x07\x1b]*)?)(?:\x07|\x1b\\)")
# Alternate screen buffer enter/exit (DECSET/DECRST 1049, with legacy 47/1047)
_ALT_ENTER = re.compile(r"\x1b\[\?(?:1049|1047|47)h")
_ALT_EXIT = re.compile(r"\x1b\[\?(?:1049|1047|47)l")
# Any CSI / OSC / two-byte escape, for prompt fingerprinting and cleanup
_ANSI_ANY = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# Control bytes other than \t \n \r \b
_CTRL = re.compile(r"[\x00-\x07\x0b-\x0c\x0e-\x1f\x7f]")
# Terminal-side line editing keys we recognise in the typed stream
_INPUT_ESC = re.compile(r"\x1b(?:\[[0-9;]*[A-Za-z~]|O[A-Za-z]|.)")

MODE_RAW = "raw"
MODE_PROMPT = "prompt-heuristic"
MODE_OSC133 = "osc133"

MAX_BUFFER = 256 * 1024  # flush guard for pathological streams
IDLE_FLUSH_S = 0.5       # flush pending output after this much quiet
# An escape sequence split across two reads is carried to the next feed;
# anything longer than this is not a sequence we care about.
_MAX_ESC_CARRY = 64
# Minimum length of a learned prompt suffix before we trust reappearance
# matching.  Prompts almost always end in "$ ", "# ", "> " or "% ".
_PROMPT_SUFFIX_LEN = 2


def strip_ansi(text: str) -> str:
    """Remove CSI/OSC sequences and stray control bytes (keeps \\t\\n\\r\\b)."""
    return _CTRL.sub("", _ANSI_ANY.sub("", text))


def apply_backspaces(text: str) -> str:
    """Apply \\b (and the ``\\b \\b`` erase idiom) to a single line."""
    out: List[str] = []
    for ch in text:
        if ch == "\b":
            if out:
                out.pop()
        else:
            out.append(ch)
    return "".join(out)


def clean_output(text: str) -> str:
    """Clean a stretch of terminal output for the journal.

    Strips escape sequences, normalises line endings, and resolves
    carriage-return redraws so a progress bar records its final state
    rather than every frame.
    """
    text = strip_ansi(text)
    text = text.replace("\r\n", "\n")
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            # Keep the last redraw of the line; an empty final segment
            # (trailing bare "\r") does not count as a redraw.
            parts = line.split("\r")
            line = parts[-1] or next((p for p in reversed(parts) if p), "")
        lines.append(apply_backspaces(line))
    return "\n".join(lines)


def _split_incomplete_escape(text: str) -> Tuple[str, str]:
    """Hold back a trailing escape sequence that has not terminated yet.

    Returns (complete, carry).  A CSI (``ESC [``) terminates at the
    first byte in ``@``..``~``; an OSC (``ESC ]``) at BEL or ``ESC \\``.
    A bare trailing ESC is carried too.
    """
    idx = text.rfind("\x1b")
    if idx < 0:
        return text, ""
    tail = text[idx:]
    if len(tail) > _MAX_ESC_CARRY:
        return text, ""
    if len(tail) == 1:
        return text[:idx], tail
    kind = tail[1]
    if kind == "[":
        if re.search(r"[@-~]", tail[2:]):
            return text, ""
        return text[:idx], tail
    if kind == "]":
        if "\x07" in tail or "\x1b\\" in tail[1:]:
            return text, ""
        return text[:idx], tail
    return text, ""


def _locked(fn):
    """Run a Segmenter method under its reentrant lock."""
    import functools
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrapper


class Segmenter:
    """Feeds on PTY output bytes + user input bytes; emits journal records.

    Hooks the pty host calls:

      feed_output(data)   bytes read from the PTY master
      feed_input(data)    bytes the human typed (already forwarded)
      on_enter(masked)    the human pressed Enter; ``masked`` means the
                          masking gate vetoed journaling this line
      tick(now=None)      idle timer: flush quiescent output
      close()             flush everything at session end

    States: ``idle`` (at a prompt), ``prompt`` / ``input`` (osc133 A/B
    seen), ``output`` (a command is running).
    """

    def __init__(self, sink, on_mode_change: Optional[Callable[[str, str], None]] = None):
        self.sink = sink
        self.on_mode_change = on_mode_change
        self.mode = MODE_RAW
        self.state = "idle"
        self.cmd_seq: Optional[int] = None
        self.altscreen = False
        self._alt_entered_at = 0.0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._carry = ""            # incomplete trailing escape sequence
        self._pending = ""          # raw output not yet journaled
        self._last_output_at = time.monotonic()
        self._typed: List[str] = [] # line-edited local echo of typed input
        # Completed typed lines waiting for their on_enter(); one input read
        # may carry several terminators (paste) and each must stay distinct.
        self._typed_lines: Deque[str] = deque()
        # After a masked Enter, drop the child's echo of that line: it may
        # arrive after on_enter ran (paste, fast typing), so dropping the
        # tail line at Enter is not sufficient.
        self._suppress_until_newline = False
        # In osc133 mode the shell echoes the command between markers B and
        # C; when that line was masked, record no cmd at C.
        self._masked_cmd_pending = False
        self._prompt_suffix = ""    # learned prompt tail for reappearance
        self._prompt_full = ""      # last full prompt line seen
        self._osc_cmd_echo = ""     # text between OSC133 B and C
        # The newline that terminates the echoed command line belongs to
        # the command, not its output; strip it once after each cmd.
        self._eat_leading_newline = False
        # The PTY read loop (feed_output/tick) and an input source on
        # another thread (headless handle_input, Phase-3 send_line) both
        # mutate _pending/state; serialize them so a command record can
        # never be emitted after the output it produced.
        self._lock = threading.RLock()

    # -- public feed points -------------------------------------------------

    @_locked
    def feed_output(self, data: bytes) -> None:
        text = self._carry + self._decoder.decode(data)
        text, self._carry = _split_incomplete_escape(text)
        if not text:
            return
        self._last_output_at = time.monotonic()
        self._scan(text)
        if len(self._pending) > MAX_BUFFER:
            self._flush_output(keep_tail=self.state != "output")

    @_locked
    def feed_input(self, data: bytes) -> None:
        """Track the human's local line editing (best effort).

        Used only as the fallback command text when the echo tail cannot
        be attributed; the echo path is preferred because tab completion
        and history recall never appear in the typed stream.
        """
        text = data.decode("utf-8", errors="replace")
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                m = _INPUT_ESC.match(text, i)
                i = m.end() if m else i + 1
                continue
            if ch in ("\x7f", "\b"):
                if self._typed:
                    self._typed.pop()
            elif ch in ("\x03", "\x15"):      # ^C, ^U clear the line
                self._typed.clear()
            elif ch in ("\r", "\n"):
                if ch == "\n" and i > 0 and text[i - 1] == "\r":
                    pass                        # \r\n is one terminator
                else:
                    self._typed_lines.append("".join(self._typed))
                    self._typed.clear()
            elif ch >= " " or ch == "\t":
                self._typed.append(ch)
            i += 1

    @_locked
    def on_enter(self, masked: bool, reason: Optional[str] = None) -> Optional[int]:
        """The human pressed Enter.  Returns the new cmd_seq if a command
        record was emitted, else None.

        ``masked`` means the masking gate vetoed journaling this line;
        ``reason`` is the gate's reason.  Masking is handled first, in every
        mode: the typed buffer is discarded, the echo tail already in the
        buffer is dropped, and — unless echo was off (nothing was echoed)
        — output is suppressed through the next newline so an echo that
        arrives *after* this call cannot land in the journal.

        In osc133 mode boundaries otherwise come from the markers, so Enter
        only consumes the typed line.  In the other modes this is the moment
        we decide whether a command started.
        """
        if self._typed_lines:
            typed = self._typed_lines.popleft().strip()
        else:
            typed = "".join(self._typed).strip()
            self._typed.clear()
        if masked:
            self._drop_tail_line()
            self._osc_cmd_echo = ""
            if self.mode == MODE_OSC133 and self.state == "input":
                self._masked_cmd_pending = True
            if reason != "echo-off":
                self._suppress_until_newline = True
            return None
        if self.mode == MODE_OSC133 or self.altscreen:
            return None
        return self._heuristic_enter(typed)

    @_locked
    def tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if not self._pending or now - self._last_output_at < IDLE_FLUSH_S:
            return
        if self.state == "output" and self.mode != MODE_OSC133:
            # A quiescent stretch whose last line is the prompt means the
            # command finished (heuristic modes have no exit marker).
            tail = self._tail_line()
            if self._looks_like_prompt(tail):
                # Flush the command's output; keep the prompt line held so
                # the next Enter can split "prompt + echoed command".
                self._flush_output(keep_tail=True)
                self._finish_command()
                self._prompt_full = tail
                return
        # Otherwise flush complete lines, holding the unterminated line
        # (it may be a prompt, or a partial line still being written).
        self._flush_output(keep_tail=True)

    @_locked
    def close(self) -> None:
        if self._carry:
            self._pending += self._carry
            self._carry = ""
        self._flush_output(keep_tail=False)
        if self.altscreen:
            self._exit_altscreen()

    # -- scanning -------------------------------------------------------------

    def _scan(self, text: str) -> None:
        """Walk ``text`` splitting on OSC133 / altscreen markers."""
        pos = 0
        while pos < len(text):
            m_osc = _OSC133.search(text, pos)
            m_in = _ALT_ENTER.search(text, pos)
            m_out = _ALT_EXIT.search(text, pos)
            cands = [m for m in (m_osc, m_in, m_out) if m]
            if not cands:
                self._consume(text[pos:])
                return
            m = min(cands, key=lambda mm: mm.start())
            self._consume(text[pos:m.start()])
            if m is m_osc:
                self._osc133(m.group(1), m.group(2))
            elif m is m_in:
                self._enter_altscreen()
            else:
                self._exit_altscreen()
            pos = m.end()

    def _consume(self, text: str) -> None:
        if not text or self.altscreen:
            return
        if self._suppress_until_newline:
            nl = text.find("\n")
            if nl < 0:
                return
            self._suppress_until_newline = False
            text = text[nl + 1:]
            if not text:
                return
        if self.state == "input":
            self._osc_cmd_echo += text
            return
        if self.state == "prompt":
            return  # the prompt itself (between OSC133 A and B) is not output
        if self._eat_leading_newline:
            stripped = text.lstrip("\r\n")
            if stripped:
                self._eat_leading_newline = False
            text = stripped
            if not text:
                return
        self._pending += text

    # -- OSC 133 ----------------------------------------------------------------

    def _osc133(self, letter: str, params: str) -> None:
        if self.mode != MODE_OSC133:
            self._set_mode(MODE_OSC133)
        if letter == "A":                     # prompt start
            if self.state == "output":
                self._flush_output(keep_tail=False)
                self._finish_command()
            self._flush_output(keep_tail=False)
            self.state = "prompt"
        elif letter == "B":                   # prompt end, input begins
            self._pending = ""
            self._osc_cmd_echo = ""
            self.state = "input"
        elif letter == "C":                   # command starts executing
            echo = clean_output(self._osc_cmd_echo).strip()
            self._osc_cmd_echo = ""
            if self._masked_cmd_pending:
                self._masked_cmd_pending = False
                cmd = ""
            else:
                cmd = echo or "".join(self._typed).strip()
            self._typed.clear()
            self._typed_lines.clear()
            self._pending = ""
            self.cmd_seq = self.sink.cmd(cmd) if cmd else None
            self.state = "output"
            self._eat_leading_newline = True
        elif letter == "D":                   # command finished
            self._flush_output(keep_tail=False)
            code: Optional[int] = None
            if params.startswith(";"):
                try:
                    code = int(params[1:].split(";")[0])
                except ValueError:
                    code = None
            if self.cmd_seq is not None and code is not None:
                self.sink.exit(self.cmd_seq, code)
            self.cmd_seq = None
            self.state = "idle"

    # -- prompt heuristic ------------------------------------------------------

    def _heuristic_enter(self, typed: str) -> Optional[int]:
        tail = self._tail_line()
        if self.state == "output":
            if not self._looks_like_prompt(tail):
                # Input to a running program (a y/n answer, a REPL line):
                # it is part of the command's output stream, not a new
                # command.  Journal the echoed line as output.
                self._pending += "\n"
                self._flush_output(keep_tail=False)
                return None
            # The prompt came back but tick() had not yet noticed.
            self._flush_output(keep_tail=True)
            self._finish_command()

        # At a prompt.  Derive the command from the echo tail when the
        # prompt is known; otherwise from what was typed, and learn the
        # prompt as whatever preceded it on the line.
        cmd = ""
        if tail:
            if self._prompt_suffix and self._prompt_suffix in tail:
                cmd = tail.rsplit(self._prompt_suffix, 1)[-1].strip()
                self._prompt_full = tail[: len(tail) - len(cmd)].rstrip()
            elif typed and tail.rstrip().endswith(typed):
                prompt = tail[: len(tail.rstrip()) - len(typed)]
                self._learn_prompt(prompt)
                cmd = typed
            elif not typed:
                # Bare Enter at an unknown prompt: the tail IS the prompt.
                self._learn_prompt(tail)
        if not cmd:
            cmd = typed
        self._drop_tail_line()
        self._flush_output(keep_tail=False)
        if not cmd:
            self.state = "idle"
            return None
        self.cmd_seq = self.sink.cmd(cmd)
        self.state = "output"
        self._eat_leading_newline = True
        return self.cmd_seq

    def _learn_prompt(self, prompt: str) -> None:
        prompt = prompt.rstrip("\n")
        stripped = prompt.rstrip()
        if not stripped:
            return
        # Suffix = last non-space run plus its trailing space, e.g. "$ ".
        suffix = stripped[-1] + (" " if prompt.endswith(" ") else "")
        if len(suffix) < _PROMPT_SUFFIX_LEN or stripped[-1].isalnum():
            # "$" with no trailing space is too weak; keep waiting.
            return
        self._prompt_suffix = suffix
        self._prompt_full = prompt
        if self.mode == MODE_RAW:
            self._set_mode(MODE_PROMPT)

    def _looks_like_prompt(self, line: str) -> bool:
        if not self._prompt_suffix or not line:
            return False
        if line.rstrip() == self._prompt_full.rstrip():
            return True
        return line.endswith(self._prompt_suffix)

    # -- pending output helpers -------------------------------------------------

    def _tail_line(self) -> str:
        return clean_output(self._pending.rsplit("\n", 1)[-1])

    def _drop_tail_line(self) -> None:
        idx = self._pending.rfind("\n")
        self._pending = self._pending[: idx + 1] if idx >= 0 else ""

    def _flush_output(self, keep_tail: bool) -> None:
        if not self._pending:
            return
        if keep_tail:
            idx = self._pending.rfind("\n")
            if idx < 0:
                return
            chunk, self._pending = self._pending[: idx + 1], self._pending[idx + 1:]
        else:
            chunk, self._pending = self._pending, ""
        text = clean_output(chunk)
        if text.strip():
            self.sink.output(text, self.cmd_seq)

    def _finish_command(self) -> None:
        self.cmd_seq = None
        self.state = "idle"

    # -- altscreen / mode ---------------------------------------------------------

    def _enter_altscreen(self) -> None:
        if self.altscreen:
            return
        self._flush_output(keep_tail=False)
        self.altscreen = True
        self._alt_entered_at = time.monotonic()

    def _exit_altscreen(self) -> None:
        if not self.altscreen:
            return
        self.altscreen = False
        dur = round(time.monotonic() - self._alt_entered_at, 1)
        self.sink.meta("altscreen", {"duration_s": dur, "cmd_seq": self.cmd_seq})

    def _set_mode(self, mode: str) -> None:
        old, self.mode = self.mode, mode
        if old != mode:
            self.sink.meta("segmentation", {"from": old, "to": mode})
            if self.on_mode_change:
                self.on_mode_change(old, mode)
