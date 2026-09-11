"""Title rewrite (design doc §8): the shadowed terminal's window title always
carries the shadow prefix; the child's own title survives as a parenthetical.
Display path only — the journal never sees the rewrite.

The child sets its window title with an OSC sequence::

    ESC ] 0 ; <text> BEL          (title + icon)
    ESC ] 2 ; <text> ST           (title only)   ST = ESC \\

``TitleRewriter.filter`` sits on the *display* byte stream.  It replaces the
text of any title-setting OSC (ps 0 or 2) with ``<prefix> (<child title>)``,
always re-emitted as an ``OSC 0 ... BEL`` sequence, and passes every other
byte — icon-name OSC 1, OSC 133 prompt marks, CSI, plain text — through
untouched.  Sequences that span reads are held until they terminate; a
sequence that never terminates is released raw once it passes ``MAX_CARRY``
so the stream can never stall.
"""
import re
from typing import Optional

# Safety valve: an OSC we are buffering is released raw once it grows past
# this many bytes without a terminator, so a child that opens ``ESC ]`` and
# never closes it cannot stall the display stream.  Comfortably larger than a
# real title (capped at 200 chars) plus its framing.
MAX_CARRY = 1024

# Child titles are truncated to this many characters after sanitisation.
_MAX_TITLE = 200

_ESC = 0x1B
_BEL = 0x07
_BACKSLASH = 0x5C

# Sequences and characters stripped from prefix/title text before it is put
# back on the wire.  Order matters: multi-byte escape sequences are removed
# before bare control bytes, so the leading ESC of a CSI/OSC is consumed as
# part of the sequence rather than leaving its tail ("[6n") visible.
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC_RE = re.compile(r"\x1b.")
_C0_RE = re.compile(r"[\x00-\x1f\x7f]")
# Bidi/zero-width format controls: title-spoofing and homograph vectors.
_FMT_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")

_HOLD = object()  # _consume_osc sentinel: need more bytes to decide


def _sanitize(text: str) -> str:
    """Strip escape sequences, control bytes and bidi format controls."""
    text = _CSI_RE.sub("", text)
    text = _OSC_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    text = _C0_RE.sub("", text)
    text = _FMT_RE.sub("", text)
    return text


class TitleRewriter:
    """Rewrites window-title OSC sequences on the display stream (§8)."""

    def __init__(self, prefix: str):
        self.enabled = True
        self.child_title: Optional[str] = None
        self._carry = b""
        self._in_osc = False
        self.set_prefix(prefix)

    # -- public API ----------------------------------------------------------

    def set_prefix(self, prefix: str) -> None:
        """Set the shadow prefix; sanitised so model-supplied text can never
        smuggle an escape sequence back onto the terminal."""
        self._prefix = _sanitize(prefix)
        self._prefix_bytes = self._prefix.encode("utf-8")

    def emit(self) -> bytes:
        """The current title sequence (prefix + child parenthetical), or b""
        when disabled.  Used to (re)assert the title on attach/relabel."""
        if not self.enabled:
            return b""
        return self._render()

    def filter(self, data: bytes) -> bytes:
        """Rewrite title OSCs in ``data``; pass everything else through.

        When disabled this is a pure pass-through with no buffering, so a
        partial sequence at the tail is not swallowed."""
        if not self.enabled:
            return data
        out = bytearray()
        self._carry += data
        while self._carry:
            if not self._in_osc:
                idx = self._carry.find(b"\x1b")
                if idx == -1:
                    out += self._carry
                    self._carry = b""
                    break
                out += self._carry[:idx]
                self._carry = self._carry[idx:]
                if len(self._carry) < 2:
                    break  # lone ESC: hold, decide next read
                if self._carry[1:2] != b"]":
                    # Not an OSC (CSI or other escape): emit the ESC and keep
                    # scanning; we never rewrite anything but OSC.
                    out += self._carry[:1]
                    self._carry = self._carry[1:]
                    continue
                self._in_osc = True
            res = self._consume_osc()
            if res is _HOLD:
                break
            out += res
        return bytes(out)

    # -- internals -----------------------------------------------------------

    def _consume_osc(self) -> object:
        """Resolve the OSC at the head of ``self._carry`` (starts ``ESC ]``).

        Returns the bytes to emit and advances ``self._carry`` past the
        sequence, or ``_HOLD`` if more input is needed."""
        buf = self._carry
        n = len(buf)
        j = 2  # past "ESC ]"
        while j < n:
            b = buf[j]
            if b == _BEL:
                seq, self._carry = buf[: j + 1], buf[j + 1:]
                self._in_osc = False
                return self._finish_osc(seq)
            if b == _ESC:
                if j + 1 >= n:
                    return _HOLD  # could be ST (ESC \\); wait for the next byte
                if buf[j + 1] == _BACKSLASH:
                    seq, self._carry = buf[: j + 2], buf[j + 2:]
                    self._in_osc = False
                    return self._finish_osc(seq)
                # A non-ST ESC aborts the OSC (xterm behaviour): release the
                # accumulated bytes raw and reprocess the ESC as normal input.
                self._in_osc = False
                emit, self._carry = buf[:j], buf[j:]
                return bytes(emit)
            j += 1
        if len(buf) > MAX_CARRY:
            # Never terminated: release raw so the stream cannot stall.
            self._in_osc = False
            self._carry = b""
            return bytes(buf)
        return _HOLD

    def _finish_osc(self, seq: bytes) -> bytes:
        """Given a complete OSC (incl. terminator), rewrite it if it is a
        title sequence (ps 0 or 2), else return it unchanged."""
        body = seq[2:]
        if body.endswith(b"\x07"):
            body = body[:-1]
        elif body.endswith(b"\x1b\\"):
            body = body[:-2]
        sep = body.find(b";")
        if sep == -1:
            return seq  # malformed / no parameter: pass through
        ps = body[:sep]
        if ps not in (b"0", b"2"):
            return seq  # icon name (1), prompt marks (133), etc.: untouched
        title = _sanitize(body[sep + 1:].decode("utf-8", "replace"))[:_MAX_TITLE]
        self.child_title = title
        return self._render()

    def _render(self) -> bytes:
        body = self._prefix_bytes
        if self.child_title:
            body = body + b" (" + self.child_title.encode("utf-8") + b")"
        return b"\x1b]0;" + body + b"\x07"
