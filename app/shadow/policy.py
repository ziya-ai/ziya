"""Command policy for shadow line control (design doc §6.3).

A line-control lease drives a remote shell one command at a time.  Every
``send_line`` runs through a policy verdict *in the shadow host* before
the bytes reach the PTY — the chat side may pre-check, but the host is
the enforcement point, because any same-UID process can speak the socket.

Two independent axes, minimum wins (the §6.1 two-sided rule per axis):

* **policy set** — what counts as an *allowed* command:
    - ``builtin``         the shipped default shell allowlist (read-only
                          diagnostics; no user edits, no yolo) — the
                          default for spawned sessions and control leases
    - ``none``            everything (only sensible with ``unrestricted``)
    - ``named:<set>``     an explicit allowlist file
                          ``~/.ziya/shadow/policies/<set>.json`` (same
                          schema as the shell config, so it is editable
                          with the existing tooling and shareable)
    - ``inherit``         the persisted local shell allowlist (deferred
                          in this build — see ``resolve_policy``)
* **mode** — what happens to a command the policy does *not* allow:
    - ``strict``          refuse, no human prompt (the only mode a
                          headless / spawned session may hold)
    - ``gated``           one-keystroke confirm banner at the terminal
    - ``unrestricted``    run it, journaled

**Remote profile.**  The allowlist engine is ``ShellServer``'s static
``is_command_allowed`` (tokenising, operator splitting, aws/curl/IaC
blocks) with two deliberate departures, because the local pipeline phases
they normally defer to cannot run against a remote host:

1. **yolo never inherits** — the instance's ``yolo_mode`` is forced off.
2. **paths are opaque** — the local write-checker validates redirection
   targets and destructive-command paths against local directories; it
   cannot judge a remote path, so this module treats *any* redirection,
   *any* destructive command (rm/mv/cp/...), and *any* command
   substitution as "not allowed".  ``is_command_allowed`` admits ``rm``
   and ``echo >f`` precisely because it expects that later phase to
   vet them; here there is no later phase, so they fall to the mode
   table.  "Mutating", in the design's language, simply means "not
   allowed by the read-only remote profile".

Nothing here executes anything or touches a PTY; it is pure classification
and is unit-tested in isolation.
"""
import contextlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Optional, Tuple

# --- verdict / mode vocabulary ------------------------------------------------

ALLOWED = "allowed"           # read-only and clean under the remote profile
NOT_ALLOWED = "not_allowed"   # redirection / destructive / subst / off-allowlist

# ShellServer narrates its decisions with print(file=sys.stderr).  Inside a
# shadow host stderr IS the human's terminal, so every verdict would spray
# debug text onto their screen (and block on a full PTY buffer).  All engine
# calls run under this lock with stderr redirected to /dev/null.
_ENGINE_IO_LOCK = threading.Lock()


@contextlib.contextmanager
def _quiet_engine():
    with _ENGINE_IO_LOCK, open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
        yield


RUN = "run"
CONFIRM = "confirm"           # gated: one-keystroke human grant at the terminal
DENY = "deny"

VALID_MODES = ("supervised", "strict", "gated", "unrestricted")


def policies_dir() -> Path:
    """``~/.ziya/shadow/policies`` — created 0700 on first use."""
    d = Path(os.path.expanduser("~/.ziya/shadow/policies"))
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


# --- remote-profile detectors (paths opaque) ---------------------------------

# Any unquoted ``>`` is a write we cannot vet (``>``, ``>>``, ``2>``, ``&>``,
# ``>|``, ``>(...)``), and ``<(...)`` runs a command.  No whitespace
# requirement: ``echo hi>f`` is as much a redirection as ``echo hi > f``.
_REDIRECT_RE = re.compile(r'>|<\(|<>')
# In-place editors write a file whose path we cannot vet remotely.  The
# ``i`` may sit inside a flag cluster (``sed -ni``, ``sed -Ei``, ``perl -pi``).
_INPLACE_RE = re.compile(r'\b(?:sed|perl|gawk)\b[^|;&]*\s-[A-Za-z]*i'
                         r'|\bsed\b[^|;&]*--in-place')
_QUOTED_RE = re.compile(r'"[^"]*"' r"|'[^']*'")
# Allowlisted locally (their targets are path-checked there) but they write
# files, which we cannot vet on a remote host: mutating under the remote profile.
_REMOTE_WRITERS = {"tee", "touch", "dd", "install", "truncate", "patch"}
# Their argument *is* a command: the classifier would have to recurse into
# an argv it cannot see the shell's parse of, so they are simply not allowed.
_COMMAND_WRAPPERS = {"xargs", "time", "timeout", "nohup", "nice", "ionice", "chrt",
                     "env", "command", "builtin", "exec", "eval", "sudo", "doas",
                     "watch", "stdbuf", "setsid", "caffeinate", "strace", "ltrace",
                     "parallel", "su"}
# Interpreters run arbitrary code (inline or from a script we cannot read).
_REMOTE_INTERPRETERS = {"python", "python2", "python3", "ruby", "node", "nodejs",
                        "perl", "php", "lua", "luajit", "tclsh", "Rscript", "julia",
                        "sh", "bash", "zsh", "ksh", "dash", "fish", "csh", "tcsh"}
_FIND_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                 "-fprint", "-fprint0", "-fprintf", "-fls"}
_AWK_HEADS = {"awk", "gawk", "mawk", "nawk"}
_ENV_ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')


def _strip_quoted(command: str) -> str:
    return _QUOTED_RE.sub(" ", command)


def has_control_chars(command: str) -> bool:
    """True if the text contains any C0 control byte or DEL.

    A newline or CR is a second command; ESC drives the line editor; ^C/^D
    interrupt or hang up the child; TAB completes (changes the command).
    ``send_line`` sends exactly one printable line.
    """
    return bool(_CONTROL_CHARS_RE.search(command))


def split_segments(command: str):
    """Split on unquoted ``;`` ``|`` ``&`` ``&&`` ``||`` ``|&`` and line
    terminators, respecting single/double quotes and backslash escapes.

    Every returned segment is a simple command the remote shell would run
    separately; classification is per segment so a benign head cannot
    shelter what follows it (``ls & rm -rf /``).
    """
    segs, cur, q, esc = [], [], None, False
    for ch in command:
        if esc:
            cur.append(ch); esc = False
            continue
        if q:
            if ch == q:
                q = None
            elif ch == "\\" and q == '"':
                esc = True
            cur.append(ch)
            continue
        if ch == "\\":
            esc = True; cur.append(ch)
            continue
        if ch in ("'", '"'):
            q = ch; cur.append(ch)
            continue
        if ch in ";|&\n\r":
            seg = "".join(cur).strip()
            if seg:
                segs.append(seg)
            cur = []
            continue
        cur.append(ch)
    seg = "".join(cur).strip()
    if seg:
        segs.append(seg)
    return segs


def has_redirection(command: str) -> bool:
    """True if the command writes via a shell redirection, process
    substitution or in-place edit.

    Quote-aware: quoted spans are removed before scanning, so
    ``grep ">" file`` is not a redirection but ``x>file`` is.  ``2>&1``
    and ``>/dev/null`` are redirections too — conservative by design; on
    a remote host we cannot tell ``/dev/null`` from ``/etc/passwd``.
    """
    bare = _strip_quoted(command)
    if _INPLACE_RE.search(bare):
        return True
    return bool(_REDIRECT_RE.search(bare))


def has_substitution(command: str) -> bool:
    """True if the command contains ``$(...)`` or backtick substitution.

    Reuses the shell server's own extractor so detection matches exactly
    what the local validator recurses into.  Resolving a substitution
    would run it on the wrong machine, so its mere presence is disallowing.
    """
    try:
        from app.mcp_servers.shell_server import _extract_command_substitutions
        return bool(_extract_command_substitutions(command))
    except Exception:  # noqa: BLE001 — fall back to a cheap literal check
        bare = _strip_quoted(command)
        return "$(" in bare or "`" in bare


def _head_and_args(segment: str):
    """(head, args) of a segment with leading ``NAME=value`` prefixes peeled."""
    toks = segment.split()
    while toks and _ENV_ASSIGN_RE.match(toks[0]):
        toks.pop(0)
    if not toks:
        return "", []
    return toks[0], toks[1:]


def first_words(command: str):
    """Head token of each segment, env-assignment prefixes peeled."""
    out = []
    for seg in split_segments(command):
        head, _ = _head_and_args(seg)
        if head:
            out.append(head)
    return out


def _segment_reason(segment: str, destructive) -> str:
    """Remote-profile verdict for one simple command; '' if clean."""
    head, args = _head_and_args(segment)
    if not head:
        return "empty command"
    base = os.path.basename(head)
    if head in destructive or base in destructive or base in _REMOTE_WRITERS:
        return f"'{base}' modifies files (target not verifiable remotely)"
    if base in _COMMAND_WRAPPERS:
        return f"'{base}' wraps another command (cannot classify it remotely)"
    if base in _REMOTE_INTERPRETERS:
        return f"'{base}' runs arbitrary code (cannot classify it remotely)"
    if base == "find" and any(a in _FIND_ACTIONS for a in args):
        return "find with an action that modifies files or runs commands"
    if base in _AWK_HEADS and ("system(" in segment or ">" in segment or "|" in segment):
        return "awk program writes a file or runs a command"
    return ""


# --- the policy object --------------------------------------------------------

class RemotePolicy:
    """Resolved policy set + mode; classifies and decides one command.

    Construct via :func:`resolve_policy`.  Holds a lazily-built
    ``ShellServer`` as the allowlist engine (yolo forced off).  ``none``
    needs no engine.
    """

    def __init__(self, policy_set: str, mode: str, *,
                 allowed_commands: Optional[list] = None):
        if mode not in VALID_MODES:
            raise ValueError(f"invalid mode: {mode!r}")
        self.policy_set = policy_set
        self.mode = mode
        self._allowed_commands = allowed_commands  # for named:; None = engine default
        self._engine = None
        self._destructive = set()

    # -- engine (allowlist) ----------------------------------------------------

    def _ensure_engine(self):
        if self._engine is not None or self.policy_set == "none":
            return
        from app.mcp_servers.shell_server import ShellServer
        with _quiet_engine():
            eng = ShellServer()
            eng.yolo_mode = False  # yolo NEVER inherits onto a remote lease (§6.3)
            if self._allowed_commands is not None:
                eng.allowed_commands = list(self._allowed_commands)
                eng.safe_command_patterns = eng._build_safe_command_patterns()
        self._engine = eng
        try:
            self._destructive = set(eng.wp_manager.policy.get("destructive_commands", []))
        except Exception:  # noqa: BLE001
            self._destructive = {"rm", "mv", "cp", "mkdir", "rmdir", "chmod",
                                 "chown", "chgrp", "ln"}

    # -- classification --------------------------------------------------------

    def classify(self, command: str) -> Tuple[str, str]:
        """Return (ALLOWED|NOT_ALLOWED, reason).

        ``reason`` is empty when allowed; otherwise a short human string
        for the grant banner and the model's rejection.
        """
        command = (command or "").strip()
        if not command:
            return NOT_ALLOWED, "empty command"
        if self.policy_set == "none":
            return ALLOWED, ""

        # Remote-profile departures (paths opaque) — checked before the
        # allowlist so the reason names the real reason, not "off allowlist".
        if has_control_chars(command):
            return NOT_ALLOWED, "contains a line terminator or control character (one command per line)"
        if has_substitution(command):
            return NOT_ALLOWED, "command substitution cannot be resolved on a remote host"
        if has_redirection(command):
            return NOT_ALLOWED, "writes a file via redirection (target not verifiable remotely)"
        self._ensure_engine()
        segments = split_segments(command)
        if not segments:
            return NOT_ALLOWED, "empty command"
        for seg in segments:
            why = _segment_reason(seg, self._destructive)
            if why:
                return NOT_ALLOWED, why

        # The allowlist engine, per segment: it does not split on ``&`` or
        # line terminators itself, so a benign head must not shelter a
        # trailing off-allowlist command.
        with _quiet_engine():
            for seg in segments:
                ok, why = self._engine.is_command_allowed(seg)
                if not ok:
                    return NOT_ALLOWED, why or "not in the allowlist"
        return ALLOWED, ""

    # -- decision (verdict × mode) ---------------------------------------------

    def decide(self, command: str) -> Tuple[str, str]:
        """Return (RUN|CONFIRM|DENY, reason) applying the mode table."""
        verdict, reason = self.classify(command)
        if self.mode == "supervised":
            # The human approves every command, allowed or not.
            return CONFIRM, reason or "supervised lease: every command is confirmed at the terminal"
        if verdict == ALLOWED:
            return RUN, ""
        if self.mode == "unrestricted":
            return RUN, reason           # journaled, but runs
        if self.mode == "gated":
            return CONFIRM, reason        # one-keystroke grant at the terminal
        return DENY, reason               # strict

    def describe(self) -> str:
        """One-line summary for the grant banner / model context."""
        return f"policy {self.policy_set} · mode {self.mode}"


# --- resolution ---------------------------------------------------------------

def clamp_restriction_for(requested: str, ceiling: str) -> Optional[str]:
    """Effective restriction after the session-ceiling clamp, or None if
    the ceiling permits no lease (the lease layer reports that case)."""
    from app.shadow.lease import clamp_restriction
    try:
        return clamp_restriction(requested, ceiling)
    except ValueError:
        return None


def load_named_policy(name: str) -> dict:
    """Load ``~/.ziya/shadow/policies/<name>.json`` (shell-config schema).

    Raises ``FileNotFoundError`` / ``ValueError`` — the caller surfaces a
    clear error rather than silently falling open.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name or ""):
        raise ValueError(f"invalid policy name: {name!r}")
    path = policies_dir() / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "allowedCommands" not in data:
        raise ValueError(f"policy {name!r} missing 'allowedCommands'")
    if not isinstance(data["allowedCommands"], list):
        raise ValueError(f"policy {name!r}: 'allowedCommands' must be a list")
    return data


def resolve_policy(policy_set: str, mode: str) -> RemotePolicy:
    """Build a :class:`RemotePolicy` from a ``policy_set`` spec and mode.

    ``policy_set`` is ``none``, ``inherit``, or ``named:<name>``.
    ``inherit`` is deferred in this build: it is accepted only to give a
    precise error, since wiring the live local shell config onto a remote
    lease needs its own review.
    """
    if policy_set == "none":
        return RemotePolicy("none", mode)
    if policy_set == "builtin":
        return RemotePolicy("builtin", mode)   # engine default allowlist, no override
    if policy_set == "inherit":
        raise NotImplementedError(
            "policy 'inherit' is not available yet; use a named policy "
            "(~/.ziya/shadow/policies/<set>.json) or 'none'")
    if policy_set.startswith("named:"):
        name = policy_set.split(":", 1)[1]
        cfg = load_named_policy(name)
        return RemotePolicy(policy_set, mode, allowed_commands=cfg["allowedCommands"])
    raise ValueError(f"unknown policy set: {policy_set!r}")


# --- spawn gate (§6.2) ---------------------------------------------------------
#
# ``shadow_spawn`` runs argv on the *local* machine with no shell allowlist
# in front of it and no human keystroke (the design assumed a tool-approval
# step that builtin tools do not have).  The head of argv must therefore be
# on a small spawn allowlist — shipped as just ``ssh`` — kept in
# ``~/.ziya/shadow/policies/spawn.json`` (same ``allowedCommands`` schema)
# so a user can add e.g. ``kubectl`` or ``mosh`` deliberately.  For ssh, the
# argument forms that execute code at spawn time (a remote command, a
# ProxyCommand/LocalCommand option, an alternate config file, control
# commands) are refused as well: the send_line policy never sees them.

DEFAULT_SPAWN_ALLOWLIST = ["ssh"]
# ssh options that take a separate argument (OpenSSH 9.x ``ssh -h``).
_SSH_ARG_OPTS = set("BbcDEeFIiJLlmOopQRSWw")
_SSH_DENY_OPTS = set("FOW")   # config file / control command / stdio forward
_SSH_DENY_O_KEYS = {"proxycommand", "localcommand", "permitlocalcommand",
                    "knownhostscommand", "include", "match", "remotecommand",
                    "proxyusefdpass", "identityagent", "pkcs11provider",
                    "securitykeyprovider"}


def load_spawn_allowlist() -> list:
    """Heads a headless session may be spawned with (basenames)."""
    path = policies_dir() / "spawn.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return list(DEFAULT_SPAWN_ALLOWLIST)
    cmds = data.get("allowedCommands") if isinstance(data, dict) else None
    if not isinstance(cmds, list) or not all(isinstance(c, str) for c in cmds):
        raise ValueError("spawn.json: 'allowedCommands' must be a list of strings")
    return list(cmds)


def _check_ssh_argv(args) -> Tuple[bool, str]:
    """Accept ``ssh [options] destination`` only — no remote command."""
    i, dest = 0, None
    while i < len(args):
        a = args[i]
        if a == "--":
            return False, "ssh: '--' introduces a remote command"
        if dest is None and a.startswith("-") and len(a) > 1:
            flags, attached = a[1:], None
            # A cluster ends in an option that takes an argument; the
            # argument is either attached (-oFoo=bar) or the next token.
            for k, ch in enumerate(flags):
                if ch in _SSH_ARG_OPTS:
                    attached = flags[k + 1:] or None
                    flags = flags[:k + 1]
                    break
            for ch in flags:
                if ch in _SSH_DENY_OPTS:
                    return False, f"ssh: option -{ch} is not allowed for a spawned session"
            last = flags[-1]
            if last in _SSH_ARG_OPTS:
                if attached is None:
                    i += 1
                    if i >= len(args):
                        return False, f"ssh: option -{last} needs an argument"
                    attached = args[i]
                if last == "o":
                    key = attached.split("=", 1)[0].split(None, 1)[0].strip().lower()
                    if key in _SSH_DENY_O_KEYS:
                        return False, f"ssh: -o {attached.split('=',1)[0]} is not allowed"
            i += 1
            continue
        if dest is None:
            dest = a
            i += 1
            continue
        return False, "ssh: a remote command is not allowed at spawn (use send_line)"
    if dest is None:
        return False, "ssh: destination is required"
    return True, ""


def check_spawn_argv(argv) -> Tuple[bool, str]:
    """(ok, reason) for spawning ``argv`` headless."""
    if not argv or not isinstance(argv, (list, tuple)):
        return False, "argv is required"
    head = os.path.basename(str(argv[0]))
    if str(argv[0]) != head:
        return False, f"spawn head must be a bare program name, not a path: {argv[0]!r}"
    allowed = load_spawn_allowlist()
    if head not in allowed:
        return False, (f"'{head}' is not on the spawn allowlist "
                       f"(~/.ziya/shadow/policies/spawn.json: {', '.join(allowed)})")
    if head == "ssh":
        return _check_ssh_argv([str(a) for a in argv[1:]])
    return True, ""