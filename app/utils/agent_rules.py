"""
Permission rules of a packaged agent (design/agent-overlay.md, step 2).

Pure matching: given a definition's rules and one tool call, say whether the
rules refuse it and why.  Only deny lists refuse; nothing here grants.

  * shell      ``shell_deny`` globs against the whole command and against
               each segment of it (split on ``;`` ``&&`` ``||`` ``|``).
  * file read  ``read_deny`` globs against the absolute path.

The baseline is Ziya's own policy, not "allow all" and not "allow nothing":
a deny list subtracts from it, and an allow list adds to it
(app/services/agent_enforcement: ``shell_grants_for``, ``write_grants_for``).
An allow list never removes anything Ziya's policy permits, so
``shell_allow`` and ``write_allow`` are not consulted here.  ``mcp_allow``
is not consulted either: MCP servers are limited by the tool list only.
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import Any, List, Mapping, Optional

SHELL_TOOLS = frozenset({"run_shell_command", "job_start"})
# Tools that type one command line into a terminal; argument ``line``.
LINE_TOOLS = frozenset({"shadow_send"})
READ_TOOLS = frozenset({"file_read", "file_list", "pdf_outline", "pdf_read_pages", "pdf_search"})
WRITE_TOOLS = frozenset({"file_write"})

_SPLIT = re.compile(r"\|\||&&|[;|\n]")
_STARS = re.compile(r"\*{2,}")


def bare_tool_name(name: Any) -> str:
    n = str(name or "")
    return n[4:] if n.startswith("mcp_") else n


def glob_match(pattern: str, text: str) -> bool:
    """``*`` and ``**`` both cross ``/``, as in the source format."""
    return fnmatch.fnmatchcase(text, _STARS.sub("*", str(pattern)))


def shell_segments(command: str) -> List[str]:
    """The command with whitespace collapsed, then each of its segments.
    Segments are extra candidates only: a quoted ``|`` can add one, which
    can refuse more, never less."""
    whole = " ".join(str(command or "").split())
    out = [whole] if whole else []
    for part in _SPLIT.split(str(command or "")):
        seg = " ".join(part.split())
        if seg and seg not in out:
            out.append(seg)
    return out


def _paths(value: Any, project_root: Optional[str]) -> List[str]:
    p = os.path.expanduser(str(value))
    if not os.path.isabs(p):
        p = os.path.join(project_root or os.getcwd(), p)
    norm = os.path.normpath(p)
    out = [norm]
    try:
        real = os.path.realpath(norm)
    except OSError:
        real = norm
    if real != norm:
        out.append(real)
    return out


def _refused(agent: str, what: str, pattern: str) -> str:
    return (f"Refused by the rules of the agent {agent}: {what} (rule \"{pattern}\"). "
            f"This cannot be approved while the agent is on; do not retry it another way. "
            f"The user can turn the agent off to run it.")


def call_refusal(rules: Any, tool_name: Any, args: Any, *,
                 project_root: Optional[str], agent: str) -> Optional[str]:
    """The refusal text when ``rules`` forbid this call, else None."""
    tool = bare_tool_name(tool_name)
    a = args if isinstance(args, Mapping) else {}
    if tool in SHELL_TOOLS or tool in LINE_TOOLS:
        command = a.get("command") if tool in SHELL_TOOLS else a.get("line")
        if not isinstance(command, str):
            return None
        for seg in shell_segments(command):
            for pat in rules.shell_deny:
                if glob_match(pat, seg):
                    return _refused(agent, f"the command `{seg}` is denied", pat)
        return None
    if tool in READ_TOOLS:
        raw = a.get("path") or ("." if tool == "file_list" else None)
        if not isinstance(raw, str):
            return None
        for p in _paths(raw, project_root):
            for pat in rules.read_deny:
                if glob_match(pat, p) or glob_match(pat, p + "/"):
                    return _refused(agent, f"reading {p} is denied", pat)
        return None
    return None
