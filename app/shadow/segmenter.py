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
import re
import secrets
import threading
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

# OSC 133 markers: ESC ] 133 ; <letter> [; params] (BEL | ESC \)
_OSC133 = re.compile(r"\x1b\]133;([A-D])((?:;[^\x07\x1b]*)?)(?:\x07|\x1b\\)")
# Alternate screen buffer enter/exit (DECSET/DECRST 1049, with legacy 47/1047)
_ALT_ENTER = re.compile(r"\x1b\[\?(?:1049|1047|47)h")
_ALT_EXIT = re.compile(r"\x1b\[\?(?:1049|1047|47)l")
# Exit report for a line shadow_send typed (see Segmenter.with_exit_report):
# ESC ] 6973 ; <session token> ; <cmd_seq> ; <status> BEL.  A private OSC
# number, so terminals ignore it; the token keeps another session's (or a
# replayed file's) report from ending this session's command.
_EXIT_REPORT = re.compile(r"\x1b\]6973;([0-9a-f]+);(\d+);(\d+)(?:\x07|\x1b\\)")
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
# A held first line (see Segmenter._echo_expect) is released unchecked once
# it grows past this many characters beyond the command without a newline:
# an echo is the command plus escape sequences (syntax highlighting), so
# anything much longer is output.
_ECHO_HOLD_SLACK = 4096
# Shell words after which nothing may be appended: the line opens a compound
# command the human or model is still writing.
_OPEN_WORDS = {"do", "then", "else", "elif", "in", "if", "while", "until",
               "for", "case", "select", "function", "{", "(", "!", "time"}


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


def _is_echo(line: str, cmd: str) -> bool:
    """Whether ``line`` (one raw output line) is the terminal's echo of ``cmd``.

    An exact match after cleaning is an echo.  A line the terminal wrapped
    at the margin carries a bare CR, and cleaning keeps only its last row,
    so there a suffix of the command (or the command after a redrawn
    prompt) counts too.  Without a CR a suffix is ordinary output: ``seq 9
    | tail -1`` printing ``1`` is not an echo.
    """
    body = strip_ansi(line).rstrip("\r\n")
    shown = clean_output(body).strip()
    cmd = cmd.strip()
    if not shown or not cmd:
        return False
    if shown == cmd:
        return True
    if "\r" in body:
        return cmd.endswith(shown) or shown.endswith(cmd)
    return False


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


def _report_separator(line: str) -> Optional[str]:
    """What to put between ``line`` and the exit report, or None to send the
    line unchanged.

    The report is appended as another command, so it is only safe when
    ``line`` is one complete command: no unterminated quote, backslash,
    parenthesis or here-document, no trailing comment (it would swallow the
    report), and no trailing operator or compound-opening keyword that
    leaves the command unfinished.  A trailing ``&`` backgrounds the line,
    so its status is not the command's anyway.  When in doubt, None: the
    command then ends with an unknown code, as before.
    """
    if not line.strip() or "\n" in line or "\r" in line:
        return None
    quote = ""            # "'", '"', '`' while inside one
    depth = 0             # unquoted ( and $( nesting
    prev = " "
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = ""
        elif quote in ('"', "`"):
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch == "\\":
            if i == n - 1:
                return None   # line continuation
            i += 1
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "#" and prev in " \t;&|()":
            return None       # comment: the report would be inside it
        elif ch == "<" and line.startswith("<<", i):
            return None       # here-document (or here-string): body follows
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        prev = ch
        i += 1
    if quote or depth != 0:
        return None
    body = line.rstrip()
    if body.endswith(";;"):
        return None
    if body.endswith(("&", "|")):
        return None           # &&, ||, |, & (background)
    words = body.replace(";", " ; ").split()
    if words and words[-1] in _OPEN_WORDS:
        return None
    # The first word opening a compound the line does not close.
    if words and words[0] in ("for", "while", "until", "select") and "done" not in words:
        return None
    if words and words[0] == "if" and "fi" not in words:
        return None
    if words and words[0] == "case" and "esac" not in words:
        return None
    return " " if body.endswith(";") else "; "


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
        # A line delivered in one write (shadow_send, a paste) reaches
        # on_enter before the child has echoed it, so over ssh the echo
        # arrives a moment later as the command's first output.  When the
        # command text did not come from an echo already on screen,
        # _echo_expect holds it and the first output line is buffered in
        # _echo_buf until its newline, then dropped if it is that echo.
        self._echo_expect: Optional[str] = None
        self._echo_buf = ""
        # osc133 mode was left because the markers stopped framing input
        # (an un-instrumented shell, e.g. across ssh).  The next marker
        # closes the command the heuristic opened meanwhile with an unknown
        # code; D's code belongs to the instrumented shell's command.
        self._osc_lapsed = False
        # The line taken at Enter in osc133 mode: the cmd text at marker C
        # when nothing was echoed between B and C.
        self._osc_typed = ""
        # Session token in exit reports (with_exit_report / _EXIT_REPORT).
        self.exit_token = secrets.token_hex(4)
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

        In osc133 mode an Enter while a command runs, on a line that has
        something before the typed text (a prompt), means the markers have
        stopped: the foreground program is an un-instrumented shell.  The
        stretch drops to the heuristic until the next marker.
        """
        self._release_echo_hold()
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
        if self.altscreen:
            return None
        if self.mode == MODE_OSC133:
            if self.state == "output" and self._prompted_line(typed):
                return self._lapse_osc133(typed)
            self._osc_typed = typed
            return None
        return self._heuristic_enter(typed)

    @_locked
    def with_exit_report(self, line: str, cmd_seq: Optional[int]) -> str:
        """The text to type for ``line`` so the host's own shell reports its
        exit status (a line written whole by send_line, after on_enter).

        Only in a prompt-heuristic stretch, where nothing else knows the
        code, and only for a line that just opened command ``cmd_seq``.
        The suffix is plain POSIX shell: it saves ``$?``, prints the report
        escape, and restores ``$?`` with a subshell so the user's next
        command (and their prompt) still sees it.  Nothing is installed on
        the host.  The cmd record stays ``line``; the echo of the longer
        text is what the echo hold now expects.
        """
        if (cmd_seq is None or cmd_seq != self.cmd_seq or self.mode != MODE_PROMPT
                or self.state != "output"):
            return line
        sep = _report_separator(line)
        if sep is None:
            return line
        typed = (f"{line.rstrip()}{sep}_zx=$?; printf '\\033]6973;{self.exit_token};"
                 f"{cmd_seq};%s\\007' $_zx; (exit $_zx)")
        if self._echo_expect is not None:
            self._echo_expect = typed
        return typed

    @_locked
    def tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if now - self._last_output_at < IDLE_FLUSH_S:
            return
        if self._echo_buf:
            # Quiet with the held first line still unterminated: not an
            # echo in progress (a bare returning prompt, a y/n question).
            self._release_echo_hold()
        if not self._pending:
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
        self._release_echo_hold()
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
            m_rep = _EXIT_REPORT.search(text, pos)
            cands = [m for m in (m_osc, m_in, m_out, m_rep) if m]
            if not cands:
                self._consume(text[pos:])
                return
            m = min(cands, key=lambda mm: mm.start())
            self._consume(text[pos:m.start()])
            if m is m_osc:
                self._osc133(m.group(1), m.group(2))
            elif m is m_rep:
                self._exit_report(m.group(1), int(m.group(2)), int(m.group(3)))
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
        if self._echo_expect is not None:
            text = self._take_echo(text)
            if not text:
                return
        self._pending += text

    # -- delayed echo ---------------------------------------------------------

    def _take_echo(self, text: str) -> str:
        """Buffer the first output line after a command whose echo is owed;
        return what should be journaled now (the line unless it is the
        echo, plus anything after it)."""
        self._echo_buf += text
        nl = self._echo_buf.find("\n")
        if nl < 0:
            if len(self._echo_buf) > len(self._echo_expect or "") * 4 + _ECHO_HOLD_SLACK:
                self._release_echo_hold()
            return ""
        line, rest = self._echo_buf[: nl + 1], self._echo_buf[nl + 1:]
        expect = self._echo_expect or ""
        self._echo_buf, self._echo_expect = "", None
        return rest if _is_echo(line, expect) else line + rest

    def _release_echo_hold(self) -> None:
        """Stop waiting for an echo; anything held becomes ordinary output."""
        held, self._echo_buf, self._echo_expect = self._echo_buf, "", None
        if held:
            self._pending += held

    # -- OSC 133 ----------------------------------------------------------------

    def _osc133(self, letter: str, params: str) -> None:
        self._release_echo_hold()
        if self._osc_lapsed:
            self._osc_lapsed = False
            self._flush_output(keep_tail=False)
            self._finish_command()
        if self.mode != MODE_OSC133:
            self._set_mode(MODE_OSC133)
        if letter == "A":                     # prompt start
            self._osc_typed = ""
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
                cmd = echo or self._osc_typed or "".join(self._typed).strip()
            self._osc_typed = ""
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

    def _prompted_line(self, typed: str) -> bool:
        """Something other than the typed text is on the line (a prompt).
        Type-ahead echoed under a running command's output has nothing
        before it and stays input to that command."""
        t = self._tail_line().rstrip()
        if typed and t.endswith(typed):
            t = t[: len(t) - len(typed)]
        return bool(t.strip())

    def _lapse_osc133(self, typed: str) -> Optional[int]:
        """The markers stopped framing input: close the running command
        (unknown code) and segment this stretch by the prompt heuristic
        until the next marker."""
        self._flush_output(keep_tail=True)
        self._finish_command()
        self._osc_lapsed = True
        self._set_mode(MODE_PROMPT if self._prompt_suffix else MODE_RAW)
        return self._heuristic_enter(typed)

    def _heuristic_enter(self, typed: str) -> Optional[int]:
        tail = self._tail_line()
        if self.state == "output":
            if not self._prompt_returned(tail, typed):
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
        from_echo = False      # cmd was read off the screen, not just typed
        if tail:
            if self._prompt_suffix and self._prompt_suffix in tail:
                cmd = tail.rsplit(self._prompt_suffix, 1)[-1].strip()
                self._prompt_full = tail[: len(tail) - len(cmd)].rstrip()
                from_echo = bool(cmd)
            elif typed and tail.rstrip().endswith(typed):
                prompt = tail[: len(tail.rstrip()) - len(typed)]
                self._learn_prompt(prompt)
                cmd = typed
                from_echo = True
            elif not typed:
                # Bare Enter at an unknown prompt: the tail IS the prompt.
                self._learn_prompt(tail)
        if not cmd:
            cmd = typed
        self._drop_tail_line()
        self._flush_output(keep_tail=False)
        if not cmd:
            self.cmd_seq = None
            self.state = "idle"
            return None
        self.cmd_seq = self.sink.cmd(cmd)
        self._eat_leading_newline = True
        # The line was written whole (send_line, a paste): its echo is
        # still to come and must not open the command's output.
        self._echo_expect = None if from_echo else cmd
        if not self._prompt_suffix:
            # raw: no learned prompt, so nothing will ever say this command
            # ended.  Group its output under it, but stay "idle" so the next
            # Enter opens a new group instead of being taken for input to
            # this one, and return None: a cmd_seq nothing can finish would
            # send shadow_wait(until=['exit']) to its timeout.
            return None
        self.state = "output"
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

    def _prompt_returned(self, tail: str, typed: str) -> bool:
        """The prompt is back -- possibly with the next command already
        echoed after it, when that was typed or pasted inside the idle
        window before tick() saw the bare prompt."""
        if self._looks_like_prompt(tail):
            return True
        t = tail.rstrip()
        return bool(typed) and t.endswith(typed) and \
            self._looks_like_prompt(t[: len(t) - len(typed)])

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
        """A command ended without an OSC 133 D: the heuristic saw the
        prompt return, or osc133 skipped D before the next A.  Journal an
        exit with an unknown code so a ``shadow_wait(cmd_seq=...)`` wakes
        instead of sleeping to its timeout.  D clears cmd_seq before this
        can run, so a real exit is never duplicated."""
        if self.cmd_seq is not None:
            self.sink.exit(self.cmd_seq, None)
        self.cmd_seq = None
        self.state = "idle"

    def _exit_report(self, token: str, cmd_seq: int, code: int) -> None:
        """A report from with_exit_report's suffix: end the command it names
        with its real code.  Anything else (another session's token, a
        command already ended, a stretch that has switched to osc133) is
        dropped, and the heuristic ends the command as before."""
        if (token != self.exit_token or cmd_seq != self.cmd_seq
                or self.state != "output" or self.mode == MODE_OSC133):
            return
        self._release_echo_hold()
        self._flush_output(keep_tail=False)
        self.sink.exit(cmd_seq, code)
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
