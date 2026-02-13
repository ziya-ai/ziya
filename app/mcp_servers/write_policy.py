"""
Shell-specific write policy checks.

Uses WritePolicyManager for path approval. Adds shell-specific
checks: in-place flags, redirection, destructive commands, interpreter heuristics.
"""

import os
import re
import shlex
import sys
from typing import Callable, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config.write_policy import WritePolicyManager


class ShellWriteChecker:
    def __init__(self, pm: WritePolicyManager):
        self.pm = pm

    @property
    def policy(self):
        return self.pm.policy

    def check(self, command: str, split_fn: Callable) -> Tuple[bool, str]:
        for _op, seg in split_fn(command):
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
        tok = _tokenize(cmd)
        if not tok or tok[0] not in self.policy.get('destructive_commands', []):
            return True, ""
        targets = [t for t in tok[1:] if not t.startswith('-')]
        if not targets:
            return False, f"Command '{tok[0]}' requires a target path."
        for t in targets:
            if not self.pm.is_write_allowed(t):
                return False, f"'{tok[0]} {t}' blocked — use git diffs for project file changes."
        return True, ""

    def _inplace_edit(self, cmd: str) -> Tuple[bool, str]:
        for prog, flags in self.policy.get('inplace_edit_flags', {}).items():
            s = cmd.strip()
            if not (s.startswith(prog + ' ') or s == prog):
                continue
            tok = _tokenize(cmd)
            for flag in flags:
                for t in tok[1:]:
                    if t == flag or t.startswith(flag):
                        return False, f"In-place editing with '{prog} {flag}' is not allowed. Use git diffs."
        return True, ""

    def _interpreter(self, cmd: str) -> Tuple[bool, str]:
        tok = _tokenize(cmd)
        if not tok or tok[0] not in self.policy.get('allowed_interpreters', []):
            return True, ""
        s = cmd.strip()
        for pat in self.policy.get('interpreter_safe_patterns', []):
            if re.match(pat, s):
                return self._redirection(cmd)
        for pat in self.policy.get('script_write_indicators', []):
            if re.search(pat, cmd):
                return False, f"Script appears to write files (matched: {pat}). Use git diffs."
        return True, ""

    def _redirection(self, command: str) -> Tuple[bool, str]:
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
                    while i < ln and command[i] == ' ':
                        i += 1
                    if i < ln:
                        target, end = _extract_target(command, i)
                        if target and not self.pm.is_write_allowed(target):
                            return False, f"Redirection to '{target}' blocked."
                        i = end
                continue
            i += 1
        return True, ""


def _tokenize(cmd: str) -> List[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _extract_target(cmd: str, pos: int) -> Tuple[str, int]:
    target = ""
    if pos < len(cmd) and cmd[pos] in "'\"":
        q = cmd[pos]; pos += 1
        while pos < len(cmd) and cmd[pos] != q:
            target += cmd[pos]; pos += 1
        if pos < len(cmd):
            pos += 1  # skip closing quote
    else:
        while pos < len(cmd) and cmd[pos] not in ' \t;|&':
            target += cmd[pos]; pos += 1
    return target, pos
