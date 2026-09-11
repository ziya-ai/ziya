"""Shadow PTY host (design doc §2, §6.2, §8).

Wraps a command (default ``$SHELL``) in a pseudo-terminal, passes bytes
through to the human's terminal untouched, and — on the local side of
the file descriptor — journals a segmented, secret-masked record of the
session and serves it over a Unix socket to other ziya processes.

Split per §6.2 from the start:

* ``ShadowCore`` — PTY management, segmenter, masking gate, journal,
  registry entry, socket server.  Has no dependency on a terminal, so
  it can run headless (agent-spawned sessions, tests).
* ``InteractiveFrontend`` — raw-mode passthrough to the human's
  terminal, SIGWINCH propagation, the overlay renderer, the ``C-x C-z``
  menu and the ask composer.  Present only in interactive mode.

Nothing is ever typed into the child invisibly: the only bytes the host
writes to the PTY master are the human's own keystrokes and, at the
human's explicit menu request, the shell-instrumentation snippet (§4.1),
which appears on screen exactly as if typed.
"""
import atexit
import errno
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import threading
import time
import tty
from typing import Dict, List, Optional

from app.shadow import registry
from app.shadow.journal import JournalWriter
from app.shadow.masking import MaskGate
from app.shadow.redaction import redact_argv, safe_terminal_text
from app.shadow.segmenter import Segmenter
from app.shadow.sock_server import ShadowSocketServer
from app.shadow.title import TitleRewriter

MENU_PREFIX = b"\x18"   # C-x
MENU_KEY = b"\x1a"      # C-z
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_TICK_S = 0.2
_LINE_TERM_RE = re.compile(rb"(\r\n|\r|\n)")

# On-demand OSC 133 instrumentation (§4.1).  Typed visibly into the PTY at
# the human's request.  Opportunistic: nothing depends on it working.
INSTRUMENT_SNIPPETS: Dict[str, str] = {
    "bash": (
        "__zs_pc(){ local s=$?; [ -n \"$__zs_in\" ] && printf '\\e]133;D;%s\\a' \"$s\"; "
        "__zs_in=; printf '\\e]133;A\\a'; }; "
        "__zs_dbg(){ [ -z \"$__zs_in\" ] && [ \"$BASH_COMMAND\" != __zs_pc ] && "
        "{ __zs_in=1; printf '\\e]133;C\\a'; }; }; "
        "PROMPT_COMMAND=\"__zs_pc${PROMPT_COMMAND:+;$PROMPT_COMMAND}\"; "
        "PS1=\"$PS1\"'\\[\\e]133;B\\a\\]'; trap __zs_dbg DEBUG"
    ),
    "zsh": (
        "autoload -Uz add-zsh-hook; __zs_pre(){ printf '\\e]133;C\\a' }; "
        "__zs_cmd(){ printf '\\e]133;D;%s\\a\\e]133;A\\a' \"$?\" }; "
        "add-zsh-hook preexec __zs_pre; add-zsh-hook precmd __zs_cmd; "
        "PS1=\"$PS1\"$'%{\\e]133;B\\a%}'"
    ),
}


def _winsize(fd: int):
    try:
        return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
    except (OSError, termios.error):
        return None


class ShadowCore:
    """Everything about a shadow session that does not need a terminal."""

    def __init__(self, argv: List[str], *, label: Optional[str] = None,
                 allow_exec: bool = False, control_ceiling: str = "none",
                 meta: Optional[Dict[str, str]] = None, headless: bool = False,
                 spawned_by: Optional[Dict[str, object]] = None):
        self.argv = list(argv) or [os.environ.get("SHELL", "/bin/sh")]
        # What is *recorded* (registry, journal, shadow_list) is the
        # redacted argv; the child receives self.argv unchanged.
        self.safe_argv = redact_argv(self.argv)
        self.entry = registry.create_session(
            label or " ".join(self.safe_argv), self.safe_argv, allow_exec=allow_exec,
            control_ceiling=control_ceiling, meta=meta, headless=headless,
            spawned_by=spawned_by)
        self.journal = JournalWriter(self.entry.journal)
        self.segmenter = Segmenter(self.journal, on_mode_change=self._on_mode_change)
        self.master_fd: int = -1
        self.child_pid: int = -1
        self.exit_code: Optional[int] = None
        self.gate: Optional[MaskGate] = None
        self.server: Optional[ShadowSocketServer] = None
        self.frontend: Optional["InteractiveFrontend"] = None
        self._write_lock = threading.Lock()
        self._closed = False

    # -- lifecycle ------------------------------------------------------------

    def spawn(self) -> None:
        """Fork the child inside a new PTY and start the socket server."""
        env = dict(os.environ)
        env["ZIYA_SHADOW_SESSION"] = self.entry.session_id
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.execvpe(self.argv[0], self.argv, env)
            except OSError as e:
                os.write(2, f"ziya shadow: cannot exec {self.argv[0]}: {e}\n".encode())
                os._exit(127)
        self.child_pid, self.master_fd = pid, fd
        self.gate = MaskGate(fd)
        self.server = ShadowSocketServer(
            self.entry, self.journal,
            on_comment=self._render_comment, on_meta_change=self._on_meta_change,
            on_attach=self._on_attach)
        self.server.start()
        self.journal.meta("start", {"argv": self.safe_argv, "label": self.entry.label,
                                    "pid": pid, "headless": self.entry.headless})
        atexit.register(self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.segmenter.close()
            self.journal.meta("exit_session", {"code": self.exit_code})
        except Exception:  # noqa: BLE001 — teardown must finish
            pass
        if self.server is not None:
            self.server.stop()
        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        self.entry.remove()

    def terminate_child(self, sig: int = signal.SIGHUP) -> None:
        if self.child_pid > 0:
            try:
                os.kill(self.child_pid, sig)
            except ProcessLookupError:
                pass

    # -- I/O plumbing ---------------------------------------------------------

    def write_to_child(self, data: bytes) -> None:
        with self._write_lock:
            view = memoryview(data)
            while view:
                try:
                    n = os.write(self.master_fd, view)
                except InterruptedError:
                    continue
                view = view[n:]

    def handle_input(self, data: bytes) -> None:
        """The human typed ``data``: account for it, then forward it.

        Accounting runs *before* the bytes reach the child so the ``cmd``
        record always precedes the output it produces.  If the write came
        first, a fast child could echo and answer before ``on_enter`` ran
        (observed from another thread), leaving the output journaled with
        ``cmd_seq=None`` ahead of its own command — which ``tail`` then
        hides.  The masking decision is likewise made per Enter before
        the child sees the terminator, so echo-off state reflects the
        prompt the line answers.
        """
        if not data:
            return
        # Split into lines so each terminator gets its own on_enter().  Only
        # the FIRST line can be checked against the gate: termios reflects
        # the prompt on screen now, and the child has not yet run anything
        # the later lines answer (a pasted ``sudo cmd⏎password⏎``).  Lines
        # after the first are therefore masked fail-closed.  Their kernel
        # echo, if echo is on, still reaches the journal as output — the
        # same bytes the human sees on screen — and is the redactor's job.
        parts = _LINE_TERM_RE.split(data)
        first = True
        for i in range(0, len(parts), 2):
            seg = parts[i]
            term = parts[i + 1] if i + 1 < len(parts) else b""
            self.segmenter.feed_input(seg + term)
            if not term:
                break
            if first:
                reason = self.gate.check() if self.gate else None
                first = False
            else:
                reason = "multi-line-input"
            if reason:
                self.journal.meta("mask", {"reason": reason})
                self.segmenter.on_enter(masked=True, reason=reason)
            else:
                self.segmenter.on_enter(masked=False)
        self.write_to_child(data)

    def handle_output(self, data: bytes) -> None:
        """Bytes from the child: display (frontend) then journal."""
        if self.frontend is not None:
            self.frontend.write_child_output(data)
        self.segmenter.feed_output(data)
        if self.gate is not None:
            self.gate.feed_output(data.decode("utf-8", errors="replace"))

    def resize(self, rows: int, cols: int) -> None:
        if self.master_fd < 0:
            return
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except (OSError, termios.error):
            return
        self.journal.meta("resize", {"rows": rows, "cols": cols})

    def copy_winsize_from(self, fd: int) -> None:
        ws = _winsize(fd)
        if ws:
            rows, cols = struct.unpack("HHHH", ws)[:2]
            self.resize(rows, cols)

    def _on_mode_change(self, old: str, new: str) -> None:
        self.entry.segmentation = new
        self.entry.save()

    def _on_meta_change(self) -> None:
        if self.frontend is not None:
            self.frontend.titles.set_prefix(self.frontend._title_prefix())
            self.frontend.overlay(f'label is now "{self.entry.label}"')

    def _on_attach(self) -> None:
        """Chat attached/detached: banner + title update for the human (§6.1)."""
        if self.frontend is None:
            return
        conv = (self.entry.attached or {}).get("conversation_id")
        self.frontend.overlay(f"attached to chat {str(conv)[:8]}" if conv
                              else "detached from chat")
        self.frontend.refresh_title()

    def _render_comment(self, text: str, provenance: Dict) -> bool:
        if self.frontend is None:
            return False  # headless: comment channel is a no-op (§6.2)
        who = provenance.get("conversation_id", "chat") if isinstance(provenance, dict) else "chat"
        self.frontend.overlay(f"{str(who)[:8]}: {text}")
        return True

    # -- main loop ------------------------------------------------------------

    def run(self, stdin_fd: Optional[int] = None) -> int:
        """Pump bytes until the child exits.  Returns its exit code.

        ``stdin_fd`` is the human's input (interactive); ``None`` runs
        headless — output is journaled, nothing is displayed.
        """
        fds = [self.master_fd] + ([stdin_fd] if stdin_fd is not None else [])
        try:
            while True:
                try:
                    ready, _, _ = select.select(fds, [], [], _TICK_S)
                except InterruptedError:
                    continue
                if self.master_fd in ready:
                    try:
                        data = os.read(self.master_fd, 65536)
                    except OSError as e:
                        if e.errno == errno.EIO:
                            data = b""
                        else:
                            raise
                    if not data:
                        break
                    self.handle_output(data)
                if stdin_fd is not None and stdin_fd in ready:
                    try:
                        data = os.read(stdin_fd, 4096)
                    except OSError:
                        data = b""
                    if not data:
                        # Human's terminal went away: hang up the child.
                        self.terminate_child()
                    elif self.frontend is not None:
                        self.frontend.handle_keys(data)
                    else:
                        self.handle_input(data)
                self.segmenter.tick()
        finally:
            self.exit_code = self._reap()
            self.close()
        return self.exit_code if self.exit_code is not None else 0

    def _reap(self) -> Optional[int]:
        if self.child_pid <= 0:
            return None
        try:
            _, status = os.waitpid(self.child_pid, 0)
        except ChildProcessError:
            return None
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
        return None


class InteractiveFrontend:
    """Terminal-facing half: passthrough, overlay, menu, ask composer (§8)."""

    def __init__(self, core: ShadowCore, stdin_fd: int = 0, stdout_fd: int = 1):
        self.core = core
        self.stdin_fd = stdin_fd
        self.stdout_fd = stdout_fd
        self._out_lock = threading.Lock()
        self._prefix_pending = False
        # None = passthrough; "menu" = awaiting one menu key;
        # ("compose", purpose, buffer) = one-line composer
        self._mode = None
        self._compose_purpose = ""
        self._compose_buf: List[str] = []
        core.frontend = self
        self.titles = TitleRewriter(self._title_prefix())

    def _title_prefix(self) -> str:
        """Window-title prefix: label, plus the attached chat when bound (§8)."""
        e = self.core.entry
        prefix = f"⏺ {e.label}"
        conv = (e.attached or {}).get("conversation_id")
        if conv:
            prefix += f" · chat {str(conv)[:8]}"
        return prefix

    def refresh_title(self) -> None:
        self.titles.set_prefix(self._title_prefix())
        self.write_raw(self.titles.emit())

    # -- output -----------------------------------------------------------------

    def write_raw(self, data: bytes) -> None:
        with self._out_lock:
            view = memoryview(data)
            while view:
                try:
                    n = os.write(self.stdout_fd, view)
                except InterruptedError:
                    continue
                view = view[n:]

    def write_child_output(self, data: bytes) -> None:
        """Display path for child bytes: rewrite window-title OSCs so the
        shadow prefix is always shown (§8), then emit.  The journal is fed
        the original bytes in ``handle_output`` and never sees the rewrite."""
        self.write_raw(self.titles.filter(data))

    def overlay(self, text: str) -> None:
        """One dim ⏺-prefixed line for the human's eyes only (§8).

        ``text`` may originate from the chat model (comment, label,
        provenance).  It is reduced to printable text first: a raw escape
        sequence here would be an injection into the terminal emulator,
        and terminal *replies* (DSR/CPR, answerback) arrive on stdin and
        would be forwarded to the wrapped shell as keystrokes.
        """
        safe = safe_terminal_text(text)
        self.write_raw(f"\r\n{_DIM}⏺ {safe}{_RESET}\r\n".encode("utf-8"))

    # -- input ------------------------------------------------------------------

    def handle_keys(self, data: bytes) -> None:
        if self._mode == "menu":
            self._mode = None
            self._menu_key(data[:1])
            if len(data) > 1:
                self.handle_keys(data[1:])
            return
        if self._mode == "compose":
            self._compose_keys(data)
            return
        out = bytearray()
        for i, b in enumerate(data):
            byte = bytes([b])
            if self._prefix_pending:
                self._prefix_pending = False
                if byte == MENU_KEY:
                    if out:
                        self.core.handle_input(bytes(out))
                        out.clear()
                    self._open_menu()
                    # Whatever follows in this read is menu input.
                    if i + 1 < len(data):
                        self.handle_keys(data[i + 1:])
                    return
                if byte == MENU_PREFIX:       # double-press passes a literal C-x
                    out += MENU_PREFIX
                    continue
                out += MENU_PREFIX            # not the menu: deliver the held C-x
            if byte == MENU_PREFIX:
                self._prefix_pending = True
                continue
            out += byte
        if out:
            self.core.handle_input(bytes(out))

    # -- menu -----------------------------------------------------------------------

    def _open_menu(self) -> None:
        e = self.core.entry
        exec_state = "on" if e.allow_exec else "off"
        self.overlay(
            f'shadow {e.session_id} ("{e.label}") · seg={e.segmentation} · '
            f"[a] ask · [l] relabel · [i] instrument shell · [x] exec {exec_state} · "
            f"[q] end session · other key: cancel")
        self._mode = "menu"

    def _menu_key(self, key: bytes) -> None:
        e = self.core.entry
        if key == b"a":
            self._start_compose("ask", "ask (Enter to send, Esc to cancel): ")
        elif key == b"l":
            self._start_compose("label", f'new label [{e.label}]: ')
        elif key == b"i":
            self._start_compose("instrument", "instrument which shell? [b]ash / [z]sh: ")
        elif key == b"x":
            e.allow_exec = not e.allow_exec
            e.save()
            self.core.journal.meta("allow_exec", {"enabled": e.allow_exec})
            self.overlay(f"exec requests {'enabled' if e.allow_exec else 'disabled'} "
                         f"(exec handshake lands in phase 2)")
        elif key == b"q":
            self.overlay("ending shadow session (SIGHUP to child)")
            self.core.terminate_child()
        else:
            self.overlay("cancelled")

    # -- one-line composer -----------------------------------------------------------

    def _start_compose(self, purpose: str, prompt: str) -> None:
        self._mode = "compose"
        self._compose_purpose = purpose
        self._compose_buf = []
        self.write_raw(f"\r\n{_DIM}⏺ {prompt}".encode("utf-8"))

    def _compose_keys(self, data: bytes) -> None:
        for b in data:
            ch = chr(b)
            if ch in ("\r", "\n"):
                self.write_raw(f"{_RESET}\r\n".encode())
                self._mode = None
                self._compose_done("".join(self._compose_buf))
                return
            if b in (0x1b, 0x03):             # Esc / ^C
                self.write_raw(f" (cancelled){_RESET}\r\n".encode())
                self._mode = None
                return
            if b in (0x7f, 0x08):
                if self._compose_buf:
                    self._compose_buf.pop()
                    self.write_raw(b"\b \b")
                continue
            if b >= 0x20:
                self._compose_buf.append(ch)
                self.write_raw(ch.encode("utf-8", errors="replace"))

    def _compose_done(self, text: str) -> None:
        core, e = self.core, self.core.entry
        purpose = self._compose_purpose
        if purpose == "label":
            if text.strip():
                e.label = text.strip()[:120]
                e.save()
                self.titles.set_prefix(f"⏺ {e.label}")
                core.journal.meta("meta", {"label": e.label, "provenance": {"by": "shadow-menu"}})
                self.overlay(f'label is now "{e.label}"')
        elif purpose == "ask":
            if text.strip():
                tail = core.journal._seq
                seq = core.journal.meta("ask", {
                    "question": text.strip(),
                    "context_from_seq": max(1, tail - 40),
                    "context_to_seq": tail})
                e.pending_ask = True
                e.save()
                self.overlay(f"asked (journal seq {seq}); an attached chat will pick it up")
        elif purpose == "instrument":
            shell = {"b": "bash", "z": "zsh"}.get(text.strip()[:1].lower())
            if shell is None:
                self.overlay("cancelled")
                return
            snippet = INSTRUMENT_SNIPPETS[shell]
            core.journal.meta("instrument", {"shell": shell})
            # Typed visibly, exactly as the human would — §6 principle.
            core.handle_input(snippet.encode("utf-8") + b"\r")

    # -- run ----------------------------------------------------------------------

    def run(self) -> int:
        core = self.core
        if not os.isatty(self.stdin_fd):
            raise RuntimeError("ziya shadow needs an interactive terminal")
        core.spawn()
        core.copy_winsize_from(self.stdin_fd)
        saved = termios.tcgetattr(self.stdin_fd)

        def _on_winch(signum, frame):
            core.copy_winsize_from(self.stdin_fd)

        def _on_term(signum, frame):
            core.terminate_child(signal.SIGHUP)

        signal.signal(signal.SIGWINCH, _on_winch)
        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGHUP, _on_term)
        e = core.entry
        self.overlay(f'shadow session {e.session_id} ("{e.label}") — journaling locally. '
                     f"C-x C-z for menu.")
        try:
            tty.setraw(self.stdin_fd)
            code = core.run(stdin_fd=self.stdin_fd)
        finally:
            termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, saved)
        self.write_raw(f"\r\n{_DIM}⏺ shadow session {e.session_id} ended "
                       f"(exit {code}); journal removed.{_RESET}\r\n".encode("utf-8"))
        return code


def run_interactive(argv: List[str], *, label: Optional[str] = None,
                    allow_exec: bool = False, control_ceiling: str = "none",
                    meta: Optional[Dict[str, str]] = None) -> int:
    """Entry point for ``ziya shadow`` (§2).  Returns the child's exit code."""
    core = ShadowCore(argv, label=label, allow_exec=allow_exec,
                      control_ceiling=control_ceiling, meta=meta)
    return InteractiveFrontend(core).run()


def run_headless(argv: List[str], *, label: Optional[str] = None,
                 control_ceiling: str = "gated",
                 spawned_by: Optional[Dict[str, object]] = None) -> int:
    """Run a session with no human terminal (§6.2 core-only).

    Phase 1 exposes this for tests and for the phase-3 ``shadow_spawn``
    tool; it is not reachable from the CLI yet.
    """
    core = ShadowCore(argv, label=label, headless=True,
                      control_ceiling=control_ceiling, spawned_by=spawned_by)
    core.spawn()
    return core.run(stdin_fd=None)
