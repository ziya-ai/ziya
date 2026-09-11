"""Secret masking for shadow journals (design doc §7).

Two mechanisms, applied before anything reaches the journal:

1. **Echo-off detection (primary).**  When the child disables ECHO on
   the PTY (sudo, ssh, gpg, passwd all do this), ALL user input until
   echo re-enables is masked.  The pty host polls termios state on the
   PTY master and consults `echo_off` before journaling input.

2. **Prompt-regex (belt and braces).**  For password-style prompts that
   read with echo ON (some installers, REPLs), match the trailing
   output against a regex set; if it looks like a secret prompt, the
   *next* input line is masked.

Masked input is journaled only as a meta record
(`{"event": "mask", "data": {"reason": ...}}`) — the bytes never touch
disk.  Masking covers *input* secrets; displayed file contents are out
of scope (§10 known gaps).
"""
import re
import termios
from typing import List, Optional

# Case-insensitive patterns matched against the tail of recent output.
# Deliberately conservative: false positives cost one un-journaled
# input line; false negatives put a secret on disk.
_PROMPT_PATTERNS: List[re.Pattern] = [
    re.compile(r"[Pp]assword\s*(for [^:]+)?:\s*$"),
    re.compile(r"[Pp]assphrase[^:]*:\s*$"),
    re.compile(r"\b(OTP|MFA|2FA)\b[^:]*:\s*$", re.IGNORECASE),
    re.compile(r"\bPIN\b[^:]*:\s*$"),
    re.compile(r"[Ss]ecret[^:]*:\s*$"),
    re.compile(r"[Aa]uth(entication)? [Cc]ode[^:]*:\s*$"),
    re.compile(r"[Vv]erification [Cc]ode[^:]*:\s*$"),
    re.compile(r"[Tt]oken[^:]*:\s*$"),
]

# How much trailing output to keep for prompt matching.  Prompts are
# short; 512 bytes comfortably covers "Password for user@host:".
_TAIL_KEEP = 512


def echo_off(pty_master_fd: int) -> bool:
    """True if the child is reading a secret: ECHO off *and* ICANON on.

    ECHO alone is not the signal.  Every line-editing shell (zsh ZLE,
    bash readline) clears ECHO at the prompt and paints its own echo, and
    it does so in raw mode (ICANON off).  A password read — sudo, ssh,
    ``read -s``, getpass — clears ECHO but stays canonical (ICANON on),
    because it wants the kernel to hand it a whole line and echo nothing.
    Measured on macOS: zsh/bash at prompt = ECHO off/ICANON off;
    ``read -s`` = ECHO off/ICANON on.

    Raw-mode applications that collect a secret (a TUI password field)
    are not caught here; whatever they choose to echo is on the human's
    screen and is the redactor's job.  Errors (child gone, fd closed
    mid-poll) report True: when unsure, mask.
    """
    try:
        lflag = termios.tcgetattr(pty_master_fd)[3]
        return (not (lflag & termios.ECHO)) and bool(lflag & termios.ICANON)
    except (termios.error, OSError, ValueError):
        return True


class PromptMasker:
    """Tracks recent output and flags secret-style prompts (§7).

    Feed it every output chunk; before journaling an input line, ask
    `pending()`.  If True, the input is masked and the flag clears
    (one prompt masks one input line).
    """

    def __init__(self, patterns: Optional[List[re.Pattern]] = None):
        self._patterns = patterns if patterns is not None else _PROMPT_PATTERNS
        self._tail = ""
        self._pending = False

    def feed_output(self, text: str) -> None:
        """Accumulate output and re-evaluate the prompt match."""
        if not text:
            return
        self._tail = (self._tail + text)[-_TAIL_KEEP:]
        # Strip trailing ANSI/OSC noise and whitespace for matching;
        # prompts often end with cursor/color sequences.
        stripped = _strip_sequences(self._tail).rstrip(" \t")
        self._pending = any(p.search(stripped) for p in self._patterns)

    def pending(self) -> bool:
        """True if the next input line should be masked.  Clears on read."""
        was = self._pending
        self._pending = False
        self._tail = ""
        return was

    def reset(self) -> None:
        """Clear state (e.g. on command boundary)."""
        self._tail = ""
        self._pending = False


_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\\\)")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f]")


def _strip_sequences(text: str) -> str:
    """Remove ANSI CSI/OSC sequences and control bytes for matching."""
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    return _CTRL.sub("", text)


class MaskGate:
    """Combines both mechanisms into the single decision the pty host
    consults before journaling user input.
    """

    def __init__(self, pty_master_fd: int):
        self._fd = pty_master_fd
        self.prompts = PromptMasker()

    def feed_output(self, text: str) -> None:
        self.prompts.feed_output(text)

    def check(self) -> Optional[str]:
        """Reason string if the next input must be masked, else None.

        Echo-off is checked first (primary, §7); the prompt flag is
        consumed either way so a matched prompt doesn't linger past
        the input it applied to.
        """
        prompt_hit = self.prompts.pending()
        if echo_off(self._fd):
            return "echo-off"
        if prompt_hit:
            return "prompt-match"
        return None
