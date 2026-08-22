"""
Shell-specific write policy checks.

Uses WritePolicyManager for path approval. Adds shell-specific
checks: in-place flags, redirection, destructive commands, interpreter heuristics.
"""

import fnmatch
import os
import re
import shlex
import sys
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config.write_policy import WritePolicyManager


# Per-call task scope, held in a ContextVar rather than on the checker
# instance. The shell server shares ONE ShellWriteChecker across all
# requests, so an instance attribute is only safe while the request loop
# is strictly serial. Once requests are dispatched concurrently, an
# instance attribute leaks across conversations: A's set_task_scope() is
# visible to B's allowlist/write checks, and A's clear_task_scope() in a
# finally block can erase the scope out from under B mid-validation. The
# envelope carries both ``writable`` paths and ``shell_commands`` grants,
# so a leak is an authorization bug, not just a race on throughput.
#
# A ContextVar is per-task, so each concurrently-dispatched request sees
# only its own grant. Default is None (not {}) to avoid sharing a single
# mutable dict across contexts.
_TASK_SCOPE: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "ziya_shell_task_scope", default=None
)

# Per-call project root, same rationale as _TASK_SCOPE above: one
# ShellWriteChecker is shared across concurrently-dispatched requests, so the
# root a request is anchored to cannot live on the instance. Supplied by the
# caller on every call; the process environment is deliberately not a fallback,
# since it names whichever project the server process was launched in.
_PROJECT_ROOT: ContextVar[Optional[str]] = ContextVar(
    "ziya_shell_project_root", default=None
)


class ShellWriteChecker:
    def __init__(self, pm: WritePolicyManager):
        self.pm = pm
        # Reset the context's task scope at construction. Previously the
        # scope was an instance attribute initialized to {}, so a fresh
        # checker always started with no grant. Moving the state to a
        # module-level ContextVar removed that guarantee: a scope set via
        # an earlier checker would still be visible here, since the
        # ContextVar outlives any single instance. Clearing it restores
        # the original "new checker => no grant" invariant, which both the
        # serial server path and per-test fixtures rely on.
        _TASK_SCOPE.set(None)

    @property
    def _task_scope(self) -> Dict[str, Any]:
        """The current context's task scope (empty dict when unset).

        Read-only. Set via ``set_task_scope`` / ``clear_task_scope`` so the
        value lands in the ContextVar and stays confined to this request's
        task. Kept under the original attribute name so the six existing
        read sites in this module need no change.

        When non-empty, ``_is_write_allowed`` consults it *in addition* to
        the base ``WritePolicyManager`` — a path the task has been granted
        is permitted even if the base policy would deny it. Each entry is
        ``{"path": str, "is_dir": bool}``, interpreted relative to the
        task's effective project root. The shape is intentionally an
        envelope so further per-task scope categories can be added without
        changing the wire payload.
        """
        return _TASK_SCOPE.get() or {}

    def set_task_scope(self, scope: Optional[Dict[str, Any]]) -> None:
        """Set the per-call task scope (or clear with ``None``).

        Expected shape: ``{"writable": [{"path": str, "is_dir": bool}, ...],
        "project_root": str}``.  Unknown keys are ignored so the same
        envelope can later carry e.g. ``{"commands": [...]}`` for Slice B.
        """
        _TASK_SCOPE.set(scope or {})

    def clear_task_scope(self) -> None:
        _TASK_SCOPE.set({})

    @property
    def _project_root(self) -> str:
        """The project root the caller anchored THIS request to."""
        return _PROJECT_ROOT.get() or ""

    def set_project_root(self, root: Optional[str]) -> None:
        """Anchor this request's write checks to *root*.

        The caller states which project the command belongs to; the checker
        never infers it from the process environment. Passing the root through
        to WritePolicyManager also makes it load THAT project's policy rather
        than whichever project last touched the shared manager.
        """
        _PROJECT_ROOT.set(root or None)

    def clear_project_root(self) -> None:
        _PROJECT_ROOT.set(None)

    @property
    def policy(self):
        return self.pm.policy

    def check(self, command: str, split_fn: Callable) -> Tuple[bool, str]:
        # Heredoc bodies are stdin *data*, not commands. Strip them before
        # splitting so body lines containing words like ``rm`` or ``sudo``
        # aren't mistaken for command segments. The redirection scan below
        # still receives the original command (it strips bodies itself).
        scan_command = _strip_heredoc_bodies(command)
        # ``split_fn`` (the server's operator splitter) does not break on
        # newlines, so a command hidden after a heredoc terminator —
        # e.g. ``cat <<EOF\n..\nEOF\nrm /etc/passwd`` — would otherwise
        # collapse into a single unchecked segment. Split on newlines too.
        for raw_line in scan_command.split('\n'):
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            for _op, seg in split_fn(line):
                for fn in (self._always_blocked, self._destructive,
                           self._inplace_edit, self._interpreter):
                    ok, reason = fn(seg)
                    if not ok:
                        return False, reason
        return self._redirection(command)

    def _always_blocked(self, cmd: str) -> Tuple[bool, str]:
        tok = _tokenize(cmd)
        if tok and tok[0] in self.policy.get('always_blocked', []):
            return False, f"Command '{tok[0]}' is never allowed."
        return True, ""

    def _destructive(self, cmd: str) -> Tuple[bool, str]:
        # Drop redirection operators/targets (``2>&1``, ``> log``, ``2>/dev/null``)
        # before extracting write targets. Otherwise a redirection token gets
        # mistaken for the destructive command's target — e.g. ``cp src dst 2>&1``
        # validated ``2>&1`` as the cp destination and blocked it. Redirection
        # targets are validated separately by ``_redirection``.
        tok = _strip_redirections(_tokenize(cmd))
        if not tok or tok[0] not in self.policy.get('destructive_commands', []):
            return True, ""
        # Slice B: per-task ``shell_commands`` grant overrides the
        # destructive-command block.  ``_always_blocked`` (caller)
        # and redirection rules remain hard ceilings — those are
        # checked separately and not bypassable per-task.
        if self._task_scope_grants_command(cmd):
            return True, ""
        args = [t for t in tok[1:] if not t.startswith('-')]
        # For cp/mv, only the last argument is the write target;
        # earlier arguments are read-only sources.
        if tok[0] in ('cp', 'mv') and len(args) >= 2:
            targets = args[-1:]
        else:
            targets = args
        if not targets:
            return False, f"Command '{tok[0]}' requires a target path."
        for t in targets:
            if not self._is_write_allowed(t):
                return False, f"'{tok[0]} {t}' blocked — use git diffs for project file changes."
        return True, ""

    def _inplace_edit(self, cmd: str) -> Tuple[bool, str]:
        for prog, flags in self.policy.get('inplace_edit_flags', {}).items():
            s = cmd.strip()
            if not (s.startswith(prog + ' ') or s == prog):
                continue
            tok = _tokenize(cmd)
            matched_flag = None
            for flag in flags:
                for t in tok[1:]:
                    if t == flag or t.startswith(flag):
                        matched_flag = flag
                        break
                if matched_flag:
                    break
            if not matched_flag:
                continue
            # In-place edit detected. Rather than a blanket block, allow it
            # when every file being edited in place resolves to a path the
            # write policy (safe_write_paths / allowed_write_patterns) or
            # active task scope already approves -- the same config gate
            # cp/mv/rm already respect via _destructive/_is_write_allowed.
            # Conservative: the leading non-flag token is treated as the
            # script/pattern argument (e.g. sed's "s#..#..#"); everything
            # after it is treated as file target(s) and must be approved.
            non_flag = [t for t in tok[1:] if not t.startswith('-')]
            targets = non_flag[1:] if len(non_flag) > 1 else []
            if not targets:
                return False, f"In-place editing with '{prog} {matched_flag}' is not allowed. Use git diffs."
            for t in targets:
                if not self._is_write_allowed(t):
                    return False, (
                        f"In-place editing with '{prog} {matched_flag}' targeting '{t}' is not allowed. "
                        f"Use git diffs, or target a path approved by write policy."
                    )
        return True, ""

    def _interpreter(self, cmd: str) -> Tuple[bool, str]:
        tok = _tokenize(cmd)
        if not tok or tok[0] not in self.policy.get('allowed_interpreters', []):
            return True, ""
        s = cmd.strip()

        # Process-spawning check runs unconditionally, before any safe-pattern
        # short-circuit. interpreter_safe_patterns (e.g. the blanket
        # ``python3 -c`` match) only certifies the *shape* of the invocation,
        # not what the interpreted code does — a one-liner that spawns a
        # process via os.system/subprocess(shell=True)/eval/exec never
        # touches the filesystem, so script_write_indicators never fires,
        # letting an allowlisted interpreter become a full shell escape
        # [PenPal #157, CWE-94]. Same task-scope and redirection handling as
        # the write-indicator path below, for consistency.
        for pat in self.policy.get('script_process_indicators', []):
            if re.search(pat, cmd):
                if self._task_scope_grants_command(cmd):
                    return self._redirection(cmd)
                return False, f"Script appears to spawn a process (matched: {pat}). Use an allowlisted command directly instead."
        
        # Check if command matches a safe pattern
        matched_safe_pattern = False
        for pat in self.policy.get('interpreter_safe_patterns', []):
            if re.match(pat, s):
                matched_safe_pattern = True
                break
        
        # Even if safe pattern matched, check for obvious write operations in -c commands
        if matched_safe_pattern and re.match(r'^python3?\s+-c\s+', s):
            for pat in self.policy.get('script_write_indicators', []):
                if re.search(pat, cmd):
                    # Scope-aware exemption: if every file the script
                    # writes is inside permitted writable scope, the
                    # write is allowed — fall through to the redirection
                    # check instead of a blanket block.
                    if self._writes_within_scope(cmd):
                        return self._redirection(cmd)
                    return False, f"Script appears to write files (matched: {pat}). Use git diffs."
        
        # Safe pattern matched and no obvious writes → only check redirection
        if matched_safe_pattern:
            return self._redirection(cmd)
        
        # Not a safe pattern → check all write indicators
        for pat in self.policy.get('script_write_indicators', []):
            if re.search(pat, cmd):
                # Scope-aware exemption (see above): allow when every
                # write target resolves inside writable scope.
                if self._writes_within_scope(cmd):
                    return self._redirection(cmd)
                # Slice B: per-task shell command grant overrides the
                # script-write-indicator block.  Useful for tasks that
                # legitimately need an interpreter one-liner ``python -c``.
                if self._task_scope_grants_command(cmd):
                    return self._redirection(cmd)
                return False, f"Script appears to write files (matched: {pat}). Use git diffs."
        return True, ""

    def _writes_within_scope(self, cmd: str) -> bool:
        """Return True iff every file the one-liner writes is in scope.

        Conservative: returns False unless all of the script's writes map
        to recognized path-bearing forms with a literal path.  Any
        destructive/opaque write keeps the blanket block (see
        _extract_write_target_paths).
        """
        paths, complete = _extract_write_target_paths(cmd)
        if not complete or not paths:
            return False
        return all(self._is_write_allowed(p) for p in paths)

    def _redirection(self, command: str) -> Tuple[bool, str]:
        # Strip heredoc bodies — they aren't shell-level I/O but contain
        # arbitrary code that may include >, >=, >> operators.
        command = _strip_heredoc_bodies(command)
        i, ln = 0, len(command)
        sq = dq = False
        while i < ln:
            ch = command[i]
            if ch == '\\' and i + 1 < ln:
                i += 2; continue
            if ch == "'" and not dq:
                sq = not sq; i += 1; continue
            if ch == '"' and not sq:
                dq = not dq; i += 1; continue
            if sq or dq:
                i += 1; continue
            if ch in '>&' or (ch.isdigit() and i + 1 < ln and command[i + 1] == '>'):
                if ch.isdigit():
                    i += 1
                if ch == '&':
                    i += 1
                if i < ln and command[i] == '>':
                    i += 1
                    if i < ln and command[i] == '>':
                        i += 1
                    while i < ln and command[i] in ' \t':
                        i += 1
                    if i < ln:
                        target, end = _extract_target(command, i)
                        if target and not self._is_write_allowed(target):
                            return False, f"Redirection to '{target}' blocked."
                        i = end
                continue
            i += 1
        return True, ""

    # -- Task scope (additive write grant) -----------------------------

    def _is_write_allowed(self, target_path: str) -> bool:
        """Return True iff the base policy or the active task scope
        permits a write to *target_path*.

        The check is additive: if the base ``WritePolicyManager``
        already allows the write, we return True without consulting
        the task scope.  Only when the base check fails do we fall
        back to the task grant.
        """
        # Special device files are not real filesystem writes — writing
        # or redirecting to them is always permitted (matches the DEVNULL
        # handling in shell_server.py).
        if _is_special_device(target_path):
            return True
        if self.pm.is_write_allowed(target_path, self._project_root):
            return True
        return self._task_scope_grants_write(target_path)

    def _task_scope_grants_write(self, target_path: str) -> bool:
        if not self._task_scope:
            return False
        entries = self._task_scope.get("writable") or []
        if not entries:
            return False
        # Both candidates are caller-supplied for this call, so they agree in
        # practice; the task envelope wins because the grant was minted against
        # that frame. Neither falls back to the environment: a grant resolved
        # against the wrong root would authorize writes into a project nobody in
        # the request chain named.
        project_root = (
            self._task_scope.get("project_root") or self._project_root
        )
        raw = (target_path or "").strip().strip("'\"")
        expanded = os.path.expanduser(raw)
        target_abs = expanded if os.path.isabs(expanded) else (
            os.path.join(project_root, expanded) if project_root else expanded
        )
        target_norm = os.path.normpath(target_abs)
        root_norm = os.path.normpath(project_root) if project_root else ""
        rel = (
            target_norm[len(root_norm):].lstrip(os.sep)
            if root_norm and target_norm.startswith(root_norm)
            else raw
        )
        for entry in entries:
            try:
                # Glob grant (CLI task write_patterns, e.g. "*.toml"): match the
                # project-relative path and its basename, mirroring
                # WritePolicyManager.allowed_write_patterns semantics.
                pat = (entry.get("pattern") or "").strip()
                if pat:
                    if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat):
                        return True
                    continue
                ep = (entry.get("path") or "").strip()
                if not ep:
                    continue
                ep_abs = ep if os.path.isabs(ep) else (
                    os.path.join(project_root, ep) if project_root else ep
                )
                ep_norm = os.path.normpath(ep_abs)
                if entry.get("is_dir"):
                    if target_norm == ep_norm or target_norm.startswith(ep_norm + os.sep):
                        return True
                else:
                    if target_norm == ep_norm:
                        return True
            except Exception:
                continue
        return False

    def _task_scope_grants_command(self, cmd: str) -> bool:
        """Slice B: return True if a per-task ``shell_commands`` grant matches.

        Each grant is one of:
          • ``"<token>"`` — literal first-token allowlist (e.g.
            ``"pytest"`` grants any ``pytest …`` invocation).
          • ``"<tok> <tok> …"`` — multi-word token-prefix allowlist (e.g.
            ``"git commit"`` grants any ``git commit …`` invocation but
            not ``git push``).  Mirrors the base allowlist's multi-word form.
          • ``"re:<regex>"`` — regex against the full command line.

        Grants are additive over base policy and consulted only after
        the base policy has decided to deny.  They cannot bypass
        ``always_blocked`` (sudo/vi/etc.) or redirection blocking.
        """
        scope = self._task_scope or {}
        grants = scope.get("shell_commands") or []
        if not grants:
            return False

        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()
        first_token = tokens[0] if tokens else ""
        first_basename = os.path.basename(first_token) if first_token else ""

        for raw in grants:
            if not isinstance(raw, str) or not raw.strip():
                continue
            entry = raw.strip()
            if entry.startswith("re:"):
                pattern = entry[3:]
                if not pattern:
                    continue
                try:
                    if re.search(pattern, cmd):
                        return True
                except re.error:
                    continue
            else:
                # Split the grant into tokens.  A single token keeps the
                # original first-token / basename semantics; a multi-word
                # grant ("git commit") must match the command's leading
                # tokens in order, with basename tolerance on the first
                # token only (mirrors the base allowlist).
                try:
                    grant_tokens = shlex.split(entry)
                except ValueError:
                    grant_tokens = entry.split()
                if not grant_tokens:
                    continue
                if len(grant_tokens) > len(tokens):
                    continue
                head = grant_tokens[0]
                if head != first_token and head != first_basename:
                    continue
                if grant_tokens[1:] == tokens[1:len(grant_tokens)]:
                    return True
        return False


# Single source of truth for heredoc detection / body-stripping.
#
# A heredoc body is stdin *data*, not executable commands, so a command using
# one must be handed to a real shell (sh -c); the manual shell=False
# orchestrator in shell_server cannot feed a body to stdin and would pass
# "<<DELIM" plus every body line as literal argv.  Both shell_server's routing
# (_execute_pipeline) and validation (is_command_allowed) consult this module,
# so detection and stripping can no longer drift out of lockstep.
#
# The ``[^\n]*`` before the newline tolerates trailing content on the opener
# line — a pipe/redirect/arg after the delimiter (``cat <<EOF | grep h``,
# ``cat <<EOF > out``).  Without it those forms weren't recognized as heredocs,
# so they bypassed the sh -c route and their body lines were either rejected by
# the validator or passed as literal argv by the manual orchestrator.
_HEREDOC_OPENER_RE = re.compile(
    r"""<<-?\s*(?:'([^']+)'|"([^"]+)"|(\S+))[^\n]*\n""",
    re.MULTILINE,
)


def _has_heredoc(command: str) -> bool:
    return bool(_HEREDOC_OPENER_RE.search(command))


def _strip_heredoc_bodies(command: str) -> str:
    """Remove heredoc bodies so their content isn't mistaken for redirection.

    Handles:  cmd << DELIM ... DELIM
              cmd << 'DELIM' ... DELIM
              cmd << "DELIM" ... DELIM
              cmd <<- DELIM ... DELIM   (dash variant)
    """
    # Re-scan the mutated ``result`` each iteration rather than iterating
    # ``finditer(command)`` offsets.  finditer yields positions into the
    # ORIGINAL command, but each splice shrinks ``result`` — so from the
    # second heredoc onward ``m.end()`` is a stale offset into the wrong
    # string and that body is left unstripped (its body + closing delimiter
    # then reach the allowlist validator as bogus command lines, e.g. a bare
    # ``EOF`` that gets rejected as a disallowed command).  A running cursor
    # over ``result`` keeps open/close offsets consistent so every heredoc
    # body is removed.
    result = command
    search_from = 0
    while True:
        m = _HEREDOC_OPENER_RE.search(result, search_from)
        if not m:
            break
        delim = m.group(1) or m.group(2) or m.group(3)
        body_start = m.end()
        end_pattern = re.compile(r'^' + re.escape(delim) + r'\s*$', re.MULTILINE)
        end_match = end_pattern.search(result, body_start)
        if not end_match:
            break  # unterminated heredoc — leave the remainder intact
        result = result[:body_start] + result[end_match.end():]
        search_from = body_start
    return result


def _tokenize(cmd: str) -> List[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


# Matches a shell redirection operator, optionally with an attached target
# (``2>&1``, ``&>``, ``2>/dev/null``, ``>>log`` ...). Optional leading fd digit,
# an optional ``&``, one or two ``>``/``<``, then an optional inline target.
_REDIR_RE = re.compile(r"^(?:\d*&?[<>]{1,2}|&>{1,2})(?:&?\d+|.*)?$")


def _strip_redirections(tokens: List[str]) -> List[str]:
    """Remove shell redirection operators and their targets from a token list.

    ``cp src dst 2>&1`` tokenizes to ``['cp','src','dst','2>&1']``; without this
    the trailing ``2>&1`` is mistaken for the cp destination. Redirections are
    validated separately by ``_redirection``, so they are safe to drop here.
    A bare operator (``>``, ``2>``) also consumes the following token, which is
    its target file (``> out.log`` -> drop both ``>`` and ``out.log``).
    """
    out: List[str] = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        # Bare operator whose target is the next token.
        if re.fullmatch(r"\d*&?[<>]{1,2}|&>{1,2}", t):
            skip_next = True
            continue
        # Operator with an attached target (``2>&1``, ``2>/dev/null``, ``>>x``).
        if _REDIR_RE.match(t) and re.match(r"^\d*&?[<>]", t):
            continue
        out.append(t)
    return out


def _extract_target(cmd: str, pos: int) -> Tuple[str, int]:
    target = ""
    if pos < len(cmd) and cmd[pos] in "'\"":
        q = cmd[pos]; pos += 1
        while pos < len(cmd) and cmd[pos] != q:
            target += cmd[pos]; pos += 1
        if pos < len(cmd):
            pos += 1  # skip closing quote
    else:
        # Stop at whitespace, pipeline/list operators, and command-
        # substitution / subshell boundaries so a trailing ``)`` or
        # backtick from ``$(... >/dev/null)`` isn't swept into the target.
        while pos < len(cmd) and cmd[pos] not in ' \t\r\n;|&()`':
            target += cmd[pos]; pos += 1
    return target, pos


# Special device files that are never real filesystem writes.  Redirecting
# or writing to any of these (or a /dev/fd/N descriptor) is always allowed.
_SPECIAL_DEVICES = frozenset({
    '/dev/null', '/dev/zero', '/dev/full', '/dev/tty',
    '/dev/stdout', '/dev/stderr', '/dev/stdin',
    '/dev/random', '/dev/urandom',
})


def _is_special_device(path: str) -> bool:
    p = (path or "").strip().strip("'\"")
    return p in _SPECIAL_DEVICES or p.startswith('/dev/fd/')


_DESTRUCTIVE_SCRIPT_RE = re.compile(
    r"shutil\.\s*(?:copy|move|rmtree|copytree)"
    r"|os\.\s*(?:rename|remove|unlink|makedirs|mkdir|rmdir)"
    r"|(?:pathlib\.)?Path\s*\([^)]*\)\s*\.\s*(?:unlink|mkdir|rename|rmdir|replace)"
    r"|subprocess\.\s*(?:run|call|Popen)"
)


def _extract_write_target_paths(command: str) -> Tuple[List[str], bool]:
    """Find filesystem paths a Python one-liner writes to.

    Returns ``(paths, complete)``.  ``complete`` is True only when every
    write maps to a recognized path-bearing form — ``open(P, 'w'|'a'|'x')``
    or ``Path(P).write_text/bytes`` — with a literal path.  Any
    destructive/opaque write (shutil/os/subprocess or a non-literal path)
    sets ``complete`` False so the caller keeps the blanket block.
    """
    paths: List[str] = []
    for m in re.finditer(r"open\s*\(([^)]*)\)", command, re.DOTALL):
        args = m.group(1)
        mode_m = re.search(r"""(?:,\s*|mode\s*=\s*)(['"])([rwaxbt+]*)\1""", args)
        if not mode_m or not any(c in mode_m.group(2) for c in 'wax+'):
            continue
        path_m = re.match(r"""\s*[frbu]*(['"])((?:\\.|(?!\1).)*)\1""", args)
        if not path_m:
            return paths, False
        paths.append(path_m.group(2))
    for m in re.finditer(
        r"""(?:pathlib\.)?Path\s*\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*\)\s*\.\s*write_(?:text|bytes)""",
        command,
    ):
        paths.append(m.group(2))
    complete = _DESTRUCTIVE_SCRIPT_RE.search(command) is None
    return paths, complete
